"""Async client for the Amap Web服务 REST API.

Two invariants worth repeating because both are easy to violate:

1. **Amap signals errors inside a 200 response.** ``_get`` branches on
   ``body["status"] == "1"``, never on the HTTP status code.
2. **The Web服务 key must never reach the browser.** POI search is therefore
   proxied through this client rather than called from the frontend.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.amap.errors import (
    OK_INFOCODE,
    AmapAuthError,
    AmapError,
    AmapParamError,
    AmapTransportError,
    raise_for_infocode,
)
from app.config import settings

logger = logging.getLogger("tourplan.amap")

Coord = tuple[float, float]

MAX_ORIGINS_PER_CALL = 100
# Total attempts, i.e. 1 initial try plus 2 retries.
MAX_ATTEMPTS = 3
# Base for exponential backoff. A module constant so tests can zero it out
# rather than sleeping for real seconds.
RETRY_BASE_DELAY = 0.5
# Hard ceiling on the best-effort photo lookup. A slow /place/detail must never stall
# adding a place, so this is deliberately short. A constant so tests can drive the
# timeout path without waiting for real.
PHOTO_TIMEOUT_S = 3.0


def fmt_coord(c: Coord) -> str:
    return f"{c[0]:.6f},{c[1]:.6f}"


class _TokenBucket:
    """Process-wide QPS ceiling. Deliberately far below the account cap."""

    def __init__(self, rate: float) -> None:
        self.rate = max(0.1, rate)
        self.capacity = max(1.0, self.rate)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        # The lock is held across the sleep on purpose: it serialises callers so
        # the global rate stays honest rather than letting a burst through.
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self.rate)


@dataclass(slots=True)
class DistanceResult:
    origin_index: int
    distance_m: int | None
    duration_s: int | None
    ok: bool
    infocode: str | None = None


@dataclass(slots=True)
class Poi:
    id: str
    name: str
    address: str
    lng: float
    lat: float
    city: str
    district: str
    # 首张实拍图 URL（extensions=all 才有）。仅在高德返回 http(s) 链接时填，缺省空串。
    photo: str = ""


@dataclass(slots=True)
class PoiPage:
    count: int
    pois: list[Poi]


def _origin_id_offset(raw_ids: list[str], n: int) -> int:
    """Detect whether Amap's ``origin_id`` is 0-based or 1-based.

    The docs are ambiguous and a partial response makes positional fallback
    wrong, so the base is inferred once per response. Confirmed against a live
    payload in M5; if it ever disagrees, this is the single place to fix.
    """
    nums = sorted(int(x) for x in raw_ids if x.isdigit())
    if not nums:
        return 0
    if nums[0] == 0:
        return 0
    if nums[0] == 1 and len(nums) == n:
        return 1
    return 0


def _parse_coord(value: Any) -> Coord | None:
    """Amap returns coordinates as "lng,lat" strings, or [] when absent."""
    if isinstance(value, str) and "," in value:
        try:
            lng, lat = value.split(",", 1)
            return (float(lng), float(lat))
        except ValueError:
            return None
    return None


class AmapWebClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = settings.amap_base_url.rstrip("/")
        self._key = settings.amap_web_key
        self._timeout = settings.amap_timeout_s
        self._bucket = _TokenBucket(settings.amap_qps_limit)
        self._sem = asyncio.Semaphore(max(1, settings.amap_qps_limit))
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self.calls_made = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def has_key(self) -> bool:
        return bool(self._key.strip())

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.has_key:
            raise AmapAuthError(
                "AMAP_WEB_KEY 未配置",
                hint=(
                    "请在 backend/.env 里填入 AMAP_WEB_KEY，值为高德「Web服务」类型的 Key"
                    "（不是「Web端(JS API)」的那个）"
                ),
            )

        query = {k: str(v) for k, v in params.items() if v is not None}
        query["key"] = self._key
        query["output"] = "json"
        url = f"{self._base_url}{path}"

        last_error: AmapError | None = None
        for attempt in range(MAX_ATTEMPTS):
            await self._bucket.acquire()
            try:
                async with self._sem:
                    resp = await self._client.get(url, params=query)
                self.calls_made += 1
            except httpx.HTTPError as exc:
                last_error = AmapTransportError(
                    f"网络请求失败：{exc}",
                    hint="检查本机网络能否访问 restapi.amap.com",
                )
            else:
                if resp.status_code != 200:
                    last_error = AmapTransportError(
                        f"高德返回 HTTP {resp.status_code}", hint="非 200 通常是网关或额度问题"
                    )
                else:
                    try:
                        body = resp.json()
                    except ValueError:
                        last_error = AmapTransportError(
                            "高德返回了非 JSON 内容",
                            hint="可能是代理或网关插入的页面",
                        )
                    else:
                        # THE envelope check. status is a string "1"/"0".
                        if str(body.get("status")) == "1":
                            return body
                        infocode = str(body.get("infocode", ""))
                        info = str(body.get("info", ""))
                        err: AmapError
                        if infocode:
                            try:
                                raise_for_infocode(infocode, info)
                            except AmapError as raised:  # noqa: PERF203 - re-raise or retry below
                                err = raised
                        else:
                            err = AmapParamError(f"高德返回失败但没有 infocode：{info}", info=info)
                        if not err.retryable:
                            raise err
                        last_error = err

            if attempt < MAX_ATTEMPTS - 1 and last_error is not None and last_error.retryable:
                delay = RETRY_BASE_DELAY * (2**attempt + random.uniform(0, 0.4))
                logger.warning(
                    "amap %s attempt %d failed (%s), retrying in %.1fs",
                    path,
                    attempt + 1,
                    last_error,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            break

        assert last_error is not None
        raise last_error

    async def distance(
        self, origins: list[Coord], destination: Coord, mode: int
    ) -> list[DistanceResult]:
        """Distance from every origin to one destination.

        ``mode``: 0 straight line, 1 driving, 3 walking (walking is invalid
        beyond 5km and yields 20800).

        Results are mapped back by the ``origin_id`` field the API returns, not
        by array position: Amap may return fewer entries than origins sent.
        """
        if not origins:
            return []
        if len(origins) > MAX_ORIGINS_PER_CALL:
            raise AmapParamError(
                f"单次最多 {MAX_ORIGINS_PER_CALL} 个起点，收到 {len(origins)}",
                hint="调用方应先分批",
            )

        body = await self._get(
            "/v3/distance",
            {
                "origins": "|".join(fmt_coord(c) for c in origins),
                "destination": fmt_coord(destination),
                "type": mode,
            },
        )

        raw = body.get("results") or []
        raw_ids = [str(item.get("origin_id", "")) for item in raw]
        offset = _origin_id_offset(raw_ids, len(origins))

        out: list[DistanceResult] = []
        for position, item in enumerate(raw):
            raw_id = raw_ids[position]
            if raw_id.isdigit():
                index = int(raw_id) - offset
                if not 0 <= index < len(origins):
                    index = position
            else:
                index = position

            distance = item.get("distance")
            duration = item.get("duration")
            # An unreachable pair comes back as distance: [] (not null) with the reason in
            # info/infocode. _is_int already rejects null, "", [] and non-numeric strings.
            item_ok = str(item.get("info", "")).upper() in ("OK", "") and _is_int(distance)
            out.append(
                DistanceResult(
                    origin_index=index,
                    distance_m=int(distance) if item_ok else None,
                    duration_s=int(duration) if _is_int(duration) else None,
                    ok=item_ok,
                    infocode=None if item_ok else str(item.get("infocode") or "20800"),
                )
            )
        return out

    async def regeo(self, lng: float, lat: float) -> dict:
        """逆地理编码：坐标 -> 结构化地址 + 最近的 POI 名（用于地图选点添加）。"""
        body = await self._get(
            "/v3/geocode/regeo",
            {"location": fmt_coord((lng, lat))},
        )
        regeo = (body.get("regeocodes") or [{}])[0]
        component = regeo.get("addressComponent") or {}
        district = "".join(
            component.get(k) or "" for k in ("province", "district", "street", "streetNumber")
        )
        nearest = (regeo.get("pois") or [{}])[0]
        return {
            "address": str(regeo.get("formatted_address") or district or "").strip(),
            "name": str(nearest.get("name") or "").strip(),
        }

    async def place_text(
        self,
        keyword: str | None = None,
        city: str | None = None,
        page: int = 1,
        page_size: int = 20,
        types: str | None = None,
        sort_rule: int | None = None,
    ) -> PoiPage:
        # keywords and types are alternative filters; at least one must be present.
        if not keyword and not types:
            raise AmapParamError("place_text 需要 keyword 或 types 之一")
        # extensions=all 才带 photos；place_text 一次请求顺带拿到，零额外配额。
        # sort_rule 实测只有 2（热度）会换序：关键字搜索没有中心点，1（距离）与不传
        # 返回的是同一份顺序，所以传 1 是白传——距离排序只能在拿到坐标之后自己算。
        body = await self._get(
            "/v3/place/text",
            {
                "keywords": keyword,
                "types": types,
                "city": city or None,
                "citylimit": "true" if city else None,
                "offset": page_size,
                "page": page,
                "extensions": "all",
                "sortrule": sort_rule,
            },
        )
        pois = [_to_poi(p) for p in (body.get("pois") or [])]
        pois = [p for p in pois if p is not None]
        count = body.get("count")
        return PoiPage(count=int(count) if _is_int(count) else len(pois), pois=pois)  # type: ignore[arg-type]

    async def inputtips(self, keyword: str, city: str | None = None) -> list[Poi]:
        body = await self._get(
            "/v3/assistant/inputtips",
            {"keywords": keyword, "city": city or None, "datatype": "poi"},
        )
        tips = [_to_tip(t) for t in (body.get("tips") or [])]
        return [t for t in tips if t is not None]

    async def photo_for_poi(self, poi_id: str) -> str:
        """POI 详情里的首张实拍图 URL；没有照片或字段异常时返回空串。

        inputtips（搜索框联想）不带照片，所以「搜索→加地点」路径在落库前用这个补一张，
        每个地点只花 1 次调用。"""
        body = await self._get(
            "/v3/place/detail",
            {"id": poi_id, "extensions": "all"},
        )
        return _first_photo_url(body.get("pois") or [])


async def fetch_photo_best_effort(poi_id: str) -> str:
    """POI id → 首张实拍图 URL。照片是锦上添花：任何失败（没配 Key、限流、超时、
    无照片）都返回空串，绝不阻塞或破坏加地点/收清单这个主操作。

    REST 与 WS 两条加地点路径共用这一个实现。"""
    try:
        return await asyncio.wait_for(
            get_amap_client().photo_for_poi(poi_id), timeout=PHOTO_TIMEOUT_S
        )
    except Exception:  # noqa: BLE001 - 照片拿不到就算了，原因不重要
        return ""


def _is_int(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return True
    return False


def _to_poi(raw: dict[str, Any]) -> Poi | None:
    coord = _parse_coord(raw.get("location"))
    if coord is None:
        return None
    address = raw.get("address")
    return Poi(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        address="" if isinstance(address, list) else str(address or ""),
        lng=coord[0],
        lat=coord[1],
        city=_as_str(raw.get("cityname")),
        district=_as_str(raw.get("adname")),
        photo=_first_photo_url([raw] if isinstance(raw, dict) else []),
    )


def _first_photo_url(nodes: list[Any]) -> str:
    """extensions=all 响应里 pois[*].photos[*].url 的第一个 http(s) 链接。

    高德偶尔给非 http 前缀或空 url 的占位项，一律跳过；解析容错——照片拿不到
    不应该影响地点本身入库。"""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        photos = node.get("photos")
        if not isinstance(photos, list):
            continue
        for photo in photos:
            url = photo.get("url") if isinstance(photo, dict) else None
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return _prefer_https(url)
    return ""


# 高德自有图床，同一个 showpic id 换协议即可取到（已实测两种都 200）。其他类别的
# photos[].url 可能指向第三方站点，不保证支持 https，所以只升这一个 host。
_HTTPS_CAPABLE_PHOTO_HOSTS = frozenset({"store.is.autonavi.com"})


def _prefer_https(url: str) -> str:
    """页面一旦走 https，http 缩略图会被浏览器当混合内容直接拦掉。"""
    if not url.startswith("http://"):
        return url
    rest = url[len("http://") :]
    host = rest.split("/", 1)[0]
    return f"https://{rest}" if host in _HTTPS_CAPABLE_PHOTO_HOSTS else url


def _to_tip(raw: dict[str, Any]) -> Poi | None:
    # inputtips returns [] for both location and address when the tip is a bare
    # keyword suggestion with no coordinates; those are not placeable.
    coord = _parse_coord(raw.get("location"))
    if coord is None:
        return None
    return Poi(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        address=_as_str(raw.get("address")),
        lng=coord[0],
        lat=coord[1],
        city=_as_str(raw.get("city")),
        district=_as_str(raw.get("district")),
    )


def _as_str(value: Any) -> str:
    # Amap uses [] for empty string fields in some responses.
    return "" if isinstance(value, list) else str(value or "")


_client: AmapWebClient | None = None


def get_amap_client() -> AmapWebClient:
    global _client
    if _client is None:
        _client = AmapWebClient()
    return _client


async def close_amap_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


__all__ = [
    "MAX_ORIGINS_PER_CALL",
    "OK_INFOCODE",
    "AmapWebClient",
    "Coord",
    "DistanceResult",
    "Poi",
    "PoiPage",
    "close_amap_client",
    "fmt_coord",
    "get_amap_client",
]
