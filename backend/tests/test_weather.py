"""日头天气：城市名 -> adcode -> 4 天预报，两级各自缓存 + 未命中预算。

这里守的几件事全是「看起来一样、其实不同」的那一类：

1. 天气接口只认 adcode。把城市中文名直接填进 city，高德回 status=1 / info=OK 而
   forecasts 为空——**空返回不等于没权限**（本项目就在这上面误判过一次），所以客户端
   必须先把名字换成 adcode，且「adcode 有效却拿到空 casts」要按失败记账。
2. 预报**按城市一发**换 4 天，绝不按天各发一次：同一座城市连打多次只花两发
   （一次换 adcode、一次换预报）。
3. 失败要降级不能报错：这个接口没有鉴权，公网上的任何人都能拿随机城市名来刷配额，
   所以未命中另有 60 秒预算，刷满之后不打上游、返回 reason=upstream。
4. 鉴权类失败（没配 Key）不写缓存——那是配置状态，改了 .env 重启必须立刻生效。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.amap import client as client_mod
from app.amap import errors as errors_mod
from app.amap.client import AmapWebClient, WeatherCast
from app.db.database import Database, set_db

ADCODE = "510100"
CASTS = [
    {
        "date": "2026-09-28",
        "week": "7",
        "dayweather": "阴",
        "nightweather": "阴",
        "daytemp": "29",
        "nighttemp": "21",
        "daywind": "北",
        "nightwind": "北",
        "daypower": "1-3",
        "nightpower": "1-3",
    },
    {
        "date": "2026-09-29",
        "week": "1",
        "dayweather": "阵雨",
        "nightweather": "中雨",
        "daytemp": "24",
        "nighttemp": "20",
        "daywind": "南",
        "nightwind": "南",
        "daypower": "3-5",
        "nightpower": "3-5",
    },
]


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "weather-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


@pytest.fixture(autouse=True)
def reset_miss_budget() -> Iterator[None]:
    """未命中预算是进程级全局，不清零会让上一条测试留下的记录把下一条打成降级。"""
    from app.api import routes_weather

    routes_weather._misses.clear()
    yield
    routes_weather._misses.clear()


class FakeAmap:
    """替掉两个上游方法，记下每一次调用。返回什么、抛什么由测试自己摆。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.district_calls: list[str] = []
        self.weather_calls: list[str] = []
        self.adcode = ADCODE
        self.casts: list[dict] = [dict(c) for c in CASTS]
        self.error: Exception | None = None
        self.weather_error: Exception | None = None
        monkeypatch.setattr(client_mod.AmapWebClient, "district_adcode", self._district)
        monkeypatch.setattr(client_mod.AmapWebClient, "weather_forecast", self._weather)

    async def _district(self, city: str) -> str:
        self.district_calls.append(city)
        if self.error is not None:
            raise self.error
        return self.adcode

    async def _weather(self, adcode: str) -> list[WeatherCast]:
        self.weather_calls.append(adcode)
        if self.weather_error is not None:
            raise self.weather_error
        return [
            WeatherCast(
                date=c["date"],
                week=c["week"],
                day_weather=c["dayweather"],
                night_weather=c["nightweather"],
                day_temp=int(c["daytemp"]),
                night_temp=int(c["nighttemp"]),
                day_wind=c["daywind"],
                day_power=c["daypower"],
            )
            for c in self.casts
        ]


@pytest.fixture()
def amap(monkeypatch: pytest.MonkeyPatch) -> FakeAmap:
    return FakeAmap(monkeypatch)


def _get(client: TestClient, city: str = "成都") -> dict:
    resp = client.get("/api/weather/forecast", params={"city": city})
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- 客户端解析：这两条挡住「问法不对但看着像成功」 --------------------------------------


def make_client(handler, calls: list[str]) -> AmapWebClient:
    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    return AmapWebClient(transport=httpx.MockTransport(wrapped))


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_district_adcode_takes_the_first_six_digit_entry(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "amap_web_key", "test-web-key")
    monkeypatch.setattr(settings, "amap_qps_limit", 10_000)
    calls: list[str] = []
    c = make_client(
        lambda r: httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "districts": [
                    {"adcode": "110000", "level": "province", "name": "北京市"},
                    {"adcode": "110100", "level": "city", "name": "北京城区"},
                ],
            },
        ),
        calls,
    )
    assert _run(c.district_adcode("北京")) == "110000"
    assert calls == ["/v3/config/district"]


def test_district_adcode_returns_empty_when_amap_matches_nothing(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "amap_web_key", "test-web-key")
    monkeypatch.setattr(settings, "amap_qps_limit", 10_000)
    c = make_client(
        lambda r: httpx.Response(200, json={"status": "1", "info": "OK", "districts": []}), []
    )
    assert _run(c.district_adcode("不存在的城")) == ""


def test_weather_forecast_parses_casts_and_survives_junk_rows(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "amap_web_key", "test-web-key")
    monkeypatch.setattr(settings, "amap_qps_limit", 10_000)
    body = {
        "status": "1",
        "info": "OK",
        "forecasts": [{"city": "成都市", "casts": [*CASTS, {"week": "2"}, "不是对象"]}],
    }
    c = make_client(lambda r: httpx.Response(200, json=body), [])
    casts = _run(c.weather_forecast(ADCODE))
    # 少了 date 的行与不是对象的行都得被丢掉，而不是把整条响应用一片 None 撑起来。
    assert [x.date for x in casts] == ["2026-09-28", "2026-09-29"]
    assert casts[1].day_temp == 24 and casts[1].night_temp == 20
    assert casts[0].day_weather == "阴" and casts[0].day_power == "1-3"


def test_weather_forecast_with_no_adcode_never_calls_amap(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "amap_web_key", "test-web-key")
    calls: list[str] = []
    c = make_client(lambda r: httpx.Response(200, json={"status": "1", "forecasts": []}), calls)
    assert _run(c.weather_forecast("")) == []
    assert calls == []


# -- 端点：缓存粒度、降级、预算 ----------------------------------------------------------


def test_two_calls_for_one_city_then_zero_upstream(client: TestClient, amap: FakeAmap):
    first = _get(client)
    assert first["adcode"] == ADCODE
    assert first["cached"] is False
    assert [c["date"] for c in first["casts"]] == ["2026-09-28", "2026-09-29"]
    assert first["casts"][1]["day_weather"] == "阵雨"
    assert first["reason"] == ""
    assert (amap.district_calls, amap.weather_calls) == (["成都"], [ADCODE])

    # 整条行程（5 天）共用这一发：前端只会按城市再问一次，命中缓存零配额。
    second = _get(client)
    assert second["cached"] is True
    assert [c["date"] for c in second["casts"]] == ["2026-09-28", "2026-09-29"]
    assert (amap.district_calls, amap.weather_calls) == (["成都"], [ADCODE])


def test_blank_city_costs_no_upstream_call(client: TestClient, amap: FakeAmap):
    payload = _get(client, city="   ")
    assert payload["casts"] == [] and payload["reason"] == "no_city"
    assert amap.district_calls == []


def test_unresolvable_city_is_negative_cached(client: TestClient, amap: FakeAmap):
    amap.adcode = ""
    first = _get(client, city="某某镇")
    assert first["adcode"] == "" and first["reason"] == "not_found"
    assert first["casts"] == []
    second = _get(client, city="某某镇")
    assert second["reason"] == "not_found"
    # 查无此城只问一次：每次开页都重问就是把配额送给一个错别字。
    assert amap.district_calls == ["某某镇"]
    assert amap.weather_calls == []


def test_empty_casts_for_a_valid_adcode_count_as_failure(client: TestClient, amap: FakeAmap):
    """这条就是那次误判的守门人：adcode 是对的，高德却回空——不能当成「今天没有天气」冻 6 小时。"""
    amap.casts = []
    first = _get(client)
    assert first["adcode"] == ADCODE
    assert first["casts"] == [] and first["reason"] == "upstream"
    assert first["cached"] is False
    second = _get(client)
    assert second["reason"] == "upstream"
    # ok=0 只顶 5 分钟，但同一分钟内不该重问。
    assert amap.weather_calls == [ADCODE]


def test_rate_limited_upstream_degrades_and_negative_caches(client: TestClient, amap: FakeAmap):
    amap.error = errors_mod.AmapRateLimitError("超出限额", infocode="10020")
    payload = _get(client)
    assert payload["casts"] == [] and payload["reason"] == "upstream"
    assert payload["adcode"] == ""
    _get(client)
    assert amap.district_calls == ["成都"]


def test_auth_failure_is_never_cached(client: TestClient, amap: FakeAmap):
    """没配 Key 是配置状态：写进缓存就等于「改了 .env 还得等一格过期」，那是自找的排查地狱。"""
    amap.error = errors_mod.AmapAuthError("AMAP_WEB_KEY 未配置")
    for _ in range(2):
        assert _get(client)["reason"] == "upstream"
    assert amap.district_calls == ["成都", "成都"]


def test_miss_budget_stops_the_twenty_first_city(client: TestClient, amap: FakeAmap):
    """未命中预算：这个接口没有鉴权，扫段随机城市名就能把本服务当免费高德代理用。

    adcode 一律摆成「查无此城」，这样每一发预算只对应一次行政区划调用——
    20 发放得下，第 21 发必须原地降级。
    """
    amap.adcode = ""
    for i in range(20):
        _get(client, city=f"城{i}")
    assert len(amap.district_calls) == 20
    over = _get(client, city="城外")
    assert over["reason"] == "upstream"
    assert amap.district_calls[-1] != "城外"


def test_forecast_cache_expires_but_adcode_cache_does_not(
    client: TestClient, amap: FakeAmap, tmp_path
):
    """预报 6 小时就过期，行政区划 30 天内不该再问——两档 TTL 各管各的。"""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    _get(client)
    assert (amap.district_calls, amap.weather_calls) == (["成都"], [ADCODE])

    conn = sqlite3.connect(str(tmp_path / "weather-test.db"))
    try:
        conn.execute(
            "UPDATE weather_cache SET fetched_at = ?",
            ((datetime.now(UTC) - timedelta(hours=7)).isoformat(timespec="seconds"),),
        )
        conn.commit()
        _get(client)
        assert amap.weather_calls == [ADCODE, ADCODE], "预报过期后必须重问"
        assert amap.district_calls == ["成都"], "adcode 那一格才 0 天，不该跟着重问"

        conn.execute(
            "UPDATE city_adcode_cache SET fetched_at = ?",
            ((datetime.now(UTC) - timedelta(days=31)).isoformat(timespec="seconds"),),
        )
        conn.commit()
    finally:
        conn.close()
    _get(client)
    assert amap.district_calls == ["成都", "成都"]


def test_payload_shape_is_the_frontend_contract(client: TestClient, amap: FakeAmap):
    """前端 types 手写镜像这份响应，字段改名等于把天气条悄悄弄坏：这里先钉死键名。"""
    payload = _get(client)
    assert set(payload) == {"city", "adcode", "casts", "cached", "reason"}
    assert set(payload["casts"][0]) == {
        "date",
        "week",
        "day_weather",
        "night_weather",
        "day_temp",
        "night_temp",
        "day_wind",
        "day_power",
    }
    assert json.dumps(payload, ensure_ascii=False)
