"""All SQL lives here.

Routes and (from M3) the WebSocket handlers both call these functions, so there is one
implementation of every mutation and two transports over it. Nothing in this module
knows about HTTP or sockets.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.db.database import Database
from app.models.domain import (
    EXPENSE_CATEGORIES,
    ChecklistItemOut,
    CostModel,
    DayOut,
    ExpenseCreate,
    ExpenseOut,
    MessageIn,
    MessageOut,
    ParticipantOut,
    PlaceCreate,
    PlaceOut,
    Snapshot,
    StashItemOut,
    TravelMode,
    TripOut,
    TripStatus,
    TripSummary,
)
from app.uploads import drop_cover_file
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
        checklist_rows = conn.execute(
            "SELECT * FROM checklist_items WHERE trip_id = ? ORDER BY sort_index", (trip_id,)
        ).fetchall()
        expense_rows = conn.execute(
            "SELECT * FROM expenses WHERE trip_id = ? ORDER BY created_at", (trip_id,)
        ).fetchall()
        # 聊天按窗口取：内层倒着捞最近 60 条未删的，外层再正过来——客户端要的是时间正序。
        message_rows = conn.execute(
            """SELECT * FROM (
                   SELECT rowid AS pos, * FROM messages
                    WHERE trip_id = ? AND deleted_at IS NULL
                    ORDER BY rowid DESC LIMIT ?
               ) ORDER BY pos""",
            (trip_id, MESSAGE_WINDOW),
        ).fetchall()
        return Snapshot(
            trip=TripOut.model_validate(dict(trip_row)),
            days=[DayOut.model_validate(dict(r)) for r in day_rows],
            places=[PlaceOut.model_validate(dict(r)) for r in place_rows],
            participants=[ParticipantOut.model_validate(dict(r)) for r in participant_rows],
            stash=[StashItemOut.model_validate(dict(r)) for r in stash_rows],
            checklist=[ChecklistItemOut.model_validate(dict(r)) for r in checklist_rows],
            expenses=[ExpenseOut.model_validate(dict(r)) for r in expense_rows],
            messages=[MessageOut.model_validate(dict(r)) for r in message_rows],
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

    M22 给卡片添的那些字段（清单进度、已花、预算、日期区间、状态）都是并排塞进同一条语句
    的相关子查询——查询条数不因卡片多画两行而增长。
    """
    if not trip_ids:
        return []
    placeholders = ", ".join("?" for _ in trip_ids)
    params = tuple(trip_ids)

    def _load(conn: sqlite3.Connection) -> list[TripSummary]:
        rows = conn.execute(
            f"""SELECT t.id, t.title, t.city, t.cover_url, t.travel_mode, t.status,
                       t.budget_cents, t.created_at,
                       (SELECT COUNT(*) FROM days d WHERE d.trip_id = t.id) AS day_count,
                       (SELECT COUNT(*) FROM places p WHERE p.trip_id = t.id) AS place_count,
                       (SELECT COUNT(DISTINCT pc.client_id) FROM participants pc
                          WHERE pc.trip_id = t.id) AS companion_count,
                       (SELECT MIN(d2.date) FROM days d2
                          WHERE d2.trip_id = t.id AND d2.date IS NOT NULL
                            AND d2.date != '') AS start_date,
                       (SELECT MAX(d3.date) FROM days d3
                          WHERE d3.trip_id = t.id AND d3.date IS NOT NULL
                            AND d3.date != '') AS end_date,
                       (SELECT COUNT(*) FROM checklist_items c WHERE c.trip_id = t.id)
                           AS checklist_total,
                       (SELECT COUNT(*) FROM checklist_items c
                          WHERE c.trip_id = t.id AND c.done = 1) AS checklist_done,
                       (SELECT COALESCE(SUM(e.amount_cents), 0) FROM expenses e
                          WHERE e.trip_id = t.id) AS spent_cents,
                       COALESCE(
                           (SELECT MAX(p2.updated_at) FROM places p2
                              WHERE p2.trip_id = t.id),
                           t.created_at) AS updated_at
                FROM trips t
                WHERE t.id IN ({placeholders})""",
            params,
        ).fetchall()

        by_id: dict[str, TripSummary] = {}
        for row in rows:
            item = dict(row)
            # 只带用户亲手那一张（决策 3 的 M37 修订）：没传图时首页显示的内置默认封面由前端按
            # `trip_id` 现算——那份清单是打包在前端里的静态资产，后端要跟着挑就得抄一份规则，
            # 而两处规则一旦分叉，首页与行程页就会各显示一张，看着像两个不同的行程。
            item["cover_photo"] = str(item.pop("cover_url", "") or "")
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
        if payload.position is not None:
            # 绝对下标优先：撤销删除要回到原位，而排在第一位的地点没有 after_place_id 可指。
            # 越界只夹到端点，不拒整笔——那 5 秒里别人可能已经动过这一天的行数。
            position = max(0, min(payload.position, len(ordered)))
        elif payload.after_place_id and payload.after_place_id in ordered:
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


def _text(value: object) -> str:
    """自由文本列。null 的语义是「清空」，那就落成空串——``str(None)`` 会写出字面量
    "None"，那不是清空，是一段用户从没打过、界面上也改不掉的文字。"""
    return "" if value is None else str(value)


def _bounded_text(max_len: int) -> Callable[[object], str]:
    """自由文本列的长度闸。上限拦的是**绕开前端直接打接口**的写入：一段几 KB 的「地点名」
    会随每次快照与广播原样放大给房里所有人。超了就拒（抛 ValueError，整笔 patch 记
    ``bad_patch``）而不是悄悄截断——被拒的那一笔用户在界面上看得见，截断的那一笔只会
    在别人屏幕上少掉尾巴。"""

    def coerce(value: object) -> str:
        text = _text(value)
        if len(text) > max_len:
            raise ValueError("text_too_long")
        return text

    return coerce


def normalize_day_date(value: object) -> str | None:
    """天日期的唯一判据：规范 ``YYYY-MM-DD`` 或 None。WS 的 day_add 与 patch 共用，
    见 ``_coerce_day_date`` 的注释——形状不对的日期炸的是整段行程的倒计时。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _coerce_day_date(value: object) -> str | None:
    """patch 侧走唯一判据（见 ``normalize_day_date``）：读不懂就当「没填」。"""
    return normalize_day_date(value)


def _coerce_day_start_min(value: object) -> int:
    """行程起始时刻 NOT NULL 且必须落在一天之内：null 不是合法的清空意图，越界夹回边缘。"""
    if value is None:
        raise ValueError("day_start_min 不能是 null")
    return clamp_min(int(value))  # type: ignore[arg-type]


PLACE_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "name": _bounded_text(120),
    "address": _bounded_text(300),
    "duration_min": _coerce_int,
    "start_min": _coerce_int,
    "user_start_min": _coerce_int,
    "note": _bounded_text(2000),
    "locked": lambda v: 1 if v else 0,
    "status": str,
}

DAY_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "title": _bounded_text(120),
    "date": _coerce_day_date,
    "start_min": _coerce_int,
    "travel_mode": lambda v: str(v) if v is not None else None,
    "start_place_id": lambda v: str(v) if v is not None else None,
    "end_place_id": lambda v: str(v) if v is not None else None,
}

MAX_BUDGET_CENTS = 1_000_000_000  # ¥10,000,000：够一段真实的团队旅行，又挡得住手滑多打几个 0


def _coerce_budget_cents(value: object) -> int:
    """预算只许是非负整数。负数与 None 都当作「清空」= 0，超大值夹住而不是报错。"""
    if value is None:
        return 0
    return max(0, min(int(value), MAX_BUDGET_CENTS))  # type: ignore[arg-type]


def _coerce_cover_url(value: object) -> str:
    """封面值会被同行者的 ``<img src>`` 直接渲染，所以这一列不是自由文本。

    只收四类：站内上传路径（``/uploads/...``）、内置海报（``/covers/...``——M37 决策 3 说
    「点中任意一张会固定为本行程的封面」，固定下来就是要写进这一列，所以它的形状必须过这道闸）、
    http(s) 直链、以及空串——空串是「恢复默认封面」这个动作的落点，不是「没填」。``data:`` 能塞
    进几百 KB 的整张图，``javascript:`` 与裸文本会在图上留一个永远读不出来的破图，一律拒（抛
    ValueError，整笔 patch 被拒）。站内两条只认前缀与形状，不去查文件在不在：破图由海报头自己
    兜，这里要拦的是任意文本；``..`` 单独挡掉，因为这两条都是拼进静态目录的路径。
    """
    if value is None:
        return ""
    url = str(value).strip()
    if len(url) > 500:
        raise ValueError("bad_cover_url")
    if not url:
        return ""
    in_app = url.startswith(("/uploads/", "/covers/"))
    if not ((in_app or url.startswith(("http://", "https://"))) and ".." not in url):
        raise ValueError("bad_cover_url")
    return url


TRIP_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "title": _bounded_text(120),
    "city": _bounded_text(60),
    "cover_url": _coerce_cover_url,
    "travel_mode": str,
    "cost_model": str,
    "day_start_min": _coerce_day_start_min,
    "status": str,
    "budget_cents": _coerce_budget_cents,
}


def _validate_patched_trip(patch: dict[str, object]) -> str | None:
    """状态、出行方式、距离模型都是写死的枚举，不是自由文本：这三列都带 CHECK，
    认不出的值必须在 UPDATE 之前拒掉。让 SQLite 去拒绝等于把一次坏输入升级成
    ``IntegrityError`` —— REST 那边是 500，WS 那边是整条连接被异常带走，
    客户端连「你这笔没生效」都收不到。null 在这里同样算非法：这三列没有「空」这一档。
    """
    for key, allowed in (
        ("status", TripStatus),
        ("travel_mode", TravelMode),
        ("cost_model", CostModel),
    ):
        if key in patch:
            value = patch[key]
            if value is None or str(value) not in {member.value for member in allowed}:
                return f"bad_{key}"
    return None


def _validate_patched_day(patch: dict[str, object]) -> str | None:
    """days.travel_mode 同样带 CHECK（NULL 合法 = 跟随行程）。"""
    mode = patch.get("travel_mode")
    if "travel_mode" in patch and mode is not None:
        if str(mode) not in {member.value for member in TravelMode}:
            return "bad_travel_mode"
    return None


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
    """Domain checks the DB cannot express. Returns a rejection reason or None.

    ``status`` 这里连 null 一起拒：那列只有 confirmed / pending 两档，没有「空」这一档，
    放 null 过去只会在 CHECK 上撞出一个 IntegrityError。
    """
    if "status" in patch:
        status = patch["status"]
        if status is None or str(status) not in ("confirmed", "pending"):
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
    if _validate_patched_day(patch) is not None:
        return None

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
    """Apply a whitelisted trip patch.

    换封面时旧文件的回收也在这里：`cover_url` 的三个写入口（上传端点、REST patch、WS 的
    `trip_update`）都经过本函数，删文件放进其中一处就另外两条路漏孤儿。放在事务**之后**是
    必要的——提交失败回滚时库里仍指着那张图，删了就是一张永久坏链。
    """
    if _validate_patched_trip(patch) is not None:
        return None

    def _update(conn: sqlite3.Connection) -> tuple[dict | None, str]:
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if row is None:
            return None, ""
        assignments = _patch_assignments(patch, TRIP_PATCH_FIELDS)
        if assignments is None:
            return None, ""
        columns, values = assignments
        sets = ", ".join(f"{col} = ?" for col in columns)
        stale = str(row["cover_url"] or "") if "cover_url" in columns else ""
        conn.execute(f"UPDATE trips SET {sets} WHERE id = ?", [*values, trip_id])
        fresh = dict(conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone())
        return fresh, "" if fresh["cover_url"] == stale else stale

    row, stale = await db.run(_update)
    if stale:
        drop_cover_file(stale, trip_id)
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


# -- checklist（出行清单）----------------------------------------------------------------


CHECKLIST_TEXT_MAX = 120


def _clean_texts(texts: Sequence[object]) -> list[str]:
    """Trim, drop empties, cap length, dedupe **within the batch**.

    Dedupe against the rows already in the trip happens in SQL, where the current state
    is actually visible; doing it here would let a second 「一键补全」 re-add everything
    the first one typed.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = str(raw).strip()[:CHECKLIST_TEXT_MAX]
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


async def checklist_add(
    db: Database, trip_id: str, texts: Sequence[object], added_by: str = ""
) -> list[ChecklistItemOut]:
    """Append a batch of items, skipping texts this trip already carries.

    Returns the rows actually inserted -- possibly none, which is a legitimate result
    (「一键补全」 pressed twice must not produce a second screen of duplicates) and must
    not be reported as an error.
    """
    batch = _clean_texts(texts)

    def _insert(conn: sqlite3.Connection) -> list[dict]:
        if not batch:
            return []
        existing = {
            str(row["text"])
            for row in conn.execute(
                "SELECT text FROM checklist_items WHERE trip_id = ?", (trip_id,)
            ).fetchall()
        }
        todo = [text for text in batch if text not in existing]
        if not todo:
            return []
        base_row = conn.execute(
            "SELECT COALESCE(MAX(sort_index), -1) AS m FROM checklist_items WHERE trip_id = ?",
            (trip_id,),
        ).fetchone()
        base = int(base_row["m"]) + 1
        now = now_iso()
        ids = [new_id() for _ in todo]
        conn.executemany(
            """INSERT INTO checklist_items
                   (id, trip_id, sort_index, text, done, added_by, rev, created_at, updated_at)
               VALUES (?, ?, ?, ?, 0, ?, 1, ?, ?)""",
            [(ids[i], trip_id, base + i, todo[i], added_by, now, now) for i in range(len(todo))],
        )
        rows = conn.execute(
            f"SELECT * FROM checklist_items WHERE id IN ({', '.join('?' for _ in ids)})",
            tuple(ids),
        ).fetchall()
        order = {text: i for i, text in enumerate(todo)}
        return sorted((dict(r) for r in rows), key=lambda r: order.get(r["text"], 0))

    inserted = await db.run(_insert)
    return [ChecklistItemOut.model_validate(row) for row in inserted]


async def checklist_ids(db: Database, trip_id: str) -> list[str]:
    """The authoritative order, same shape as `place_ids_for_day`: a reorder op carries
    the full array so two clients dragging at once converge instead of diverging."""
    rows = await db.fetch_all(
        "SELECT id FROM checklist_items WHERE trip_id = ? ORDER BY sort_index", (trip_id,)
    )
    return [str(row["id"]) for row in rows]


CHECKLIST_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "text": lambda v: str(v).strip()[:CHECKLIST_TEXT_MAX],
    "done": lambda v: 1 if v else 0,
}


async def update_checklist(
    db: Database, item_id: str, patch: dict[str, object]
) -> ChecklistItemOut | None:
    def _update(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT id FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        assignments = _patch_assignments(patch, CHECKLIST_PATCH_FIELDS)
        if assignments is None:
            return None
        columns, values = assignments
        if "text" in columns and not str(values[columns.index("text")]):
            return None  # 清空文本不是改名，是要把这条删掉——那走 delete
        sets = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE checklist_items SET {sets}, rev = rev + 1, updated_at = ? WHERE id = ?",
            [*values, now_iso(), item_id],
        )
        fresh = conn.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
        return dict(fresh)

    row = await db.run(_update)
    return ChecklistItemOut.model_validate(row) if row is not None else None


async def delete_checklist(db: Database, trip_id: str, item_id: str) -> bool:
    def _delete(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "DELETE FROM checklist_items WHERE id = ? AND trip_id = ?", (item_id, trip_id)
        )
        return cur.rowcount > 0

    return await db.run(_delete)


async def reorder_checklist(
    db: Database, trip_id: str, item_ids: Sequence[str]
) -> ReorderResult:
    """Permutation guard identical to `reorder_day`: the array must be exactly the current
    id set. Index deltas under two people dragging at once diverge forever.

    `ReorderResult.place_ids` here carries checklist item ids -- the field is the
    authoritative order, whatever kind of row is being ordered.
    """
    current = await checklist_ids(db, trip_id)
    wanted = [str(i) for i in item_ids]
    if sorted(wanted) != sorted(current) or len(set(wanted)) != len(wanted):
        return ReorderResult(ok=False, place_ids=current)

    def _apply(conn: sqlite3.Connection) -> None:
        conn.executemany(
            "UPDATE checklist_items SET sort_index = ? WHERE id = ? AND trip_id = ?",
            [(index, item_id, trip_id) for index, item_id in enumerate(wanted)],
        )

    await db.run(_apply)
    return ReorderResult(ok=True, place_ids=wanted)


# -- expenses（费用与 AA）-----------------------------------------------------------------


def _clean_split_ids(value: object) -> list[str]:
    """Dedupe, keep the caller's order, drop blanks. Accepts a JSON string (what the
    column holds) or a list (what an op payload carries)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for raw in value:
        client_id = str(raw).strip()
        if client_id and client_id not in out:
            out.append(client_id)
        if len(out) >= 40:
            break
    return out


def _clean_category(value: object) -> str:
    label = str(value).strip().lower()
    return label if label in EXPENSE_CATEGORIES else "other"


async def expense_add(db: Database, trip_id: str, payload: ExpenseCreate) -> ExpenseOut:
    expense_id = new_id()
    splits = _clean_split_ids(payload.split_ids)
    payer = payload.paid_by.strip()
    # 没人分摊 = 付款人自己全担。留一份空名单会让「AA」这个词说谎，也会让结算少算一笔。
    if not splits and payer:
        splits = [payer]
    now = now_iso()

    def _insert(conn: sqlite3.Connection) -> dict:
        conn.execute(
            """INSERT INTO expenses
                   (id, trip_id, title, amount_cents, category, paid_by, paid_by_name,
                    split_ids, created_at, updated_at, rev)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                expense_id,
                trip_id,
                payload.title.strip()[:80],
                payload.amount_cents,
                _clean_category(payload.category),
                payer,
                payload.paid_by_name.strip()[:40],
                json.dumps(splits, ensure_ascii=False),
                now,
                now,
            ),
        )
        return dict(conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone())

    return ExpenseOut.model_validate(await db.run(_insert))


EXPENSE_PATCH_FIELDS: dict[str, Callable[[object], object]] = {
    "title": lambda v: str(v).strip()[:80],
    "amount_cents": lambda v: int(v),
    "category": _clean_category,
    "split_ids": lambda v: json.dumps(_clean_split_ids(v), ensure_ascii=False),
    "paid_by": lambda v: str(v).strip()[:40],
    "paid_by_name": lambda v: str(v).strip()[:40],
}


def _validate_patched_expense(patch: dict[str, object]) -> str | None:
    if "amount_cents" in patch:
        amount = patch["amount_cents"]
        # bool 是 int 的子类：不挡的话 True 悄悄变成 1 分。
        if isinstance(amount, bool):
            return "bad_amount"
        try:
            cents = int(amount)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "bad_amount"
        if not 1 <= cents <= 1_000_000_000:
            return "bad_amount"
    if "paid_by" in patch:
        # 付款人是「谁垫的」这份事实本身。清成空会让应摊照算、垫付无处落账，
        # AA 的合计从此不平账——而账本上看不出任何一处错。
        payer = patch["paid_by"]
        if payer is None or not str(payer).strip():
            return "bad_payer"
    return None


async def update_expense(
    db: Database, expense_id: str, patch: dict[str, object]
) -> ExpenseOut | None:
    if _validate_patched_expense(patch) is not None:
        return None

    def _update(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT id FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is None:
            return None
        assignments = _patch_assignments(patch, EXPENSE_PATCH_FIELDS)
        if assignments is None:
            return None
        columns, values = assignments
        if "title" in columns and not str(values[columns.index("title")]):
            return None
        sets = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE expenses SET {sets}, rev = rev + 1, updated_at = ? WHERE id = ?",
            [*values, now_iso(), expense_id],
        )
        return dict(conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone())

    row = await db.run(_update)
    return ExpenseOut.model_validate(row) if row is not None else None


async def delete_expense(db: Database, trip_id: str, expense_id: str) -> bool:
    def _delete(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "DELETE FROM expenses WHERE id = ? AND trip_id = ?", (expense_id, trip_id)
        )
        return cur.rowcount > 0

    return await db.run(_delete)


# -- messages（同行聊天）---------------------------------------------------------------

# 快照带走的条数。它是**读取窗口，不是删除策略**：写入时永不裁剪，否则 seq 会指向一条
# 已经不存在的行，resync 时就成了「我是不是漏了一条」这种查不出来的问题。
MESSAGE_WINDOW = 60
MESSAGE_TEXT_MAX = 300


def _clean_ref(value: object) -> str:
    return str(value or "").strip()[:40]


async def message_add(
    db: Database, trip_id: str, client_id: str, payload: MessageIn
) -> MessageOut | None:
    """记一句留言。空文本返回 None（调用方拒）。

    锚点指向不属于本行程的东西时**丢掉锚点、把话照发**：同伴刚删掉那张卡，不该让这句话
    因此发不出去——内容比挂点重要。最多挂一个，地点优先（它比「某一天」具体）。
    """
    text = str(payload.text or "").strip()[:MESSAGE_TEXT_MAX]
    if not text:
        return None
    message_id = new_id()
    now = now_iso()
    place_ref = _clean_ref(payload.ref_place_id)
    day_ref = _clean_ref(payload.ref_day_id)

    def _insert(conn: sqlite3.Connection) -> dict:
        place, day = place_ref, day_ref
        if place and conn.execute(
            "SELECT 1 FROM places WHERE id = ? AND trip_id = ?", (place, trip_id)
        ).fetchone() is None:
            place = ""
        if day and conn.execute(
            "SELECT 1 FROM days WHERE id = ? AND trip_id = ?", (day, trip_id)
        ).fetchone() is None:
            day = ""
        if place:
            day = ""
        conn.execute(
            """INSERT INTO messages
                   (id, trip_id, client_id, text, ref_place_id, ref_day_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (message_id, trip_id, client_id[:40], text, place, day, now),
        )
        row = conn.execute(
            "SELECT rowid AS pos, * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return dict(row)

    return MessageOut.model_validate(await db.run(_insert))


async def message_delete(
    db: Database, trip_id: str, message_id: str, client_id: str
) -> tuple[str, MessageOut | None]:
    """软删一句留言，返回 `('ok'|'not_found'|'not_owner', 那一行)`。

    软删而不是硬删：**聊天的位置就是语义**——「换成早上去吧」指的是它上面那句，撤销若把
    它放回末尾就接不上上文。返回那一行是为了让 5 秒撤销知道该放回哪一格。
    """

    def _apply(conn: sqlite3.Connection):
        row = conn.execute(
            """SELECT rowid AS pos, * FROM messages
               WHERE id = ? AND trip_id = ? AND deleted_at IS NULL""",
            (message_id, trip_id),
        ).fetchone()
        if row is None:
            return "not_found", None
        if str(row["client_id"] or "") != client_id:
            return "not_owner", None
        conn.execute("UPDATE messages SET deleted_at = ? WHERE id = ?", (now_iso(), message_id))
        return "ok", MessageOut.model_validate(dict(row))

    return await db.run(_apply)


async def message_restore(
    db: Database, trip_id: str, message_id: str, client_id: str
) -> tuple[str, MessageOut | None]:
    """撤销那次删除：把 `deleted_at` 清回 NULL。

    行从头到尾没动过，所以 `pos` 还是原来的 `pos`——「回到原位」的全部实现就是这一列。
    """

    def _apply(conn: sqlite3.Connection):
        row = conn.execute(
            """SELECT rowid AS pos, * FROM messages
               WHERE id = ? AND trip_id = ? AND deleted_at IS NOT NULL""",
            (message_id, trip_id),
        ).fetchone()
        if row is None:
            return "not_found", None
        if str(row["client_id"] or "") != client_id:
            return "not_owner", None
        conn.execute("UPDATE messages SET deleted_at = NULL WHERE id = ?", (message_id,))
        return "ok", MessageOut.model_validate(dict(row))

    return await db.run(_apply)
