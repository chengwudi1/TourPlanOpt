"""Tests for the Amap client's error envelope handling.

These cover the #1 integration failure point without needing a real key: Amap
returns **HTTP 200 with the error inside the JSON body**, and the retry policy
must distinguish "retry this" from "retrying hides the real problem".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.amap import client as client_mod
from app.amap.client import AmapWebClient, DistanceResult, _first_photo_url, _prefer_https
from app.amap.errors import (
    AmapAuthError,
    AmapErrorKind,
    AmapQuotaError,
    AmapRateLimitError,
    AmapTransportError,
)
from app.config import settings

ORIGIN = (121.4737, 31.2304)
DEST = (121.5057, 31.2453)


@pytest.fixture(autouse=True)
def fast_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the client a key and remove every source of real waiting."""
    monkeypatch.setattr(settings, "amap_web_key", "test-web-key")
    monkeypatch.setattr(settings, "amap_qps_limit", 10_000)
    monkeypatch.setattr(client_mod, "RETRY_BASE_DELAY", 0.0)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], calls: list[str] | None = None
) -> AmapWebClient:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        return handler(request)

    return AmapWebClient(transport=httpx.MockTransport(wrapped))


def ok_body(**extra: Any) -> httpx.Response:
    payload = {"status": "1", "info": "OK", "infocode": "10000", **extra}
    return httpx.Response(200, json=payload)


def err_body(infocode: str, info: str = "ERROR") -> httpx.Response:
    # HTTP 200 on purpose: this is the trap.
    return httpx.Response(200, json={"status": "0", "info": info, "infocode": infocode})


async def test_success_returns_body() -> None:
    calls: list[str] = []
    client = make_client(lambda req: ok_body(count="1"), calls)
    body = await client._get("/v3/place/text", {"keywords": "外滩"})
    assert body["status"] == "1"
    assert calls == ["/v3/place/text"]


async def test_http_200_with_error_body_raises() -> None:
    """The envelope trap: a 200 response carrying status "0" must raise."""
    client = make_client(lambda req: err_body("10001", "INVALID_USER_KEY"))
    with pytest.raises(AmapAuthError) as excinfo:
        await client._get("/v3/distance", {})
    assert excinfo.value.infocode == "10001"


async def test_10009_hint_names_the_swapped_keys() -> None:
    """10009 is the exact symptom of putting the JS key into AMAP_WEB_KEY."""
    client = make_client(lambda req: err_body("10009", "USERKEY_PLAT_NOMATCH"))
    with pytest.raises(AmapAuthError) as excinfo:
        await client._get("/v3/distance", {})
    hint = excinfo.value.hint
    assert "AMAP_WEB_KEY" in hint
    assert "JS API" in hint


async def test_auth_error_is_not_retried() -> None:
    calls: list[str] = []
    client = make_client(lambda req: err_body("10001"), calls)
    with pytest.raises(AmapAuthError):
        await client._get("/v3/distance", {})
    assert len(calls) == 1, "retrying an invalid key burns quota and hides the problem"


async def test_daily_quota_error_is_not_retried() -> None:
    calls: list[str] = []
    client = make_client(lambda req: err_body("10003", "DAILY_QUERY_OVER_LIMIT"), calls)
    with pytest.raises(AmapQuotaError):
        await client._get("/v3/distance", {})
    assert len(calls) == 1


async def test_rate_limit_is_retried_then_succeeds() -> None:
    calls: list[str] = []
    attempts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return err_body("10004", "ACCESS_TOO_FREQUENT") if attempts["n"] < 3 else ok_body()

    client = make_client(handler, calls)
    body = await client._get("/v3/distance", {})
    assert body["status"] == "1"
    assert len(calls) == 3


async def test_transport_error_exhausts_retries() -> None:
    calls: list[str] = []
    client = make_client(lambda req: httpx.Response(503, text="busy"), calls)
    with pytest.raises(AmapTransportError):
        await client._get("/v3/distance", {})
    assert len(calls) == client_mod.MAX_ATTEMPTS


async def test_non_json_body_is_a_transport_error() -> None:
    client = make_client(lambda req: httpx.Response(200, text="<html>proxy page</html>"))
    with pytest.raises(AmapTransportError):
        await client._get("/v3/distance", {})


async def test_amap_busy_infocode_is_retried() -> None:
    calls: list[str] = []
    client = make_client(lambda req: err_body("10016", "SERVER_IS_BUSY"), calls)
    with pytest.raises(AmapTransportError):
        await client._get("/v3/distance", {})
    assert len(calls) == client_mod.MAX_ATTEMPTS


async def test_missing_key_raises_without_any_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "amap_web_key", "")
    calls: list[str] = []
    client = make_client(lambda req: ok_body(), calls)
    with pytest.raises(AmapAuthError) as excinfo:
        await client._get("/v3/distance", {})
    assert calls == []
    assert "AMAP_WEB_KEY" in excinfo.value.hint
    # kind drives the HTTP status: an absent key is our .env, not a bad gateway.
    assert excinfo.value.kind == AmapErrorKind.AUTH
    assert excinfo.value.retryable is False


async def test_distance_maps_results_by_origin_id() -> None:
    """Amap may return fewer results than origins, so position is not identity."""

    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(
            results=[
                {"origin_id": "0", "dest_id": "0", "distance": "1200", "duration": "300"},
                {"origin_id": "2", "dest_id": "0", "distance": "5400", "duration": "900"},
            ]
        )

    client = make_client(handler)
    results = await client.distance(
        [(121.0, 31.0), (121.1, 31.1), (121.2, 31.2)], DEST, mode=1
    )

    by_index = {r.origin_index: r for r in results}
    assert set(by_index) == {0, 2}, "origin 1 was absent and must not be invented"
    assert by_index[0].distance_m == 1200
    assert by_index[2].distance_m == 5400
    assert by_index[2].duration_s == 900
    assert all(r.ok for r in results)


async def test_distance_detects_one_based_origin_ids() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(
            results=[
                {"origin_id": "1", "dest_id": "1", "distance": "10", "duration": "1"},
                {"origin_id": "2", "dest_id": "1", "distance": "20", "duration": "2"},
            ]
        )

    client = make_client(handler)
    results = await client.distance([(121.0, 31.0), (121.1, 31.1)], DEST, mode=1)
    assert sorted(r.origin_index for r in results) == [0, 1]


async def test_distance_falls_back_to_position_when_no_origin_id() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(
            results=[
                {"distance": "10", "duration": "1"},
                {"distance": "20", "duration": "2"},
            ]
        )

    client = make_client(handler)
    results = await client.distance([(121.0, 31.0), (121.1, 31.1)], DEST, mode=1)
    assert [r.origin_index for r in results] == [0, 1]


async def test_distance_marks_unreachable_pair() -> None:
    """20800 (e.g. walking beyond 5km) must surface as ok=False, not as 0 metres."""

    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(
            results=[
                {
                    "origin_id": "0",
                    "dest_id": "0",
                    "distance": [],
                    "duration": [],
                    "info": "OUT_OF_SERVICE",
                    "infocode": "20800",
                }
            ]
        )

    client = make_client(handler)
    (result,) = await client.distance([ORIGIN], DEST, mode=3)
    assert isinstance(result, DistanceResult)
    assert result.ok is False
    assert result.distance_m is None
    assert result.infocode == "20800"


async def test_distance_rejects_oversized_batch() -> None:
    client = make_client(lambda req: ok_body(results=[]))
    too_many = [(121.0 + i * 1e-4, 31.0) for i in range(client_mod.MAX_ORIGINS_PER_CALL + 1)]
    with pytest.raises(Exception) as excinfo:
        await client.distance(too_many, DEST, mode=1)
    assert "分批" in str(excinfo.value.hint) or "分批" in str(excinfo.value)


async def test_inputtips_drops_tips_without_coordinates() -> None:
    """inputtips returns [] for location on bare keyword suggestions."""

    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(
            tips=[
                {
                    "id": "B1",
                    "name": "外滩",
                    "location": "121.4900,31.2400",
                    "address": "中山东一路",
                },
                {"id": "", "name": "外滩附近美食", "location": [], "address": []},
            ]
        )

    client = make_client(handler)
    tips = await client.inputtips("外滩")
    assert len(tips) == 1
    assert tips[0].name == "外滩"
    assert tips[0].lng == pytest.approx(121.49)


async def test_rate_limit_exhausts_retries_and_raises() -> None:
    """A sustained 10021 must surface as AmapRateLimitError, not as a bogus 0-metre leg."""
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10021"},
        )

    client = make_client(handler)
    with pytest.raises(AmapRateLimitError) as excinfo:
        await client.distance([ORIGIN], DEST, mode=1)
    assert calls == client_mod.MAX_ATTEMPTS
    assert excinfo.value.retryable is True


# -- M13 地点照片 --------------------------------------------------------------------
#
# The load-bearing property: a photo is decoration. Every failure mode below must
# degrade to "" rather than break "add a place".


async def test_place_text_requests_extensions_all_and_yields_photo() -> None:
    """photos only come back with extensions=all, so the existing search call carries
    them for free -- no extra request, no extra quota."""
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        return ok_body(
            pois=[
                {
                    "id": "B001",
                    "name": "外滩",
                    "address": "中山东一路",
                    "location": "121.4900,31.2400",
                    "cityname": "上海市",
                    "adname": "黄浦区",
                    # Amap's first entry is often a placeholder with url: [].
                    "photos": [{"title": [], "url": []}, {"title": "夜景", "url": "https://a.com/1.jpg"}],
                }
            ]
        )

    page = await make_client(handler).place_text("外滩", "上海")
    assert seen["extensions"] == "all"
    assert page.pois[0].photo == "https://a.com/1.jpg"


async def test_inputtips_carries_no_photo() -> None:
    """Which is exactly why adding from the search box backfills via /place/detail."""

    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(
            tips=[{"id": "B1", "name": "外滩", "location": "121.4900,31.2400", "address": "路"}]
        )

    tips = await make_client(handler).inputtips("外滩")
    assert tips[0].photo == ""


async def test_photo_for_poi_uses_the_detail_endpoint() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        return ok_body(pois=[{"id": "B001", "photos": [{"url": "https://a.com/2.jpg"}]}])

    url = await make_client(handler).photo_for_poi("B001")
    assert url == "https://a.com/2.jpg"
    assert seen == {"id": "B001", "extensions": "all", "key": "test-web-key", "output": "json"}


async def test_photo_for_poi_returns_empty_when_poi_has_no_photos() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(pois=[{"id": "B001", "name": "某个没图的地点"}])

    assert await make_client(handler).photo_for_poi("B001") == ""


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{}],
        [{"photos": []}],
        [{"photos": "不是列表"}],
        ["不是字典"],
        [{"photos": [{"url": "ftp://不合法/1.jpg"}]}],
        [{"photos": [None, {"url": None}, {"url": "   "}]}],
    ],
)
def test_first_photo_url_degrades_to_empty_on_any_missing_shape(nodes: list[Any]) -> None:
    assert _first_photo_url(nodes) == ""


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # 高德自有图床：同一个 showpic id 两种协议都实测 200，升级协议是免费的。
        # 页面一旦走 https，http 缩略图会被浏览器当混合内容直接拦掉。
        (
            "http://store.is.autonavi.com/showpic/abc123?type=pic",
            "https://store.is.autonavi.com/showpic/abc123?type=pic",
        ),
        ("https://store.is.autonavi.com/showpic/abc123", "https://store.is.autonavi.com/showpic/abc123"),
        # 其他类别的 photos[].url 可能指向第三方站点，不保证支持 https，不能乱升。
        ("http://img.third-party.com/a.jpg", "http://img.third-party.com/a.jpg"),
        ("http://store.is.autonavi.com.evil.example/a.jpg", "http://store.is.autonavi.com.evil.example/a.jpg"),
    ],
)
def test_prefer_https_upgrades_only_amap_own_image_host(url: str, expected: str) -> None:
    assert _prefer_https(url) == expected


def test_first_photo_url_upgrades_scheme() -> None:
    nodes = [{"photos": [{"url": "http://store.is.autonavi.com/showpic/x.jpg"}]}]
    assert _first_photo_url(nodes) == "https://store.is.autonavi.com/showpic/x.jpg"


async def test_fetch_photo_best_effort_swallows_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "amap_web_key", "")
    monkeypatch.setattr(client_mod, "get_amap_client", lambda: make_client(lambda req: ok_body()))
    assert await client_mod.fetch_photo_best_effort("B001") == ""


async def test_fetch_photo_best_effort_swallows_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(client_mod, "get_amap_client", lambda: make_client(handler))
    assert await client_mod.fetch_photo_best_effort("B001") == ""


async def test_fetch_photo_best_effort_swallows_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Slow:
        async def photo_for_poi(self, poi_id: str) -> str:
            await asyncio.sleep(0.2)
            return "https://a.com/late.jpg"

    monkeypatch.setattr(client_mod, "get_amap_client", lambda: _Slow())
    monkeypatch.setattr(client_mod, "PHOTO_TIMEOUT_S", 0.01)
    assert await client_mod.fetch_photo_best_effort("B001") == ""


async def test_fetch_photo_best_effort_returns_url_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return ok_body(pois=[{"id": "B001", "photos": [{"url": "https://a.com/3.jpg"}]}])

    monkeypatch.setattr(client_mod, "get_amap_client", lambda: make_client(handler))
    assert await client_mod.fetch_photo_best_effort("B001") == "https://a.com/3.jpg"
