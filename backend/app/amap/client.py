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

    async def place_text(
        self, keyword: str, city: str | None = None, page: int = 1, page_size: int = 20
    ) -> PoiPage:
        body = await self._get(
            "/v3/place/text",
            {
                "keywords": keyword,
                "city": city or None,
                "citylimit": "true" if city else None,
                "offset": page_size,
                "page": page,
                "extensions": "base",
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
    )


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
