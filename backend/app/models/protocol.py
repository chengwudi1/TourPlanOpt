"""The WebSocket protocol in one place.

`frontend/src/types/protocol.ts` is a hand-mirror of this file; tests/test_protocol.py
asserts every op literal defined here appears in that file, so the two cannot drift
silently. Every frame on the wire is one of the shapes built by the helpers below.

Design rules the helpers encode (see the approved plan):
- Every frame carries ``v`` so a future protocol change is detectable.
- Server frames that mutate state carry a monotonically increasing ``seq`` (trips.seq).
- Ops broadcast to ALL connections *including the originator*, tagged with ``origin``
  and the sender's ``op_id`` -- that echo is how the sender confirms its optimistic
  write instead of double-applying it.
"""

from __future__ import annotations

import time
from typing import Any

PROTOCOL_VERSION = 1

# -- client -> server --------------------------------------------------------------


class ClientMsg:
    HELLO = "hello"
    OP = "op"
    PRESENCE = "presence"
    RESYNC = "resync"
    PING = "ping"


# Ops carried inside a `type:"op"` frame. Server message names are past tense
# ("place_updated") because they announce an applied result, never an intent.
class Ops:
    PLACE_ADD = "place_add"
    PLACE_UPDATE = "place_update"
    PLACE_DELETE = "place_delete"
    PLACE_LOCK = "place_lock"
    DAY_REORDER = "day_reorder"

    DAY_ADD = "day_add"
    DAY_DELETE = "day_delete"
    PLACE_MOVE = "place_move"
    STASH_ADD = "stash_add"
    STASH_REMOVE = "stash_remove"
    DAY_UPDATE = "day_update"
    TRIP_UPDATE = "trip_update"

    CHECKLIST_ADD = "checklist_add"
    CHECKLIST_UPDATE = "checklist_update"
    CHECKLIST_DELETE = "checklist_delete"
    CHECKLIST_REORDER = "checklist_reorder"
    EXPENSE_ADD = "expense_add"
    EXPENSE_UPDATE = "expense_update"
    EXPENSE_DELETE = "expense_delete"

    # M30 同行聊天。**故意没有 message_update**：话发出去就不许改，于是 `rev` 恒为 1，
    # 也省掉「同一条消息两个版本谁赢」这套判断。delete 是软删，restore 按 id 把它放回
    # 原来那一格——聊天的位置就是语义，放回末尾会接不上上文。
    MESSAGE_ADD = "message_add"
    MESSAGE_DELETE = "message_delete"
    MESSAGE_RESTORE = "message_restore"


# -- server -> client --------------------------------------------------------------


class ServerMsg:
    WELCOME = "welcome"
    OP = "op"
    OP_REJECT = "op_reject"
    PRESENCE_JOIN = "presence_join"
    PRESENCE_LEAVE = "presence_leave"
    PRESENCE_UPDATE = "presence_update"
    ERROR = "error"
    PONG = "pong"


def _envelope(type_: str, **fields: Any) -> dict[str, Any]:
    frame = {"v": PROTOCOL_VERSION, "type": type_}
    frame.update(fields)
    return frame


def hello_frame(client_id: str, name: str, color: str) -> dict[str, Any]:
    return _envelope(
        ClientMsg.HELLO, data={"client_id": client_id, "name": name, "color": color}
    )


def presence_frame(
    current_day_id: str | None,
    focusing_place_id: str | None,
    dragging_day_id: str | None = None,
) -> dict[str, Any]:
    return _envelope(
        ClientMsg.PRESENCE,
        data={
            "current_day_id": current_day_id,
            "focusing_place_id": focusing_place_id,
            "dragging_day_id": dragging_day_id,
        },
    )


def resync_frame(last_seq: int) -> dict[str, Any]:
    return _envelope(ClientMsg.RESYNC, data={"last_seq": last_seq})


def ping_frame() -> dict[str, Any]:
    return _envelope(ClientMsg.PING)


def op_frame(op: str, op_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return _envelope(ClientMsg.OP, op=op, op_id=op_id, data=data)


def welcome_frame(seq: int, data: dict[str, Any]) -> dict[str, Any]:
    return _envelope(ServerMsg.WELCOME, seq=seq, data=data)


def broadcast_op_frame(
    seq: int, op: str, origin: str, op_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    return _envelope(
        ServerMsg.OP,
        seq=seq,
        op=op,
        origin=origin,
        op_id=op_id,
        ts=int(time.time() * 1000),
        data=data,
    )


def op_reject_frame(op_id: str, reason: str, data: dict[str, Any]) -> dict[str, Any]:
    return _envelope(ServerMsg.OP_REJECT, op_id=op_id, reason=reason, data=data)


def presence_frame_out(type_: str, seq: int, data: dict[str, Any]) -> dict[str, Any]:
    return _envelope(type_, seq=seq, data=data)


def error_frame(message: str, hint: str = "") -> dict[str, Any]:
    return _envelope(ServerMsg.ERROR, message=message, hint=hint)


def pong_frame() -> dict[str, Any]:
    return _envelope(ServerMsg.PONG, ts=int(time.time() * 1000))
