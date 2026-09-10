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
import typing

from app.amap.client import fetch_photo_best_effort
from app.db import repositories
from app.db.database import get_db
from app.models import protocol
from app.models.domain import PlaceCreate, StashCreate
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
    }
    handler = handlers.get(op)
    if handler is None:
        conn.send_json(protocol.error_frame(f"未知 op：{op}"))
        return

    db = get_db()
    await handler(conn, hub, db, client_id, op_id, data)


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
        # 有内容的天必须先删地点：拒绝并告知原因，绝不静默丢数据。
        await _reject(conn, op_id, "day_not_empty", {"day_id": day_id})
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
