"""Mutation ops over the WebSocket.

Every op follows the same shape: validate -> apply through a repository function ->
bump seq -> remember the op_id -> broadcast the RESULT to everyone including the
originator. The originator's echo is its confirmation; other clients apply the result
state (never an intent), so the protocol cannot desynchronize.

Rejections use op_reject with a reason plus whatever authoritative data the client
needs to converge (e.g. the current order after order_stale) -- never a bare error.
"""

from __future__ import annotations

import logging
import time
import typing

from pydantic import ValidationError

from app.amap.client import fetch_photo_best_effort
from app.db import repositories
from app.db.database import get_db
from app.models import protocol
from app.models.domain import ChecklistAdd, ExpenseCreate, MessageIn, PlaceCreate, StashCreate
from app.routing.timeline import reschedule_days
from app.ws.hub import TripHub

if typing.TYPE_CHECKING:
    from app.ws.connection import ClientConnection

logger = logging.getLogger("tourplan.ws")

ServerMsg = protocol.ServerMsg
Ops = protocol.Ops


async def apply_op(conn: ClientConnection, hub: TripHub, frame: dict) -> None:
    op_id = str(frame.get("op_id") or "").strip()
    if not op_id:
        conn.send_json(protocol.error_frame("op 缺少 op_id"))
        return

    # Duplicate delivery (reconnect replay, transport retry): the first broadcast
    # already did the work, so a repeat must be a no-op -- NOT a second application.
    if hub.seen_op(op_id):
        return

    op = str(frame.get("op") or "")
    data = frame.get("data") or {}
    client_id = conn.client_id or ""

    handlers = {
        Ops.PLACE_ADD: _place_add,
        Ops.PLACE_MOVE: _place_move,
        Ops.DAY_DELETE: _day_delete,
        Ops.STASH_ADD: _stash_add,
        Ops.STASH_REMOVE: _stash_remove,
        Ops.PLACE_UPDATE: _place_update,
        Ops.PLACE_DELETE: _place_delete,
        Ops.PLACE_LOCK: _place_lock,
        Ops.DAY_REORDER: _day_reorder,
        Ops.DAY_ADD: _day_add,
        Ops.DAY_UPDATE: _day_update,
        Ops.TRIP_UPDATE: _trip_update,
        Ops.CHECKLIST_ADD: _checklist_add,
        Ops.CHECKLIST_UPDATE: _checklist_update,
        Ops.CHECKLIST_DELETE: _checklist_delete,
        Ops.CHECKLIST_REORDER: _checklist_reorder,
        Ops.EXPENSE_ADD: _expense_add,
        Ops.EXPENSE_UPDATE: _expense_update,
        Ops.EXPENSE_DELETE: _expense_delete,
        Ops.MESSAGE_ADD: _message_add,
        Ops.MESSAGE_DELETE: _message_delete,
        Ops.MESSAGE_RESTORE: _message_restore,
    }
    handler = handlers.get(op)
    if handler is None:
        conn.send_json(protocol.error_frame(f"未知 op：{op}"))
        return

    db = get_db()
    try:
        await handler(conn, hub, db, client_id, op_id, data)
    except Exception:  # noqa: BLE001 - 一个 op 的意外不能带走整条连接
        # 抛出去的后果是 socket 被异常关掉：客户端既没有回执也没有错误，只看到「重连中」，
        # 而它那条乐观改过的行还留在原地。失败时没有 remember_op，客户端可以照原样重试。
        logger.exception("op %s (op_id=%s) 在服务端异常", op, op_id)
        await _reject(conn, op_id, "op_failed")


async def _broadcast(
    hub: TripHub,
    db,
    trip_id: str,
    op: str,
    op_id: str,
    origin: str,
    data: dict,
) -> None:
    seq = await repositories.next_seq(db, trip_id)
    hub.remember_op(op_id)
    hub.broadcast(protocol.broadcast_op_frame(seq, op, origin, op_id, data))


async def _retimeline(hub: TripHub, db, trip_id: str, day_ids: list[str]) -> None:
    """Re-time these days and broadcast the authoritative rows.

    Sent as its own frame after the op's own broadcast, so clients first apply the
    change they made and then the schedule it implies -- never a half-updated timeline.
    """
    timelines = await reschedule_days(db, trip_id, day_ids)
    if not timelines:
        return
    seq = await repositories.next_seq(db, trip_id)
    hub.broadcast(
        protocol.broadcast_op_frame(
            seq,
            "timeline_updated",
            origin="server",
            op_id=f"timeline-{seq}",
            data={"timelines": [t.payload() for t in timelines]},
        )
    )


async def _reject(
    conn: ClientConnection, op_id: str, reason: str, data: dict | None = None
) -> None:
    conn.send_json(protocol.op_reject_frame(op_id, reason, data or {}))


# -- places ------------------------------------------------------------------------

# Only these patched fields feed the schedule. Recomputing on a rename would bump every
# rev in the day and make other clients redraw for nothing.
_TIMELINE_PLACE_FIELDS = frozenset({"duration_min", "start_min", "locked"})
_TIMELINE_DAY_FIELDS = frozenset({"start_min", "travel_mode", "start_place_id", "end_place_id"})
_TIMELINE_TRIP_FIELDS = frozenset({"travel_mode", "day_start_min"})


async def _place_add(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    day_id = str(data.get("day_id") or "")
    day = await repositories.get_day(db, day_id) if day_id else None
    if day is None or day.trip_id != trip_id:
        await _reject(conn, op_id, "day_not_found", {"day_id": day_id})
        return
    try:
        payload = PlaceCreate(
            name=str(data.get("name") or "").strip(),
            lng=float(data.get("lng")),
            lat=float(data.get("lat")),
            address=str(data.get("address") or ""),
            amap_poi_id=str(data.get("amap_poi_id") or ""),
            duration_min=int(data.get("duration_min", 60)),
            note=str(data.get("note") or ""),
            added_by=str(data.get("added_by") or client_id),
            after_place_id=data.get("after_place_id"),
            position=data.get("position"),
            photo_url=str(data.get("photo_url") or ""),
        )
    except (TypeError, ValueError):
        await _reject(conn, op_id, "bad_payload")
        return
    if not payload.name:
        await _reject(conn, op_id, "bad_payload")
        return

    # 照片补抓：搜索联想（inputtips）不带照片，落库前用 POI id 换一张实拍图。
    # best-effort——失败/超时/没配 Key 都不影响加地点本身，photo_url 留空即可。
    if not payload.photo_url and payload.amap_poi_id:
        payload = payload.model_copy(
            update={"photo_url": await fetch_photo_best_effort(payload.amap_poi_id)}
        )

    place = await repositories.add_place(db, day_id, payload)
    if place is None:
        await _reject(conn, op_id, "day_not_found", {"day_id": day_id})
        return
    place_ids = await repositories.place_ids_for_day(db, day_id)
    await _broadcast(
        hub, db, trip_id, "place_added", op_id, client_id,
        {"place": place.model_dump(), "day_id": day_id, "place_ids": place_ids},
    )
    await _retimeline(hub, db, trip_id, [day_id])


async def _place_update(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    place_id = str(data.get("place_id") or "")
    place = await repositories.get_place(db, place_id) if place_id else None
    if place is None or place.trip_id != trip_id:
        await _reject(conn, op_id, "place_not_found", {"place_id": place_id})
        return

    patch = data.get("patch")
    if isinstance(patch, dict) and "start_min" in patch:
        # 修正 2: a hand-set time IS an anchor. Setting a time locks the place for the
        # optimizer; clearing the time releases it -- in the same atomic patch so the
        # broadcast row never shows a time without the pin (or vice versa).
        # 值同时写进 start_min：那是展示位，紧随其后的 _retimeline 会按整条时间轴覆盖它。
        hand_set = patch["start_min"]
        patch = {**patch, "user_start_min": hand_set, "locked": hand_set is not None}
    updated = (
        await repositories.update_place(db, place_id, patch) if isinstance(patch, dict) else None
    )
    if updated is None:
        await _reject(conn, op_id, "bad_patch")
        return
    await _broadcast(
        hub, db, trip_id, "place_updated", op_id, client_id, {"place": updated.model_dump()}
    )
    if _TIMELINE_PLACE_FIELDS & patch.keys():
        await _retimeline(hub, db, trip_id, [updated.day_id])


async def _place_move(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    place_id = str(data.get("place_id") or "")
    to_day_id = str(data.get("day_id") or "")
    place = await repositories.get_place(db, place_id) if place_id else None
    if place is None or place.trip_id != trip_id:
        await _reject(conn, op_id, "place_not_found", {"place_id": place_id})
        return
    result = await repositories.move_place(db, place_id, to_day_id)
    if result is None:
        await _reject(conn, op_id, "day_not_found", {"day_id": to_day_id})
        return
    moved, old_day_id, old_order, new_day_id, new_order = result
    await _broadcast(
        hub, db, trip_id, "place_moved", op_id, client_id,
        {
            "place": moved,
            "old_day_id": old_day_id,
            "old_place_ids": old_order,
            "day_id": new_day_id,
            "place_ids": new_order,
        },
    )
    await _retimeline(hub, db, trip_id, [old_day_id, new_day_id])


async def _place_delete(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    place_id = str(data.get("place_id") or "")
    place = await repositories.get_place(db, place_id) if place_id else None
    if place is None or place.trip_id != trip_id:
        await _reject(conn, op_id, "place_not_found", {"place_id": place_id})
        return

    result = await repositories.delete_place(db, place_id)
    if result is None:
        await _reject(conn, op_id, "place_not_found", {"place_id": place_id})
        return
    day_id, place_ids = result
    await _broadcast(
        hub, db, trip_id, "place_deleted", op_id, client_id,
        {"place_id": place_id, "day_id": day_id, "place_ids": place_ids},
    )
    await _retimeline(hub, db, trip_id, [day_id])


async def _place_lock(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    place_id = str(data.get("place_id") or "")
    place = await repositories.get_place(db, place_id) if place_id else None
    if place is None or place.trip_id != trip_id:
        await _reject(conn, op_id, "place_not_found", {"place_id": place_id})
        return

    locked = bool(data.get("locked"))
    # 手填时间本身就是锚点（求解器的 has_time），所以解锁必须连它一起清掉，
    # 否则「解锁」点了地点还是不动。
    patch: dict[str, object] = {"locked": locked}
    if not locked:
        patch["user_start_min"] = None
    updated = await repositories.update_place(db, place_id, patch)
    if updated is None:  # pragma: no cover - row verified above
        await _reject(conn, op_id, "place_not_found", {"place_id": place_id})
        return
    await _broadcast(
        hub, db, trip_id, "place_locked", op_id, client_id, {"place": updated.model_dump()}
    )
    await _retimeline(hub, db, trip_id, [updated.day_id])


# -- days --------------------------------------------------------------------------


async def _day_reorder(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    day_id = str(data.get("day_id") or "")
    day = await repositories.get_day(db, day_id) if day_id else None
    if day is None or day.trip_id != trip_id:
        await _reject(conn, op_id, "day_not_found", {"day_id": day_id})
        return

    place_ids = data.get("place_ids")
    if not isinstance(place_ids, list):
        await _reject(conn, op_id, "bad_payload")
        return

    # The single non-negotiable validation in the protocol: a reorder must carry the
    # day's full current id set as a permutation. Index deltas diverge permanently
    # under concurrent drags; a rejected array with the authoritative order cannot.
    result = await repositories.reorder_day(db, day_id, [str(p) for p in place_ids])
    if not result.ok:
        await _reject(conn, op_id, "order_stale", {"day_id": day_id, "place_ids": result.place_ids})
        return
    await _broadcast(
        hub, db, trip_id, "day_reordered", op_id, client_id,
        {"day_id": day_id, "place_ids": result.place_ids},
    )
    await _retimeline(hub, db, trip_id, [day_id])


async def _day_add(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    title = str(data.get("title") or "")
    date = data.get("date")
    day = await repositories.create_day(db, trip_id, title, str(date) if date else None)
    if day is None:
        await _reject(conn, op_id, "trip_not_found")
        return
    await _broadcast(hub, db, trip_id, "day_added", op_id, client_id, {"day": day.model_dump()})


async def _day_update(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    day_id = str(data.get("day_id") or "")
    day = await repositories.get_day(db, day_id) if day_id else None
    if day is None or day.trip_id != trip_id:
        await _reject(conn, op_id, "day_not_found", {"day_id": day_id})
        return

    patch = data.get("patch")
    updated = (
        await repositories.update_day(db, day_id, patch) if isinstance(patch, dict) else None
    )
    if updated is None:
        await _reject(conn, op_id, "bad_patch")
        return
    await _broadcast(
        hub, db, trip_id, "day_updated", op_id, client_id, {"day": updated.model_dump()}
    )
    if isinstance(patch, dict) and _TIMELINE_DAY_FIELDS & patch.keys():
        await _retimeline(hub, db, trip_id, [day_id])


async def _day_delete(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    day_id = str(data.get("day_id") or "")
    day = await repositories.get_day(db, day_id) if day_id else None
    if day is None or day.trip_id != trip_id:
        await _reject(conn, op_id, "day_not_found", {"day_id": day_id})
        return

    deleted = await repositories.delete_day(db, trip_id, day_id)
    if not deleted:
        # 两种「不能删」要分开说：有内容的天必须先移走地点，而最后一天是底线。
        # 仓储那层的守卫留着——这里查完到真删之间别人还能改，返回 False 就照旧拒绝。
        remaining = await repositories.place_ids_for_day(db, day_id)
        reason = "day_not_empty" if remaining else "day_last"
        await _reject(conn, op_id, reason, {"day_id": day_id})
        return
    await _broadcast(hub, db, trip_id, "day_deleted", op_id, client_id, {"day_id": day_id})


async def _trip_update(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    patch = data.get("patch")
    updated = (
        await repositories.update_trip(db, trip_id, patch) if isinstance(patch, dict) else None
    )
    if updated is None:
        await _reject(conn, op_id, "bad_patch")
        return
    await _broadcast(
        hub, db, trip_id, "trip_updated", op_id, client_id, {"trip": updated.model_dump()}
    )
    if isinstance(patch, dict) and _TIMELINE_TRIP_FIELDS & patch.keys():
        rows = await db.fetch_all(
            "SELECT id FROM days WHERE trip_id = ? ORDER BY day_index", (trip_id,)
        )
        await _retimeline(hub, db, trip_id, [str(r["id"]) for r in rows])


# -- stash（暂存区）--------------------------------------------------------------------


async def _stash_add(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    try:
        payload = StashCreate(
            name=str(data.get("name") or "").strip(),
            lng=float(data.get("lng")),
            lat=float(data.get("lat")),
            address=str(data.get("address") or ""),
            amap_poi_id=str(data.get("amap_poi_id") or ""),
            added_by=str(data.get("added_by") or client_id),
            photo_url=str(data.get("photo_url") or ""),
        )
    except (TypeError, ValueError):
        await _reject(conn, op_id, "bad_payload")
        return
    if not payload.name:
        await _reject(conn, op_id, "bad_payload")
        return
    if not payload.photo_url and payload.amap_poi_id:
        payload = payload.model_copy(
            update={"photo_url": await fetch_photo_best_effort(payload.amap_poi_id)}
        )
    item = await repositories.stash_add(
        db, trip_id,
        name=payload.name, lng=payload.lng, lat=payload.lat,
        address=payload.address, amap_poi_id=payload.amap_poi_id,
        added_by=payload.added_by, photo_url=payload.photo_url,
    )
    await _broadcast(
        hub, db, trip_id, "stash_added", op_id, client_id, {"item": item.model_dump()}
    )


async def _stash_remove(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    item_id = str(data.get("id") or "")
    removed = await repositories.stash_remove(db, trip_id, item_id) if item_id else False
    if not removed:
        await _reject(conn, op_id, "stash_not_found", {"id": item_id})
        return
    await _broadcast(hub, db, trip_id, "stash_removed", op_id, client_id, {"id": item_id})


# -- checklist（出行清单）----------------------------------------------------------------


async def _checklist_add(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    texts = data.get("texts")
    if not isinstance(texts, list):
        await _reject(conn, op_id, "bad_payload")
        return
    try:
        payload = ChecklistAdd(
            texts=[str(t) for t in texts],
            added_by=str(data.get("added_by") or client_id),
        )
    except ValidationError:
        await _reject(conn, op_id, "bad_payload")
        return

    # 整批一条 op：一条一条发会把 seq 打成一串，别人端上看着像有人连点了十几次添加。
    items = await repositories.checklist_add(db, trip_id, payload.texts, payload.added_by)
    order = await repositories.checklist_ids(db, trip_id)
    await _broadcast(
        hub,
        db,
        trip_id,
        "checklist_added",
        op_id,
        client_id,
        {"items": [i.model_dump() for i in items], "item_ids": order},
    )


async def _checklist_update(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    item_id = str(data.get("id") or "")
    row = (
        await db.fetch_one("SELECT trip_id FROM checklist_items WHERE id = ?", (item_id,))
        if item_id
        else None
    )
    if row is None or row["trip_id"] != trip_id:
        await _reject(conn, op_id, "checklist_not_found", {"id": item_id})
        return
    patch = data.get("patch")
    updated = (
        await repositories.update_checklist(db, item_id, patch)
        if isinstance(patch, dict)
        else None
    )
    if updated is None:
        await _reject(conn, op_id, "bad_patch")
        return
    await _broadcast(
        hub, db, trip_id, "checklist_updated", op_id, client_id, {"item": updated.model_dump()}
    )


async def _checklist_delete(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    item_id = str(data.get("id") or "")
    removed = await repositories.delete_checklist(db, trip_id, item_id) if item_id else False
    if not removed:
        await _reject(conn, op_id, "checklist_not_found", {"id": item_id})
        return
    await _broadcast(hub, db, trip_id, "checklist_deleted", op_id, client_id, {"id": item_id})


async def _checklist_reorder(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    item_ids = data.get("item_ids")
    if not isinstance(item_ids, list):
        await _reject(conn, op_id, "bad_payload")
        return
    result = await repositories.reorder_checklist(db, trip_id, [str(i) for i in item_ids])
    if not result.ok:
        # 与 day_reorder 同一套收敛：拒绝时附上权威顺序，客户端照它重画，不去猜谁对。
        await _reject(conn, op_id, "checklist_stale", {"item_ids": result.place_ids})
        return
    await _broadcast(
        hub,
        db,
        trip_id,
        "checklist_reordered",
        op_id,
        client_id,
        {"item_ids": result.place_ids},
    )


# -- expenses（费用与 AA）-----------------------------------------------------------------


async def _expense_add(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    splits = data.get("split_ids")
    try:
        payload = ExpenseCreate(
            title=str(data.get("title") or "").strip(),
            amount_cents=data.get("amount_cents"),
            category=str(data.get("category") or "other"),
            paid_by=str(data.get("paid_by") or client_id),
            paid_by_name=str(data.get("paid_by_name") or ""),
            split_ids=[str(s) for s in splits] if isinstance(splits, list) else [],
        )
    except ValidationError:
        await _reject(conn, op_id, "bad_expense")
        return
    if not payload.title:
        await _reject(conn, op_id, "bad_expense")
        return
    expense = await repositories.expense_add(db, trip_id, payload)
    await _broadcast(
        hub, db, trip_id, "expense_added", op_id, client_id, {"expense": expense.model_dump()}
    )


async def _expense_update(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    expense_id = str(data.get("id") or "")
    row = (
        await db.fetch_one("SELECT trip_id FROM expenses WHERE id = ?", (expense_id,))
        if expense_id
        else None
    )
    if row is None or row["trip_id"] != trip_id:
        await _reject(conn, op_id, "expense_not_found", {"id": expense_id})
        return
    patch = data.get("patch")
    updated = (
        await repositories.update_expense(db, expense_id, patch)
        if isinstance(patch, dict)
        else None
    )
    if updated is None:
        await _reject(conn, op_id, "bad_patch")
        return
    await _broadcast(
        hub, db, trip_id, "expense_updated", op_id, client_id, {"expense": updated.model_dump()}
    )


async def _expense_delete(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    expense_id = str(data.get("id") or "")
    removed = await repositories.delete_expense(db, trip_id, expense_id) if expense_id else False
    if not removed:
        await _reject(conn, op_id, "expense_not_found", {"id": expense_id})
        return
    await _broadcast(hub, db, trip_id, "expense_deleted", op_id, client_id, {"id": expense_id})


# -- messages（同行聊天）---------------------------------------------------------------

async def _message_add(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    try:
        payload = MessageIn(
            text=str(data.get("text") or ""),
            ref_place_id=str(data.get("ref_place_id") or ""),
            ref_day_id=str(data.get("ref_day_id") or ""),
        )
    except ValidationError:
        await _reject(conn, op_id, "bad_message")
        return
    # 先验内容再频控：一句空话不该把这个人 2 秒的窗口烧掉。
    if not payload.text.strip():
        await _reject(conn, op_id, "bad_message")
        return
    if not hub.message_should_send(client_id, time.monotonic()):
        await _reject(conn, op_id, "message_too_fast", {"retry_after_ms": 2000})
        return
    message = await repositories.message_add(db, trip_id, client_id, payload)
    if message is None:
        await _reject(conn, op_id, "bad_message")
        return
    await _broadcast(
        hub, db, trip_id, "message_added", op_id, client_id, {"message": message.model_dump()}
    )


async def _message_delete(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    message_id = str(data.get("id") or "")
    if not message_id:
        await _reject(conn, op_id, "message_not_found", {"id": ""})
        return
    status, _row = await repositories.message_delete(db, trip_id, message_id, client_id)
    if status != "ok":
        # 越权与不存在分开回：前端要能区分「这句不是你的」和「这句已经没了」——
        # 前者该提示用户，后者只需把自己那条静默收掉。
        await _reject(conn, op_id, f"message_{status}", {"id": message_id})
        return
    await _broadcast(hub, db, trip_id, "message_deleted", op_id, client_id, {"id": message_id})


async def _message_restore(
    conn: ClientConnection, hub: TripHub, db, client_id: str, op_id: str, data: dict
) -> None:
    trip_id = conn.trip_id
    message_id = str(data.get("id") or "")
    if not message_id:
        await _reject(conn, op_id, "message_not_found", {"id": ""})
        return
    status, row = await repositories.message_restore(db, trip_id, message_id, client_id)
    if status != "ok" or row is None:
        await _reject(conn, op_id, f"message_{status}", {"id": message_id})
        return
    # 广播整行而不是只广播「恢复了」：客户端靠行里的 pos 把它插回原来那一格。
    await _broadcast(
        hub, db, trip_id, "message_restored", op_id, client_id, {"message": row.model_dump()}
    )
