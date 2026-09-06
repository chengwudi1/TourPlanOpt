"""Auth + history + city recommendation endpoints (M10)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database, set_db


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "auth-test.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


def _register(client: TestClient, name: str, password = "secret1") -> dict:
    resp = client.post("/api/auth/register", json={"name": name, "password": password})
    assert resp.status_code == 200
    return resp.json()["user"]


def test_register_login_logout_roundtrip(client: TestClient):
    user = _register(client, "小明")
    assert user["name"] == "小明"

    # me works via cookie
    me = client.get("/api/auth/me").json()["user"]
    assert me["name"] == "小明"

    # wrong password rejected, never leaking whether the name exists
    wrong = client.post("/api/auth/login", json={"name": "小明", "password": "nope123"})
    assert wrong.status_code == 401
    unknown = client.post("/api/auth/login", json={"name": "不存在的人", "password": "nope123"})
    assert unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]

    # logout clears the session
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None

    # login again works
    ok = client.post("/api/auth/login", json={"name": "小明", "password": "secret1"})
    assert ok.status_code == 200


def test_register_duplicate_name_conflict(client: TestClient):
    _register(client, "小红")
    dup = client.post("/api/auth/register", json={"name": "小红", "password": "secret1"})
    assert dup.status_code == 409


def test_password_validated(client: TestClient):
    short = client.post("/api/auth/register", json={"name": "阿短", "password": "123"})
    assert short.status_code == 400


def test_history_records_visits(client: TestClient):
    _register(client, "小明")
    trip = client.post("/api/trips", json={"title": "苏州三日", "city": "苏州"}).json()
    # opening the trip records a visit (cookie identifies the user)
    client.get(f"/api/trips/{trip['trip_id']}")

    feed = client.get("/api/auth/trips").json()["trips"]
    assert len(feed) == 1
    row = feed[0]
    assert row["title"] == "苏州三日" and row["city"] == "苏州"
    assert row["owned"] == 1
    assert row["place_count"] == 0
    assert row["last_seen"]

    # a second trip created WITHOUT a session is invisible to my feed
    client.post("/api/auth/logout")
    anon_trip = client.post("/api/trips", json={"title": "匿名行程"}).json()
    client.post("/api/auth/login", json={"name": "小明", "password": "secret1"})
    feed = client.get("/api/auth/trips").json()["trips"]
    assert [t["id"] for t in feed] == [trip["trip_id"]]
    assert anon_trip["trip_id"] not in {t["id"] for t in feed}


def test_recommendations_cached_and_shaped(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """The recommendation endpoint must serve real Amap-shaped POIs and serve the
    second request from cache (no extra API call)."""
    from app.amap import client as client_mod
    from app.amap.client import Poi, PoiPage

    calls: list[str] = []

    async def fake_place_text(self, keyword=None, city=None, page=1, page_size=20, types=None):
        calls.append(f"{city}:{types}:{keyword}")
        return PoiPage(
            count=1,
            pois=[Poi(id="B001", name="外滩", address="中山东一路", lng=121.49, lat=31.23,
                      city="上海市", district="黄浦区")],
        )

    monkeypatch.setattr(client_mod.AmapWebClient, "place_text", fake_place_text)

    first = client.get(
        "/api/city/recommendations", params={"city": "上海", "category": "scenic"}
    ).json()
    assert first["cached"] is False
    assert first["pois"][0]["name"] == "外滩"
    assert "uri.amap.com" in first["amap_url"]

    second = client.get(
        "/api/city/recommendations", params={"city": "上海", "category": "scenic"}
    ).json()
    assert second["cached"] is True
    assert len(calls) == 1  # the cache answered the second call

    # a different category is a different cache entry
    client.get("/api/city/recommendations", params={"city": "上海", "category": "food"})
    assert len(calls) == 2

    bad = client.get("/api/city/recommendations", params={"city": "上海", "category": "hotel"})
    assert bad.status_code == 400
