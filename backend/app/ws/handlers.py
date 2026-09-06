"""What each client frame means.

Pure domain logic: receives a ClientConnection, talks to the repositories and the hub.
No frame-size or rate-limit concerns -- those live in connection.py. Ops (place_add,
day_reorder, ...) arrive through the same dispatch and all funnel through the same
apply-broadcast sequence so there is exactly one implementation of every mutation.
"""

from __future__ import annotations

import enum
import logging
import time
import typing

from app.db.database import Database, get_db
from app.db.repositories import current_seq, get_snapshot, next_seq, upsert_participant
from app.models import protocol
from app.models.domain import Presence, Snapshot
from app.ws.hub import TripHub

if typing.TYPE_CHECKING:
    from app.ws.connection import ClientConnection

logger = logging.getLogger("tourplan.ws")

ClientMsg = protocol.ClientMsg
ServerMsg = protocol.ServerMsg
Ops = protocol.Ops


class Dispatch(enum.Enum):
    OK = "ok"
    CLOSED = "closed"


def error_frame(message: str, hint: str = "") -> dict:
    return protocol.error_frame(message, hint)


async def dispatch(conn: ClientConnection, frame: dict) -> Dispatch:
    msg_type = frame.get("type")
    if msg_type == ClientMsg.HELLO:
        await _hello(conn, frame)
    elif msg_type == ClientMsg.PING:
        conn.send_json(protocol.pong_frame())
    elif msg_type == ClientMsg.RESYNC:
        await _resync(conn)
    elif msg_type == ClientMsg.PRESENCE:
        await _presence(conn, frame)
    elif msg_type == ClientMsg.OP:
        await _op(conn, frame)
    else:
        conn.send_json(error_frame(f"未知消息类型：{msg_type}"))
    return Dispatch.OK


# -- hello / welcome -------------------------------------------------------------------


async def _hello(conn: ClientConnection, frame: dict) -> Dispatch:
    data = frame.get("data") or {}
    client_id = str(data.get("client_id") or "").strip()
    name = str(data.get("name") or "").strip()
    color = str(data.get("color") or "").strip()
    if not client_id or not (1 <= len(name) <= 40):
        conn.send_json(error_frame("hello 缺少 client_id 或 name（1-40 字）"))
        return Dispatch.CLOSED

    db = get_db()
    snapshot = await get_snapshot(db, conn.trip_id)
    if snapshot is None:
        conn.send_json(error_frame(f"行程 {conn.trip_id} 不存在"))
        return Dispatch.CLOSED

    conn.client_id = client_id
    conn.name = name
    conn.color = color

    # Same client_id reconnecting (or a second tab of the same session): the new
    # connection replaces the old one. Kick the old first so its cleanup cannot drop
    # the presence the new connection is about to register.
    hub = conn.hub.get(conn.trip_id)
    old = hub.members.get(client_id)
    if old is not None and old is not conn:
        hub.members.pop(client_id, None)
        old.trip_hub = None  # its finish() must not undo this join
        old.schedule_close()

    participant = await upsert_participant(db, conn.trip_id, client_id, name, color)
    hub.members[client_id] = conn
    conn.trip_hub = hub
    presence = Presence(
        client_id=client_id,
        name=name,
        color=color or participant.color,
        current_day_id=None,
        focusing_place_id=None,
        joined_at=time.time(),
    )
    hub.set_presence(client_id, presence.model_dump())

    # welcome carries the CURRENT seq without consuming one: it is a snapshot, not a
    # broadcast event, and every seq that IS consumed reaches every member.
    seq = await current_seq(db, conn.trip_id)
    welcome = protocol.welcome_frame(
        seq,
        {
            "you": presence.model_dump(),
            "snapshot": _snapshot_with_presence(snapshot, hub),
        },
    )
    conn.send_json(welcome)

    # Broadcast to everyone including the joiner: every seq-consuming frame must reach
    # every member or the seq-gap heuristic would fire false resyncs. The client applies
    # its own presence idempotently.
    await _broadcast_presence(hub, db, ServerMsg.PRESENCE_JOIN, presence.model_dump())
    return Dispatch.OK


def _snapshot_with_presence(snapshot: Snapshot, hub: TripHub) -> dict:
    data = snapshot.model_dump()
    data["presence"] = hub.presence_list()
    return data


# -- presence --------------------------------------------------------------------------


async def _presence(conn: ClientConnection, frame: dict) -> Dispatch:
    hub = conn.trip_hub
    if hub is None or conn.client_id is None:
        conn.send_json(error_frame("请先发送 hello"))
        return Dispatch.OK
    data = frame.get("data") or {}
    hub.set_presence(
        conn.client_id,
        {
            "current_day_id": data.get("current_day_id"),
            "focusing_place_id": data.get("focusing_place_id"),
            "dragging_day_id": data.get("dragging_day_id"),
        },
    )
    await _broadcast_presence(
        hub, get_db(), ServerMsg.PRESENCE_UPDATE, hub.presence[conn.client_id]
    )
    return Dispatch.OK


async def _broadcast_presence(
    hub: TripHub, db: Database, type_: str, data: dict, *, exclude: str | None = None
) -> None:
    client_id = data.get("client_id", "")
    # Presence is throttled per client (hub.presence_should_broadcast); frames exempt
    # from the strict rate limit are coalesced here instead.
    if not hub.presence_should_broadcast(client_id, time.monotonic()):
        return
    seq = await next_seq(db, hub.trip_id)
    hub.broadcast_presence(type_, client_id, data, seq=seq, exclude=exclude)


async def broadcast_presence_leave(hub: TripHub, client_id: str) -> None:
    """Called from connection.finish() -- the leaver is already gone from members."""
    seq = await next_seq(get_db(), hub.trip_id)
    hub.broadcast_presence(
        ServerMsg.PRESENCE_LEAVE, client_id, {"client_id": client_id}, seq=seq
    )


# -- resync / ops ----------------------------------------------------------------------


async def _resync(conn: ClientConnection) -> Dispatch:
    hub = conn.trip_hub
    if hub is None or conn.client_id is None:
        conn.send_json(error_frame("请先发送 hello"))
        return Dispatch.OK
    db = get_db()
    snapshot = await get_snapshot(db, conn.trip_id)
    if snapshot is None:
        conn.send_json(error_frame(f"行程 {conn.trip_id} 不存在"))
        return Dispatch.CLOSED
    seq = await next_seq(db, conn.trip_id)
    conn.send_json(
        protocol.welcome_frame(
            seq,
            {
                "you": hub.presence.get(conn.client_id, {"client_id": conn.client_id}),
                "snapshot": _snapshot_with_presence(snapshot, hub),
            },
        )
    )
    return Dispatch.OK


async def _op(conn: ClientConnection, frame: dict) -> Dispatch:
    hub = conn.trip_hub
    if hub is None or conn.client_id is None:
        conn.send_json(error_frame("请先发送 hello"))
        return Dispatch.OK
    from app.ws import ops

    await ops.apply_op(conn, hub, frame)
    return Dispatch.OK
