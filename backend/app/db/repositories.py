"""All SQL lives here.

Routes and (from M3) the WebSocket handlers both call these functions, so there is one
implementation of every mutation and two transports over it. Nothing in this module
knows about HTTP or sockets.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.db.database import Database
from app.models.domain import (
    DayOut,
    ParticipantOut,
    PlaceCreate,
    PlaceOut,
    Snapshot,
    StashItemOut,
    TravelMode,
    TripOut,
    TripSummary,
)
from app.util.ids import new_id
from app.util.timefmt import clamp_min, now_iso


@dataclass(frozen=True, slots=True)
class ReorderResult:
    ok: bool
    # The authoritative order either way: on failure this is what the caller sends back
    # so the client can converge instead of guessing.
    place_ids: list[str]


def _renumber(conn: sqlite3.Connection, day_id: str, ordered_ids: Sequence[str]) -> None:
    """Rewrite every sort_index for one day.

    SQLite checks the UNIQUE(day_id, sort_index) index row-by-row *during* an UPDATE, so
    the obvious `SET sort_index = sort_index + 1` transiently collides and dies. Parking
    every row on a distinct negative first makes the final assignment collision-free by
    construction: a negative can never equal a target index.
    """
    conn.execute("UPDATE places SET sort_index = -sort_index - 1 WHERE day_id = ?", (day_id,))
    conn.executemany(
        "UPDATE places SET sort_index = ? WHERE id = ? AND day_id = ?",
        [(index, place_id, day_id) for index, place_id in enumerate(ordered_ids)],
    )


def _ordered_ids(conn: sqlite3.Connection, day_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM places WHERE day_id = ? ORDER BY sort_index", (day_id,)
    ).fetchall()
    return [row["id"] for row in rows]


# -- trips -------------------------------------------------------------------------

# 创建表单上的天数是可选字段：可能有人填 99，也可能被脚本灌进一个负数。
MAX_TRIP_DAYS = 30


def clamp_trip_days(days: int) -> int:
    """Clamp instead of reject: building a trip must never fail on a bad count."""
    return max(1, min(int(days), MAX_TRIP_DAYS))


def day_dates(start_date: str | None, count: int) -> list[str | None]:
    """['2026-10-01', '2026-10-02', ...], or all-NULL when `start_date` is absent or junk.

    A date the user has only half-typed must not block creation, so an unparseable
    value degrades to "no dates" rather than a 422.
    """
    if not start_date:
        return [None] * count
    try:
        first = date.fromisoformat(start_date.strip())
    except ValueError:
        return [None] * count
    return [(first + timedelta(days=i)).isoformat() for i in range(count)]


async def create_trip(
    db: Database,
    *,
    title: str = "",
    city: str = "",
    travel_mode: TravelMode = TravelMode.DRIVING,
    created_by: str | None = None,
    days: int = 1,
    start_date: str | None = None,
    day_start_min: int | None = None,
) -> tuple[str, str]:
    """Creates the trip and its first `days` days. Returns (trip_id, first_day_id)."""
    count = clamp_trip_days(days)
    dates = day_dates(start_date, count)
    trip_id = new_id()
    day_ids = [new_id() for _ in range(count)]
    now = now_iso()
    start_min = 540 if day_start_min is None else clamp_min(day_start_min)

    def _create(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO trips (id, title, city, travel_mode, cost_model, day_start_min, seq,
                                  created_by, created_at)
               VALUES (?, ?, ?, ?, 'haversine', ?, 0, ?, ?)""",
            (trip_id, title, city, str(travel_mode), start_min, created_by, now),
        )
        # 天标题一律留空。这里原先写的是**行程**标题，于是天头读起来像行程名而不是
        # 「第 1 天」；DaySection 自己会在空标题时回落到 `第 N 天`。
        conn.executemany(
            """INSERT INTO days (id, trip_id, day_index, date, title, rev)
               VALUES (?, ?, ?, ?, '', 1)""",
            [(day_ids[i], trip_id, i, dates[i]) for i in range(count)],
        )

    await db.run(_create)
    return trip_id, day_ids[0]


async def get_snapshot(db: Database, trip_id: str) -> Snapshot | None:
    def _load(conn: sqlite3.Connection) -> Snapshot | None:
        trip_row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if trip_row is None:
            return None
        day_rows = conn.execute(
            "SELECT * FROM days WHERE trip_id = ? ORDER BY day_index", (trip_id,)
        ).fetchall()
        place_rows = conn.execute(
            """SELECT p.* FROM places p JOIN days d ON d.id = p.day_id
               WHERE p.trip_id = ? ORDER BY d.day_index, p.sort_index""",
            (trip_id,),
        ).fetchall()
        participant_rows = conn.execute(
            "SELECT * FROM participants WHERE trip_id = ? ORDER BY joined_at", (trip_id,)
        ).fetchall()
        stash_rows = conn.execute(
            "SELECT * FROM stash WHERE trip_id = ? ORDER BY created_at", (trip_id,)
        ).fetchall()
        return Snapshot(
            trip=TripOut.model_validate(dict(trip_row)),
            days=[DayOut.model_validate(dict(r)) for r in day_rows],
            places=[PlaceOut.model_validate(dict(r)) for r in place_rows],
            participants=[ParticipantOut.model_validate(dict(r)) for r in participant_rows],
            stash=[StashItemOut.model_validate(dict(r)) for r in stash_rows],
        )

    return await db.run(_load)


async def get_trip_summaries(db: Database, trip_ids: Sequence[str]) -> list[TripSummary]:
    """Card data for several trips at once, in the caller's id order.

    Two queries whatever the number of trips -- the correlated-subquery idiom
    `accounts.my_trips` already uses, batched with ``WHERE id IN (...)`` instead of a loop
    (N+1 on a dashboard of 24 cards is 24 snapshots' worth of rows for 9 fields).

    Performs no writes at all: no `record_visit`, no `trips.seq` bump. That is the whole
    point of this function existing next to `get_snapshot` rather than being replaced by
    it. Unknown ids simply produce no row.
    """
    if not trip_ids:
        return []
    placeholders = ", ".join("?" for _ in trip_ids)
    params = tuple(trip_ids)

    def _load(conn: sqlite3.Connection) -> list[TripSummary]:
        rows = conn.execute(
            f"""SELECT t.id, t.title, t.city, t.travel_mode, t.created_at,
                       (SELECT COUNT(*) FROM days d WHERE d.trip_id = t.id) AS day_count,
                       (SELECT COUNT(*) FROM places p WHERE p.trip_id = t.id) AS place_count,
                       (SELECT COUNT(DISTINCT pc.client_id) FROM participants pc
                          WHERE pc.trip_id = t.id) AS companion_count,
                       COALESCE(
                           (SELECT MAX(p2.updated_at) FROM places p2
                              WHERE p2.trip_id = t.id),
                           t.created_at) AS updated_at
                FROM trips t
                WHERE t.id IN ({placeholders})""",
            params,
        ).fetchall()

        # 封面：第一个真的带图的地点。按 (trip_id, day_index, sort_index) 排好后在
        # Python 里取每程首条命中，省掉窗口函数，也不必为每程单发一条查询。
        photo_rows = conn.execute(
            f"""SELECT p.trip_id, p.photo_url FROM places p
                JOIN days d ON d.id = p.day_id
                WHERE p.trip_id IN ({placeholders}) AND p.photo_url <> ''
                ORDER BY p.trip_id, d.day_index, p.sort_index""",
            params,
        ).fetchall()
        covers: dict[str, str] = {}
        for row in photo_rows:
            covers.setdefault(str(row["trip_id"]), str(row["photo_url"]))

        by_id: dict[str, TripSummary] = {}
        for row in rows:
            item = dict(row)
            item["cover_photo"] = covers.get(item["id"], "")
            by_id[item["id"]] = TripSummary.model_validate(item)
        return [by_id[trip_id] for trip_id in trip_ids if trip_id in by_id]

    return await db.run(_load)


async def next_seq(db: Database, trip_id: str) -> int:
    """Atomically bump and return trips.seq. Persisted so a restart does not rewind it
    and make clients think they have seen the future."""
    row = await db.run(
        lambda conn: conn.execute(
            "UPDATE trips SET seq = seq + 1 WHERE id = ? RETURNING seq", (trip_id,)
        ).fetchone()
    )
    return int(row["seq"]) if row is not None else 0


async def current_seq(db: Database, trip_id: str) -> int:
    """The seq value WITHOUT consuming the next one.

    welcome carries this: a snapshot is not a broadcast event, and if it consumed a seq,
    every other member would see a gap and needlessly resync. Everything that carries a
    seq is broadcast to all members, so per-client views never skip a value."""
    row = await db.fetch_one("SELECT seq FROM trips WHERE id = ?", (trip_id,))
    return int(row["seq"]) if row is not None else 0


# -- participants ------------------------------------------------------------------


async def upsert_participant(
    db: Database, trip_id: str, client_id: str, name: str, color: str = ""
) -> ParticipantOut:
    now = now_iso()

    def _upsert(conn: sqlite3.Connection) -> dict:
        conn.execute(
            """INSERT INTO participants (trip_id, client_id, name, color, joined_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(trip_id, client_id) DO UPDATE SET
                   name = excluded.name,
                   color = CASE WHEN excluded.color = '' THEN participants.color
                                ELSE excluded.color END,
                   last_seen = excluded.last_seen""",
            (trip_id, client_id, name, color, now, now),
        )
        row = conn.execute(
            "SELECT * FROM participants WHERE trip_id = ? AND client_id = ?",
            (trip_id, client_id),
        ).fetchone()
        return dict(row)

    return ParticipantOut.model_validate(await db.run(_upsert))


# -- places ------------------------------------------------------------------------


async def add_place(db: Database, day_id: str, payload: PlaceCreate) -> PlaceOut | None:
    """Insert a place, keeping sort_index dense. Returns None if the day does not exist."""
    place_id = new_id()
    now = now_iso()

    def _insert(conn: sqlite3.Connection) -> dict | None:
        day_row = conn.execute("SELECT trip_id FROM days WHERE id = ?", (day_id,)).fetchone()
        if day_row is None:
            return None

        ordered = _ordered_ids(conn, day_id)
        position = len(ordered)
        if payload.after_place_id and payload.after_place_id in ordered:
            position = ordered.index(payload.after_place_id) + 1
        ordered.insert(position, place_id)
        _renumber(conn, day_id, ordered)

        conn.execute(
            """INSERT INTO places (id, day_id, trip_id, sort_index, name, amap_poi_id, address,
                                   lng, lat, duration_min, locked, status, note, added_by,
                                   photo_url, rev, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?, ?, 1, ?, ?)""",
            (
                place_id,
                day_id,
                day_row["trip_id"],
                position,
                payload.name,
                payload.amap_poi_id,
                payload.address,
                payload.lng,
                payload.lat,
                payload.duration_min,
                payload.note,
                payload.added_by,
                payload.photo_url,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
        return dict(row)

    row = await db.run(_insert)
    return PlaceOut.model_validate(row) if row is not None else None


async def delete_place(db: Database, place_id: str) -> tuple[str, list[str]] | None:
    """Returns (day_id, remaining ordered ids), or None if the place did not exist."""

    def _delete(conn: sqlite3.Connection) -> tuple[str, list[str]] | None:
        row = conn.execute("SELECT day_id FROM places WHERE id = ?", (place_id,)).fetchone()
        if row is None:
            return None
        day_id = row["day_id"]
        conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
        ordered = _ordered_ids(conn, day_id)
        _renumber(conn, day_id, ordered)
        return day_id, ordered

    return await db.run(_delete)


async def reorder_day(db: Database, day_id: str, place_ids: Sequence[str]) -> ReorderResult:
    """Apply a full ordered id array.

    The array MUST be a permutation of the day's current ids. Rejecting anything else is
    what stops two concurrent drags from diverging permanently -- an index delta would
    not. On rejection the authoritative array comes back so the client can converge.
    """

    def _reorder(conn: sqlite3.Connection) -> ReorderResult:
        current = _ordered_ids(conn, day_id)
        if sorted(current) != sorted(place_ids) or len(current) != len(place_ids):
            return ReorderResult(ok=False, place_ids=current)
        _renumber(conn, day_id, list(place_ids))
        return ReorderResult(ok=True, place_ids=list(place_ids))

    return await db.run(_reorder)


async def place_ids_for_day(db: Database, day_id: str) -> list[str]:
    return await db.run(_ordered_ids, day_id)


async def get_place(db: Database, place_id: str) -> PlaceOut | None:
    row = await db.fetch_one("SELECT * FROM places WHERE id = ?", (place_id,))
    return PlaceOut.model_validate(row) if row is not None else None


async def get_db_places(db: Database, day_id: str) -> list[PlaceOut]:
    """A day's places in authoritative order."""
    rows = await db.run(
        lambda conn: conn.execute(
            "SELECT * FROM places WHERE day_id = ? ORDER BY sort_index", (day_id,)
        ).fetchall()
    )
    return [PlaceOut.model_validate(dict(r)) for r in rows]


async def move_place(
    db: Database, place_id: str, to_day_id: str
) -> tuple[PlaceOut, str, list[str], str, list[str]] | None:
    """把地点移动到另一天的末尾（跨天移动）。返回
    (place, old_day_id, old_order, new_day_id, new_order)；地点或目标天无效时 None。"""

    def _move(conn: sqlite3.Connection) -> tuple[dict, str, list[str], str, list[str]] | None:
        row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
        if row is None:
            return None
        old_day_id = row["day_id"]
        target = conn.execute("SELECT trip_id FROM days WHERE id = ?", (to_day_id,)).fetchone()
        if target is None or target["trip_id"] != row["trip_id"]:
            return None
        if old_day_id == to_day_id:
            return None  # 同天移动走 day_reorder，不在这里

        old_order = _ordered_ids(conn, old_day_id)
        if place_id in old_order:
            old_order.remove(place_id)
        _renumber(conn, old_day_id, old_order)

        new_order = _ordered_ids(conn, to_day_id)
        new_order.append(place_id)
        _renumber(conn, to_day_id, new_order)

        conn.execute(
            "UPDATE places SET day_id = ?, sort_index = ?, rev = rev + 1,"
            " updated_at = ? WHERE id = ?",
            (to_day_id, len(new_order) - 1, now_iso(), place_id),
        )
        place = dict(conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone())
        return place, old_day_id, old_order, to_day_id, new_order

    return await db.run(_move)


async def persist_schedule(
    db: Database, schedule: Sequence[tuple[str, int, int, int]]
) -> int:
    """Write (place_id, travel_min_before, arrive_min, start_min) rows after an
    optimize. Server-derived values, so this bypasses the client patch whitelist but
    still bumps rev so other clients accept the new rows. Never touches
    user_start_min -- that column is the user's intent, and overwriting it would pin
    the whole day to last run's clock times."""

    def _persist(conn: sqlite3.Connection) -> int:
        conn.executemany(
            """UPDATE places
               SET travel_min_before = ?, arrive_min = ?, start_min = ?,
                   rev = rev + 1, updated_at = ?
               WHERE id = ?""",
            [(t, a, s, now_iso(), place_id) for place_id, t, a, s in schedule],
        )
        return len(schedule)

    return await db.run(_persist)


# -- field-level patches ---------------------------------------------------------------
# The collaboration protocol sends patches, not whole rows: "只应用 patch 里出现的键".
# A missing key means "don't touch it"; an explicit null means "clear it". Both must
# survive the round trip, so the whitelist below is applied key-by-key against the
# payload dict, never by re-typing a fixed struct.


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)  # type: ignore[arg-type]


PLACE_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "name": str,
    "address": str,
    "duration_min": _coerce_int,
    "start_min": _coerce_int,
    "user_start_min": _coerce_int,
    "note": str,
    "locked": lambda v: 1 if v else 0,
    "status": str,
}

DAY_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "title": str,
    "date": lambda v: str(v) if v is not None else None,
    "start_min": _coerce_int,
    "travel_mode": lambda v: str(v) if v is not None else None,
    "start_place_id": lambda v: str(v) if v is not None else None,
    "end_place_id": lambda v: str(v) if v is not None else None,
}

TRIP_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "title": str,
    "city": str,
    "travel_mode": str,
    "cost_model": str,
    "day_start_min": _coerce_int,
}


def _patch_assignments(
    patch: dict[str, object], whitelist: dict[str, Callable[[object], object]]
) -> tuple[list[str], list[object]] | None:
    """Returns (column list, value list), or None if the patch is empty or has an
    unknown/invalid field. Unknown fields are rejected rather than ignored: silence
    would let a client believe a field was saved when it was dropped."""
    if not isinstance(patch, dict) or not patch:
        return None
    columns: list[str] = []
    values: list[object] = []
    for key, value in patch.items():
        coerce = whitelist.get(str(key))
        if coerce is None:
            return None
        try:
            columns.append(str(key))
            values.append(coerce(value))
        except (TypeError, ValueError):
            return None
    return columns, values


def _validate_patched_place(patch: dict[str, object]) -> str | None:
    """Domain checks the DB cannot express. Returns a rejection reason or None."""
    status = patch.get("status")
    if status is not None and str(status) not in ("confirmed", "pending"):
        return "bad_status"
    if "duration_min" in patch:
        duration = patch["duration_min"]
        if duration is not None and not (0 <= int(duration) <= 24 * 60):  # type: ignore[arg-type]
            return "bad_duration"
    return None


async def update_place(
    db: Database, place_id: str, patch: dict[str, object]
) -> PlaceOut | None:
    """Field-level patch of one place. rev+1 marks convergence, it rejects nothing."""
    reason = _validate_patched_place(patch)
    if reason is not None:
        return None

    def _update(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
        if row is None:
            return None
        assignments = _patch_assignments(patch, PLACE_PATCH_FIELDS)
        if assignments is None:
            return None
        columns, values = assignments
        sets = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE places SET {sets}, rev = rev + 1, updated_at = ? WHERE id = ?",
            [*values, now_iso(), place_id],
        )
        return dict(conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone())

    row = await db.run(_update)
    return PlaceOut.model_validate(row) if row is not None else None


async def get_day(db: Database, day_id: str) -> DayOut | None:
    row = await db.fetch_one("SELECT * FROM days WHERE id = ?", (day_id,))
    return DayOut.model_validate(row) if row is not None else None


async def create_day(
    db: Database, trip_id: str, title: str = "", date: str | None = None
) -> DayOut | None:
    """Appends a new day. Returns None if the trip does not exist."""

    def _create(conn: sqlite3.Connection) -> dict | None:
        trip_row = conn.execute("SELECT id FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if trip_row is None:
            return None
        row = conn.execute(
            "SELECT COALESCE(MAX(day_index), -1) AS max_index FROM days WHERE trip_id = ?",
            (trip_id,),
        ).fetchone()
        day_index = int(row["max_index"]) + 1
        day_id = new_id()
        conn.execute(
            "INSERT INTO days (id, trip_id, day_index, date, title, rev) VALUES (?, ?, ?, ?, ?, 1)",
            (day_id, trip_id, day_index, date, title or f"第 {day_index + 1} 天"),
        )
        return dict(conn.execute("SELECT * FROM days WHERE id = ?", (day_id,)).fetchone())

    row = await db.run(_create)
    return DayOut.model_validate(row) if row is not None else None


async def update_day(db: Database, day_id: str, patch: dict[str, object]) -> DayOut | None:
    def _update(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT * FROM days WHERE id = ?", (day_id,)).fetchone()
        if row is None:
            return None
        assignments = _patch_assignments(patch, DAY_PATCH_FIELDS)
        if assignments is None:
            return None
        columns, values = assignments
        sets = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(f"UPDATE days SET {sets}, rev = rev + 1 WHERE id = ?", [*values, day_id])
        return dict(conn.execute("SELECT * FROM days WHERE id = ?", (day_id,)).fetchone())

    row = await db.run(_update)
    return DayOut.model_validate(row) if row is not None else None


async def update_trip(db: Database, trip_id: str, patch: dict[str, object]) -> TripOut | None:
    def _update(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if row is None:
            return None
        assignments = _patch_assignments(patch, TRIP_PATCH_FIELDS)
        if assignments is None:
            return None
        columns, values = assignments
        sets = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(f"UPDATE trips SET {sets} WHERE id = ?", [*values, trip_id])
        return dict(conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone())

    row = await db.run(_update)
    return TripOut.model_validate(row) if row is not None else None


async def delete_day(db: Database, trip_id: str, day_id: str) -> bool:
    """Delete an EMPTY day. Returns False if the day does not exist or still has places
    -- days with content must go through place_delete first (no silent data loss)."""

    def _delete(conn: sqlite3.Connection) -> bool:
        day = conn.execute("SELECT id FROM days WHERE id = ?", (day_id,)).fetchone()
        if day is None:
            return False
        has_places = conn.execute(
            "SELECT 1 FROM places WHERE day_id = ? LIMIT 1", (day_id,)
        ).fetchone()
        if has_places:
            return False
        remaining = conn.execute(
            "SELECT id FROM days WHERE trip_id = ? AND id != ? ORDER BY day_index LIMIT 1",
            (trip_id, day_id),
        ).fetchone()
        if remaining is None:
            return False  # 最后一天不可删
        conn.execute("DELETE FROM days WHERE id = ?", (day_id,))
        return True

    return await db.run(_delete)


# -- stash（暂存区）--------------------------------------------------------------------


async def stash_add(
    db: Database,
    trip_id: str,
    *,
    name: str,
    lng: float,
    lat: float,
    address: str = "",
    amap_poi_id: str = "",
    added_by: str = "",
    photo_url: str = "",
) -> StashItemOut:
    item_id = new_id()

    def _insert(conn: sqlite3.Connection) -> dict:
        conn.execute(
            """INSERT INTO stash (id, trip_id, name, address, lng, lat, amap_poi_id,
                                  added_by, photo_url, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, trip_id, name, address, lng, lat,
                amap_poi_id, added_by, photo_url, now_iso(),
            ),
        )
        return dict(conn.execute("SELECT * FROM stash WHERE id = ?", (item_id,)).fetchone())

    return StashItemOut.model_validate(await db.run(_insert))


async def stash_remove(db: Database, trip_id: str, item_id: str) -> bool:
    def _remove(conn: sqlite3.Connection) -> bool:
        cur = conn.execute("DELETE FROM stash WHERE id = ? AND trip_id = ?", (item_id, trip_id))
        return cur.rowcount > 0

    return await db.run(_remove)
