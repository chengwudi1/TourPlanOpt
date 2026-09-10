"""M21 发现面板三档排序：热度真打上游、距离零配额复用综合那份缓存。

这里守的三件事都是会静默退化的：
1. 热度必须把 sortrule=2 传到上游，且与综合各占一条缓存（否则切档永远看到同一批）。
2. 距离**不许**再花一次配额：它读的是 composite 那份，只是重排 + 回填 distance_m。
3. origin 脏了要降级成「没给」，不能 422 把整个面板打空——建行程时垃圾日期也是这个规矩。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database, set_db

# 三个候选离 origin (120.0, 36.0) 的远近是刻意打乱顺序放的：远、近、中。
POIS = [
    ("B003", "栈桥", 120.30, 36.10),
    ("B001", "崂山", 120.80, 36.20),
    ("B002", "信号山", 120.05, 36.05),
]


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "city-sort-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


@pytest.fixture()
def amap_calls(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    """替掉 place_text，记录每一次请求带的 sortrule（None = 综合，不传给高德）。"""
    from app.amap import client as client_mod
    from app.amap.client import Poi, PoiPage

    seen: list[int | None] = []

    async def fake_place_text(
        self, keyword=None, city=None, page=1, page_size=20, types=None, sort_rule=None
    ):
        seen.append(sort_rule)
        return PoiPage(
            count=len(POIS),
            pois=[
                Poi(id=pid, name=name, address="某路", lng=lng, lat=lat,
                    city="青岛市", district="市南区")
                for pid, name, lng, lat in POIS
            ],
        )

    monkeypatch.setattr(client_mod.AmapWebClient, "place_text", fake_place_text)
    return seen


def _names(payload: dict) -> list[str]:
    return [p["name"] for p in payload["pois"]]


def test_hot_sort_reaches_amap_and_has_its_own_cache_entry(client: TestClient, amap_calls):
    first = client.get(
        "/api/city/recommendations", params={"city": "青岛", "category": "scenic", "sort": "hot"}
    ).json()
    assert amap_calls == [2], "热度必须把 sortrule=2 传到上游"
    assert first["cached"] is False and first["sort"] == "hot"

    # 再点一次热度：命中自己那条缓存，不再烧配额
    again = client.get(
        "/api/city/recommendations", params={"city": "青岛", "category": "scenic", "sort": "hot"}
    ).json()
    assert again["cached"] is True and amap_calls == [2]

    # 切回综合是另一条缓存，不是把热度那份当综合端出来
    comp = client.get("/api/city/recommendations", params={"city": "青岛"}).json()
    assert amap_calls == [2, None], "综合不该带 sortrule"
    assert comp["cached"] is False and comp["sort"] == "composite"


def test_distance_sort_costs_zero_quota_and_sorts_by_meters(client: TestClient, amap_calls):
    warm = client.get("/api/city/recommendations", params={"city": "青岛"}).json()
    assert _names(warm) == ["栈桥", "崂山", "信号山"], "综合就是上游原样"
    assert amap_calls == [None]

    near = client.get(
        "/api/city/recommendations",
        params={"city": "青岛", "sort": "distance", "origin": "120.0,36.0"},
    ).json()
    assert amap_calls == [None], "距离档一次配额都不许花"
    assert near["cached"] is True, "读的就是 composite 那条缓存"
    assert _names(near) == ["信号山", "栈桥", "崂山"]
    dists = [p["distance_m"] for p in near["pois"]]
    assert dists == sorted(dists) and all(isinstance(d, int) for d in dists)
    assert near["origin"] == [120.0, 36.0]


def test_composite_and_hot_keep_distance_m_absent(client: TestClient, amap_calls):
    for payload in (
        client.get("/api/city/recommendations", params={"city": "青岛"}).json(),
        client.get("/api/city/recommendations", params={"city": "青岛", "sort": "hot"}).json(),
    ):
        assert all(p["distance_m"] is None for p in payload["pois"])


def test_distance_without_a_usable_origin_degrades_to_composite(
    client: TestClient, amap_calls
):
    client.get("/api/city/recommendations", params={"city": "青岛"})
    for bad in (None, "abc", "120.0", "120.0,36.0,7", "0,0", "200.0,36.0"):
        params = {"city": "青岛", "sort": "distance"}
        if bad is not None:
            params["origin"] = bad
        resp = client.get("/api/city/recommendations", params=params)
        assert resp.status_code == 200, f"origin={bad!r} 不该把面板打空"
        body = resp.json()
        assert body["origin"] is None
        assert _names(body) == ["栈桥", "崂山", "信号山"], "读不懂就当没排序"
        assert all(p["distance_m"] is None for p in body["pois"])
    assert amap_calls == [None]


def test_unknown_sort_is_rejected(client: TestClient, amap_calls):
    resp = client.get("/api/city/recommendations", params={"city": "青岛", "sort": "nearest"})
    assert resp.status_code == 400
    assert amap_calls == [], "参数校验必须发生在花配额之前"
