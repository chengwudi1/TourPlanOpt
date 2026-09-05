"""Tests for the Amap client's error envelope handling.

These cover the #1 integration failure point without needing a real key: Amap
returns **HTTP 200 with the error inside the JSON body**, and the retry policy
must distinguish "retry this" from "retrying hides the real problem".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.amap import client as client_mod
from app.amap.client import AmapWebClient, DistanceResult
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
