"""`POST /api/trips` -- the create sheet's one and only round trip.

Two layers, same reason as `test_trip_summary.py`: the day/date arithmetic is repository
behaviour (it must clamp and degrade rather than reject), while what the frontend depends
on is the JSON contract -- `day_count` in particular has to be what the server *built*,
because the sheet sends a number it got from a stepper and cannot predict the clamp.

The day-title case is a regression guard, not a preference: `create_trip` used to write
the **trip** title into every day's title, which made the day header read like the trip
name instead of falling back to 「第 N 天」.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import repositories as repo
from app.db.database import Database, set_db
from app.models.domain import TravelMode


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "create-test.db")
    await database.init()
    return database


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    set_db(Database(tmp_path / "create-http.db"))
    from app.main import app

    with TestClient(app) as testclient:
        yield testclient
    set_db(Database(":memory:"))


async def rows(db: Database, trip_id: str) -> list[dict]:
    return await db.fetch_all(
        "SELECT day_index, date, title FROM days WHERE trip_id = ? ORDER BY day_index",
        (trip_id,),
    )


# -- repository level ---------------------------------------------------------------


async def test_create_trip_builds_every_requested_day_with_consecutive_dates(db) -> None:
    trip_id, first_day = await repo.create_trip(
        db, title="苏州三日游", city="苏州", days=3, start_date="2026-10-01"
    )
    assert [r["day_index"] for r in await rows(db, trip_id)] == [0, 1, 2]
    assert [r["date"] for r in await rows(db, trip_id)] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
    ]
    # The returned id is the first day, so the client can select it without a query.
    row0 = await db.fetch_one(
        "SELECT id FROM days WHERE trip_id = ? AND day_index = 0", (trip_id,)
    )
    assert row0["id"] == first_day


async def test_dates_roll_over_month_and_year_boundaries(db) -> None:
    trip_id, _ = await repo.create_trip(db, days=3, start_date="2026-12-31")
    assert [r["date"] for r in await rows(db, trip_id)] == [
        "2026-12-31",
        "2027-01-01",
        "2027-01-02",
    ]


async def test_day_titles_stay_empty_instead_of_copying_the_trip_title(db) -> None:
    """Regression: the trip title used to leak into every day's title."""
    trip_id, _ = await repo.create_trip(db, title="M15b 验证", days=2)
    assert [r["title"] for r in await rows(db, trip_id)] == ["", ""]


async def test_day_count_is_clamped_not_rejected(db) -> None:
    assert repo.clamp_trip_days(99) == repo.MAX_TRIP_DAYS
    assert repo.clamp_trip_days(0) == 1
    assert repo.clamp_trip_days(-5) == 1
    trip_id, _ = await repo.create_trip(db, days=99)
    assert len(await rows(db, trip_id)) == repo.MAX_TRIP_DAYS


async def test_unparseable_start_date_degrades_to_no_dates(db) -> None:
    """A half-typed date must not block creation -- and must not 422 either."""
    for junk in ("垃圾", "2026-13-45", "next friday", "2026/10/01"):
        trip_id, _ = await repo.create_trip(db, title=junk, days=2, start_date=junk)
        assert [r["date"] for r in await rows(db, trip_id)] == [None, None], junk


async def test_day_start_min_defaults_and_clamps(db) -> None:
    async def start_min(trip_id: str) -> int:
        row = await db.fetch_one("SELECT day_start_min FROM trips WHERE id = ?", (trip_id,))
        return int(row["day_start_min"])

    unset, _ = await repo.create_trip(db)
    assert await start_min(unset) == 540  # the schema default, 09:00

    early, _ = await repo.create_trip(db, day_start_min=8 * 60)
    assert await start_min(early) == 480

    absurd, _ = await repo.create_trip(db, day_start_min=99999)
    assert await start_min(absurd) == 24 * 60 - 1


async def test_travel_mode_and_city_still_land(db) -> None:
    trip_id, _ = await repo.create_trip(db, city="南京", travel_mode=TravelMode.WALKING)
    row = await db.fetch_one("SELECT city, travel_mode FROM trips WHERE id = ?", (trip_id,))
    assert (row["city"], row["travel_mode"]) == ("南京", "walking")


# -- HTTP level: the contract the sheet codes against --------------------------------


def test_post_trips_returns_day_count_and_share_url(client: TestClient) -> None:
    res = client.post(
        "/api/trips",
        json={"title": "苏州三日游", "city": "苏州", "days": 3, "start_date": "2026-10-01"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["day_count"] == 3
    assert body["share_url"].endswith(f"/trip/{body['trip_id']}")
    snap = client.get(f"/api/trips/{body['trip_id']}").json()
    assert [d["date"] for d in snap["days"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert [d["title"] for d in snap["days"]] == ["", "", ""]


def test_post_trips_reports_the_clamped_count(client: TestClient) -> None:
    """The sheet cannot predict the clamp, so the answer must carry the real number."""
    res = client.post("/api/trips", json={"days": 999})
    assert res.status_code == 201, res.text
    assert res.json()["day_count"] == repo.MAX_TRIP_DAYS


def test_post_trips_with_an_empty_body_still_builds_a_trip(client: TestClient) -> None:
    """「什么都不填，一次点击建完」是这一轮的核心验收，不是边缘情况。"""
    res = client.post("/api/trips", json={})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["day_count"] == 1
    snap = client.get(f"/api/trips/{body['trip_id']}").json()
    assert snap["trip"]["title"] == ""
    assert snap["trip"]["day_start_min"] == 540
    assert len(snap["days"]) == 1


def test_post_trips_applies_day_start_min(client: TestClient) -> None:
    res = client.post("/api/trips", json={"title": "早班", "day_start_min": 480})
    assert res.status_code == 201, res.text
    snap = client.get(f"/api/trips/{res.json()['trip_id']}").json()
    assert snap["trip"]["day_start_min"] == 480
