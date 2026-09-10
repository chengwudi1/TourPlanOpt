"""Column-addition migrations for an existing database.

schema.sql only covers *new* tables (`CREATE TABLE IF NOT EXISTS` is a no-op against a
table that already exists), so adding a column needs an explicit guarded ALTER. That
path is what runs against a real user's `backend/data/tourplan.db` on next boot, and it
is the one place a mistake destroys existing trips rather than just failing a test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.database import Database

# A pre-M10/pre-M13 database: trips has no created_by, places and stash have no
# photo_url. Column order and NOT NULLs mirror what those milestones actually shipped.
OLD_SCHEMA = """
CREATE TABLE trips (
    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
    travel_mode TEXT NOT NULL DEFAULT 'driving',
    cost_model TEXT NOT NULL DEFAULT 'haversine',
    day_start_min INTEGER NOT NULL DEFAULT 540,
    seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE days (
    id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    day_index INTEGER NOT NULL, date TEXT, title TEXT NOT NULL DEFAULT '',
    start_place_id TEXT, end_place_id TEXT, start_min INTEGER, travel_mode TEXT,
    rev INTEGER NOT NULL DEFAULT 1, UNIQUE(trip_id, day_index)
);
CREATE TABLE places (
    id TEXT PRIMARY KEY, day_id TEXT NOT NULL REFERENCES days(id) ON DELETE CASCADE,
    trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    sort_index INTEGER NOT NULL, name TEXT NOT NULL, amap_poi_id TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '', lng REAL NOT NULL, lat REAL NOT NULL,
    duration_min INTEGER NOT NULL DEFAULT 60, start_min INTEGER, arrive_min INTEGER,
    travel_min_before INTEGER, locked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL DEFAULT '', rev INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(day_id, sort_index)
);
CREATE TABLE stash (
    id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '', lng REAL NOT NULL,
    lat REAL NOT NULL, amap_poi_id TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
"""

ROWS = {
    "trip": ("T0", "上海三日游", "2026-01-01T00:00:00+00:00"),
    "day": ("D0", "T0", "第 1 天"),
    "place": ("P0", "D0", "T0", "外滩"),
    "stash": ("S0", "T0", "城隍庙"),
}


def build_old_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO trips (id, title, created_at) VALUES (?, ?, ?)",
            ROWS["trip"],
        )
        conn.execute(
            "INSERT INTO days (id, trip_id, day_index, title) VALUES (?, ?, 0, ?)",
            ROWS["day"],
        )
        conn.execute(
            """INSERT INTO places (id, day_id, trip_id, sort_index, name, lng, lat,
                                   created_at, updated_at)
               VALUES (?, ?, ?, 0, ?, 121.49, 31.24, 'x', 'x')""",
            ROWS["place"],
        )
        conn.execute(
            """INSERT INTO stash (id, trip_id, name, lng, lat, created_at)
               VALUES (?, ?, ?, 121.5, 31.2, 'x')""",
            ROWS["stash"],
        )
        conn.commit()
    finally:
        conn.close()


async def test_booting_an_old_database_adds_photo_url_without_losing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    build_old_db(path)

    db = Database(path)
    await db.init()

    place = await db.fetch_one("SELECT * FROM places WHERE id = ?", ("P0",))
    assert place is not None
    assert place["photo_url"] == ""  # NOT NULL DEFAULT '', never NULL
    assert place["name"] == "外滩"

    item = await db.fetch_one("SELECT * FROM stash WHERE id = ?", ("S0",))
    assert item is not None
    assert item["photo_url"] == ""
    assert item["name"] == "城隍庙"


async def test_migration_preserves_every_existing_trip(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    build_old_db(path)
    await Database(path).init()

    snapshot = await _snapshot_trip(path)
    assert snapshot["title"] == "上海三日游"
    assert snapshot["places"] == ["外滩"]
    assert snapshot["stash"] == ["城隍庙"]


async def test_init_is_idempotent_on_an_already_migrated_database(tmp_path: Path) -> None:
    """Startup runs init() every boot. A second pass must not try to ALTER a column that
    already exists, or the app would fail to start from its second run onward."""
    path = tmp_path / "old.db"
    build_old_db(path)

    db = Database(path)
    await db.init()
    await db.init()
    await db.init()

    place = await db.fetch_one("SELECT photo_url, name FROM places WHERE id = ?", ("P0",))
    assert place == {"photo_url": "", "name": "外滩"}


async def test_existing_photo_url_survives_repeated_boots(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    build_old_db(path)

    db = Database(path)
    await db.init()
    await db.execute("UPDATE places SET photo_url = ? WHERE id = ?", ("https://a/1.jpg", "P0"))
    await db.init()  # guarded ALTER must be a no-op, must not reset the value

    row = await db.fetch_one("SELECT photo_url FROM places WHERE id = ?", ("P0",))
    assert row["photo_url"] == "https://a/1.jpg"


# Written by the pre-_prefer_https code, so the app already has rows like these on disk.
LEGACY_URLS = [
    ("http://store.is.autonavi.com/showpic/abc?type=pic", "https://store.is.autonavi.com/showpic/abc?type=pic"),
    ("https://store.is.autonavi.com/showpic/ok", "https://store.is.autonavi.com/showpic/ok"),
    ("http://img.third-party.com/a.jpg", "http://img.third-party.com/a.jpg"),
    ("http://store.is.autonavi.com.evil.example/a.jpg", "http://store.is.autonavi.com.evil.example/a.jpg"),
    ("", ""),
]


async def test_legacy_http_photo_urls_are_healed_on_next_boot(tmp_path: Path) -> None:
    """A stored http:// thumbnail is silently blocked as mixed content the moment the page
    is https, so rows written before _prefer_https must be healed on boot."""
    path = tmp_path / "old.db"
    build_old_db(path)
    db = Database(path)
    await db.init()

    for i, (stored, _) in enumerate(LEGACY_URLS):
        await db.execute(
            "INSERT INTO places (id, day_id, trip_id, sort_index, name, lng, lat,"
            " created_at, updated_at, photo_url) VALUES (?, 'D0', 'T0', ?, ?, 121.4, 31.2,"
            " 'x', 'x', ?)",
            (f"PX{i}", i + 1, f"地点{i}", stored),
        )
        await db.execute(
            "INSERT INTO stash (id, trip_id, name, lng, lat, created_at, photo_url)"
            " VALUES (?, 'T0', ?, 121.4, 31.2, 'x', ?)",
            (f"SX{i}", f"想去{i}", stored),
        )
    await db.init()

    expected = [exp for _, exp in LEGACY_URLS]
    places = await db.fetch_all(
        "SELECT photo_url FROM places WHERE id LIKE 'PX%' ORDER BY sort_index"
    )
    assert [r["photo_url"] for r in places] == expected
    stash = await db.fetch_all("SELECT photo_url FROM stash WHERE id LIKE 'SX%' ORDER BY id")
    assert [r["photo_url"] for r in stash] == expected
    # The rows that predate the feature stay the empty string, not NULL.
    assert (await db.fetch_one("SELECT photo_url FROM places WHERE id = 'P0'"))["photo_url"] == ""
    assert (await db.fetch_one("SELECT photo_url FROM stash WHERE id = 'S0'"))["photo_url"] == ""


async def test_user_start_min_is_backfilled_only_for_anchors(tmp_path: Path) -> None:
    """M14 splits the hand-set time (user_start_min) from the derived one (start_min).

    Old rows have both mixed in start_min and no way to tell them apart, so only the
    pinned ones (locked=1, which is what a hand-set time set) become anchors. Backfilling
    every row would freeze each already-optimized day on its last clock times forever.
    """
    path = tmp_path / "old.db"
    build_old_db(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE places SET locked = 1, start_min = 1140 WHERE id = 'P0'")
    conn.execute(
        "INSERT INTO places (id, day_id, trip_id, sort_index, name, lng, lat, start_min,"
        " locked, created_at, updated_at) VALUES ('PD', 'D0', 'T0', 1, '推导值', 121.4, 31.2,"
        " 600, 0, 'x', 'x')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    await db.init()

    pinned = await db.fetch_one("SELECT start_min, user_start_min FROM places WHERE id = 'P0'")
    assert pinned == {"start_min": 1140, "user_start_min": 1140}
    derived = await db.fetch_one("SELECT start_min, user_start_min FROM places WHERE id = 'PD'")
    assert derived == {"start_min": 600, "user_start_min": None}


async def test_trips_created_by_column_is_added_too(tmp_path: Path) -> None:
    """The M10 column shares this migration path, so pin it alongside M13."""
    path = tmp_path / "old.db"
    build_old_db(path)
    await Database(path).init()

    trip = await (Database(path)).fetch_one("SELECT created_by FROM trips WHERE id = ?", ("T0",))
    assert "created_by" in trip
    assert trip["created_by"] is None


async def _snapshot_trip(path: Path) -> dict:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        trip = dict(conn.execute("SELECT * FROM trips WHERE id = 'T0'").fetchone())
        return {
            "title": trip["title"],
            "places": [r["name"] for r in conn.execute("SELECT name FROM places")],
            "stash": [r["name"] for r in conn.execute("SELECT name FROM stash")],
        }
    finally:
        conn.close()
