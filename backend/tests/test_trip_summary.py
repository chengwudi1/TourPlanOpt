"""`GET /api/trips/summary` -- the home dashboard's batched card feed.

Two layers on purpose. The aggregates (counts, cover-photo precedence, the
`MAX(updated_at)` fallback) live in SQL, so they are tested against the repository with
timestamps written by hand: `now_iso()` only has second resolution, so places added
within the same second carry identical `updated_at` -- without fixed literals a `MAX`
assertion can pass by accident. The HTTP layer tests what the frontend actually
depends on: the query-string contract, the JSON field names and their order, and that the
route is reachable at all (`/{trip_id}` below it would otherwise swallow "summary").
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes_trips import SUMMARY_MAX_IDS, parse_summary_ids
from app.db import repositories as repo
from app.db.database import Database, set_db
from app.models.domain import PlaceCreate, TravelMode
from tests.test_repositories import make_place, new_day

CARD_KEYS = [
    "id",
    "title",
    "city",
    "travel_mode",
    "day_count",
    "place_count",
    "companion_count",
    "cover_photo",
    "updated_at",
    "created_at",
]


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "summary-test.db")
    await database.init()
    return database


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "summary-http.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


def photo_place(name: str, url: str, lng: float = 121.47) -> PlaceCreate:
    return PlaceCreate(name=name, lng=lng, lat=31.23, photo_url=url)


# -- repository level: the aggregates ------------------------------------------------


async def test_summary_counts_days_places_and_participants(db: Database) -> None:
    trip_id, day0 = await repo.create_trip(
        db, title="上海三日游", city="上海", travel_mode=TravelMode.WALKING
    )
    day1 = await new_day(db, trip_id, 1)
    await repo.add_place(db, day0, make_place("外滩"))
    await repo.add_place(db, day0, make_place("豫园"))
    await repo.add_place(db, day1, make_place("迪士尼"))
    await repo.upsert_participant(db, trip_id, "c1", "小明")
    await repo.upsert_participant(db, trip_id, "c2", "小红")
    # 同一个人重连：participants 是 (trip_id, client_id) 主键的常驻名册，只能算一个。
    await repo.upsert_participant(db, trip_id, "c2", "小红改名")

    rows = await repo.get_trip_summaries(db, [trip_id])
    assert len(rows) == 1
    card = rows[0]
    assert card.id == trip_id
    assert card.title == "上海三日游"
    assert card.city == "上海"
    assert card.travel_mode == "walking"
    assert (card.day_count, card.place_count, card.companion_count) == (2, 3, 2)


async def test_cover_photo_skips_placeless_and_prefers_the_earlier_day(db: Database) -> None:
    """(day_index, sort_index) 决定封面，而不是"第一个有图的 sort_index"。

    第 1 天的 D 排在 sort_index 0，第 0 天的 C 排在 sort_index 2：只按 sort_index 排的
    实现会选中 D，这里必须选 C。
    """
    trip_id, day0 = await repo.create_trip(db, title="两日")
    day1 = await new_day(db, trip_id, 1)
    await repo.add_place(db, day0, make_place("无图A"))
    await repo.add_place(db, day0, make_place("无图B"))
    await repo.add_place(db, day0, photo_place("有图C", "https://a.amap.com/C.jpg"))
    await repo.add_place(db, day1, photo_place("有图D", "https://a.amap.com/D.jpg"))

    card = (await repo.get_trip_summaries(db, [trip_id]))[0]
    assert card.cover_photo == "https://a.amap.com/C.jpg"


async def test_cover_photo_is_empty_when_no_place_has_one(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db, title="全程无图")
    await repo.add_place(db, day_id, make_place("街角咖啡店"))

    card = (await repo.get_trip_summaries(db, [trip_id]))[0]
    assert card.cover_photo == ""


async def test_empty_trip_falls_back_to_created_at(db: Database) -> None:
    """新建还没加地点的行程：没有 MAX(places.updated_at) 可取，退回 trips.created_at。"""
    trip_id, _ = await repo.create_trip(db, title="刚建的", city="杭州")

    card = (await repo.get_trip_summaries(db, [trip_id]))[0]
    assert card.cover_photo == ""
    assert card.updated_at == card.created_at != ""
    # create_trip 连第一天一起建，所以空行程是 1 天 0 地点 0 同伴。
    assert (card.day_count, card.place_count, card.companion_count) == (1, 0, 0)


async def test_updated_at_is_the_newest_place_edit_not_the_last_row(db: Database) -> None:
    trip_id, day_id = await repo.create_trip(db, title="改过地点")
    first = await repo.add_place(db, day_id, make_place("外滩"))
    second = await repo.add_place(db, day_id, make_place("豫园"))
    # 后加的那条时间更早：取最后一行还是取 MAX，只有在这里能区分开。
    await db.execute(
        "UPDATE places SET updated_at = ? WHERE id = ?",
        ("2025-03-04T09:00:00+00:00", first.id),
    )
    await db.execute(
        "UPDATE places SET updated_at = ? WHERE id = ?",
        ("2024-01-02T03:04:05+00:00", second.id),
    )

    card = (await repo.get_trip_summaries(db, [trip_id]))[0]
    assert card.updated_at == "2025-03-04T09:00:00+00:00"


async def test_unknown_ids_are_omitted_and_order_follows_the_request(db: Database) -> None:
    """没有 404、没有 null 占位：前端按"没回来的就是没了"来清理本地记录。"""
    known_a, _ = await repo.create_trip(db, title="甲")
    known_b, _ = await repo.create_trip(db, title="乙")

    rows = await repo.get_trip_summaries(db, [known_b, "GHOST000", known_a])
    assert [r.id for r in rows] == [known_b, known_a]
    assert await repo.get_trip_summaries(db, ["GHOST000"]) == []
    assert await repo.get_trip_summaries(db, []) == []


# -- route level: the query-string contract ------------------------------------------


def test_parse_summary_ids_trims_dedupes_and_caps() -> None:
    assert parse_summary_ids(None) == []
    assert parse_summary_ids("") == []
    assert parse_summary_ids(" , ,, ") == []
    assert parse_summary_ids("a, b ,,a,c") == ["a", "b", "c"]

    capped = parse_summary_ids(",".join(f"id{i}" for i in range(40)))
    assert len(capped) == SUMMARY_MAX_IDS
    assert capped == [f"id{i}" for i in range(SUMMARY_MAX_IDS)]

    # 重复的 id 不占配额：40 段里只有 3 个不同的 id，就返回 3 个。
    assert parse_summary_ids(",".join(["a", "b", "c"] * 20)) == ["a", "b", "c"]


def test_summary_route_is_reachable_and_empty_is_not_an_error(client: TestClient) -> None:
    """`/summary` 必须排在 `/{trip_id}` 前面。顺序一旦回退，这个请求会变成一个
    "行程 summary 不存在" 的 404 —— 下面这条就是那次的哨兵。"""
    missing = client.get("/api/trips/summary")
    assert missing.status_code == 200, "被 /{trip_id} 吞掉了"
    assert missing.json() == {"trips": []}

    blank = client.get("/api/trips/summary", params={"ids": " , , "})
    assert blank.status_code == 200
    assert blank.json() == {"trips": []}


def _add_place(
    client: TestClient, trip_id: str, day_id: str, name: str, photo: str = ""
) -> dict:
    resp = client.post(
        f"/api/trips/{trip_id}/days/{day_id}/places",
        json={"name": name, "lng": 120.62, "lat": 31.32, "photo_url": photo},
    )
    assert resp.status_code == 201
    return resp.json()


def test_summary_card_over_http(client: TestClient) -> None:
    created = client.post(
        "/api/trips", json={"title": "苏州三日", "city": "苏州", "travel_mode": "walking"}
    ).json()
    trip_id, day_id = created["trip_id"], created["day_id"]
    _add_place(client, trip_id, day_id, "无图A")
    _add_place(client, trip_id, day_id, "有图B", "https://a.amap.com/B.jpg")
    _add_place(client, trip_id, day_id, "有图C", "https://a.amap.com/C.jpg")
    for client_id, name in (("c-1", "小明"), ("c-2", "小红")):
        assert client.post(
            f"/api/trips/{trip_id}/participants",
            json={"client_id": client_id, "name": name},
        ).status_code == 200

    # 时间戳由 now_iso() 现算，所以跟同一份快照比，不去断言字面值。（匿名调用，
    # 这条 GET 本身也不会写足迹。）
    snapshot = client.get(f"/api/trips/{trip_id}").json()

    resp = client.get("/api/trips/summary", params={"ids": trip_id})
    assert resp.status_code == 200
    trips = resp.json()["trips"]
    assert len(trips) == 1
    card = trips[0]

    # 字段名和顺序就是前端的契约，UI 直接按这些 key 取值。
    assert list(card.keys()) == CARD_KEYS
    assert card["id"] == trip_id
    assert card["title"] == "苏州三日"
    assert card["city"] == "苏州"
    assert card["travel_mode"] == "walking"
    assert card["day_count"] == 1
    assert card["place_count"] == 3
    assert card["companion_count"] == 2
    assert card["cover_photo"] == "https://a.amap.com/B.jpg"
    assert card["created_at"] == snapshot["trip"]["created_at"]
    assert card["updated_at"] == max(p["updated_at"] for p in snapshot["places"])


def test_summary_omits_unknown_ids_and_caps_at_24_over_http(client: TestClient) -> None:
    mine = client.post("/api/trips", json={"title": "我的"}).json()["trip_id"]
    mixed = client.get("/api/trips/summary", params={"ids": f"{mine},GHOST000"})
    assert [t["id"] for t in mixed.json()["trips"]] == [mine]

    deduped = client.get("/api/trips/summary", params={"ids": f" {mine} ,{mine},,"})
    assert [t["id"] for t in deduped.json()["trips"]] == [mine]

    ids = [
        client.post("/api/trips", json={"title": f"行程{i}"}).json()["trip_id"]
        for i in range(26)
    ]
    capped = client.get("/api/trips/summary", params={"ids": ",".join(ids[:25])}).json()["trips"]
    assert [t["id"] for t in capped] == ids[:SUMMARY_MAX_IDS]


# -- the reason this endpoint exists: it must not write ------------------------------


def _register(client: TestClient, name: str, password: str = "secret1") -> dict:
    resp = client.post("/api/auth/register", json={"name": name, "password": password})
    assert resp.status_code == 200
    return resp.json()["user"]


def _feed_row(client: TestClient, trip_id: str) -> dict:
    """`/api/auth/trips` is the repo's own read path over trip_visits (and is itself
    read-only), so the visit row can be asserted without a second SQL helper."""
    rows = client.get("/api/auth/trips").json()["trips"]
    return next((row for row in rows if row["id"] == trip_id), {})


def test_summary_does_not_record_a_visit_for_a_logged_in_user(client: TestClient) -> None:
    _register(client, "小明")
    trip_id = client.post("/api/trips", json={"title": "杭州"}).json()["trip_id"]

    # 基线：真的打开过一次，足迹已经落库。
    seq_before = client.get(f"/api/trips/{trip_id}").json()["trip"]["seq"]
    seen_before = _feed_row(client, trip_id)["last_seen"]
    assert seen_before

    # now_iso() 只到秒。不睡过一秒，"没变"可能只是两次写在同一秒里的巧合。
    time.sleep(1.1)

    assert client.get("/api/trips/summary", params={"ids": trip_id}).status_code == 200

    assert _feed_row(client, trip_id)["last_seen"] == seen_before, (
        "摘要接口写了 trip_visits：只把仪表盘显示出来就改写了「最近打开」排序"
    )
    assert client.get(f"/api/trips/{trip_id}").json()["trip"]["seq"] == seq_before


def test_summary_records_nothing_for_an_anonymous_call(client: TestClient) -> None:
    trip_id = client.post("/api/trips", json={"title": "匿名行程"}).json()["trip_id"]
    assert client.get("/api/trips/summary", params={"ids": trip_id}).status_code == 200

    _register(client, "小红")
    # 匿名调用既不能替这个新用户记足迹，也不能替他建行程归属。
    assert _feed_row(client, trip_id) == {}
