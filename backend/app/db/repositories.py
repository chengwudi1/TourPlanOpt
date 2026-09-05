"""All SQL lives here.

Routes and (from M3) the WebSocket handlers both call these functions, so there is one
implementation of every mutation and two transports over it. Nothing in this module
knows about HTTP or sockets.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.db.database import Database
from app.models.domain import (
    DayOut,
    ParticipantOut,
    PlaceCreate,
    PlaceOut,
    Snapshot,
    TravelMode,
    TripOut,
)
from app.util.ids import new_id
from app.util.timefmt import now_iso


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


async def create_trip(
    db: Database,
    *,
    title: str = "",
    city: str = "",
    travel_mode: TravelMode = TravelMode.DRIVING,
) -> tuple[str, str]:
    """Creates the trip and its first day. Returns (trip_id, day_id)."""
    trip_id, day_id = new_id(), new_id()
    now = now_iso()

    def _create(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO trips (id, title, city, travel_mode, cost_model, day_start_min, seq,
                                  created_at)
               VALUES (?, ?, ?, ?, 'haversine', 540, 0, ?)""",
            (trip_id, title, city, str(travel_mode), now),
        )
        conn.execute(
            """INSERT INTO days (id, trip_id, day_index, date, title, rev)
               VALUES (?, ?, 0, NULL, ?, 1)""",
            (day_id, trip_id, title or "第 1 天"),
        )

    await db.run(_create)
    return trip_id, day_id


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
        return Snapshot(
            trip=TripOut.model_validate(dict(trip_row)),
            days=[DayOut.model_validate(dict(r)) for r in day_rows],
            places=[PlaceOut.model_validate(dict(r)) for r in place_rows],
            participants=[ParticipantOut.model_validate(dict(r)) for r in participant_rows],
        )

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
                                   lng, lat, duration_min, locked, status, note, added_by, rev,
                                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?, 1, ?, ?)""",
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


async def persist_schedule(
    db: Database, schedule: Sequence[tuple[str, int, int, int]]
) -> int:
    """Write (place_id, travel_min_before, arrive_min, start_min) rows after an
    optimize. Server-derived values, so this bypasses the client patch whitelist but
    still bumps rev so other clients accept the new rows."""

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
