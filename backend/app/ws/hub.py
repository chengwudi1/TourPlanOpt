"""The in-process collaboration hub.

All collaboration guarantees live here or nowhere: membership, the monotonically
increasing ``seq`` (persisted in trips.seq), broadcast-to-everyone-including-the-
originator, in-memory presence and the op_id LRU that makes reconnection replays
idempotent. Deliberately NOT distributed: one uvicorn worker only (main.py warns about
WEB_CONCURRENCY) -- a second worker would split members across processes and silently
break everything this module promises.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:
    from app.ws.connection import ClientConnection

logger = logging.getLogger("tourplan.ws")

# Presence frames are throttled per client to at most one broadcast per this many
# seconds -- the latest state wins, intermediate states are dropped.
PRESENCE_MIN_INTERVAL_S = 0.25

# 聊天频控：同一个 client 每 2 秒最多一句。防的不是机器人（这产品没有公网），是**误触
# 长按回车**把同伴的手机弹到炸。它叠在既有的 30 帧/10 秒兜底之上——聊天不开任何豁免，
# presence 才需要（250ms 心跳不豁免会自己把自己限流掉）。
MESSAGE_MIN_INTERVAL_S = 2.0

# Reconnecting clients may replay ops they already sent. We remember this many recent
# op_ids per trip; a replay older than the window is applied twice, which the LWW model
# survives (same payload, same result).
OP_ID_LRU_SIZE = 4096

# 同时保有多少段行程的去重历史。房间会生死（最后一人离开即回收 TripHub），历史不能跟着
# 陪葬：重连成员那笔「已应用但 echo 没见过」的 add 全靠它挡住第二次落地。
# 128 段 × 每段至多 4096 个 op_id 是几 MB 量级的字符串，而「房间空了又被人重连回来」
# 在这个产品里是常态（分享链接、手机切后台）。
ROOM_DEDUPE_LIMIT = 128


class TripHub:
    """One room = one trip."""

    def __init__(self, trip_id: str, seen_op_ids: OrderedDict[str, None] | None = None) -> None:
        self.trip_id = trip_id
        self.members: dict[str, ClientConnection] = {}
        # client_id -> last presence payload + last broadcast time. Memory only: a
        # crash must not leave zombie roster rows, so presence is never persisted.
        self.presence: dict[str, dict[str, Any]] = {}
        self._presence_last_broadcast: dict[str, float] = {}
        # 聊天频控同样是内存态：重启后清空不是 bug，是「没人正在连着我就不欠他计数」。
        self._message_last_sent: dict[str, float] = {}
        # op_id 去重集由 Hub 持有并跨房间生死传下来（见 Hub._dedupe_for）：房间空掉时
        # TripHub 被 release_if_empty 回收，下一次 join 新建的实例若带着空集合，重连成员
        # 那笔「服务端已应用、echo 却没收到」的 add 就不再被认成重复，place_add 会落两行。
        self._seen_op_ids: OrderedDict[str, None] = (
            seen_op_ids if seen_op_ids is not None else OrderedDict()
        )

    # -- membership ------------------------------------------------------------------

    def is_empty(self) -> bool:
        return not self.members

    def member_ids(self) -> list[str]:
        return list(self.members.keys())

    # -- op_id dedupe ----------------------------------------------------------------

    def seen_op(self, op_id: str) -> bool:
        if op_id in self._seen_op_ids:
            # Move to the end so the LRU eviction order stays honest on replays too.
            self._seen_op_ids.move_to_end(op_id)
            return True
        return False

    def remember_op(self, op_id: str) -> None:
        self._seen_op_ids[op_id] = None
        self._seen_op_ids.move_to_end(op_id)
        while len(self._seen_op_ids) > OP_ID_LRU_SIZE:
            self._seen_op_ids.popitem(last=False)

    # -- presence --------------------------------------------------------------------

    def set_presence(self, client_id: str, payload: dict[str, Any]) -> None:
        existing = self.presence.get(client_id, {})
        self.presence[client_id] = {**existing, **payload, "client_id": client_id}

    def drop_presence(self, client_id: str) -> None:
        self.presence.pop(client_id, None)
        self._presence_last_broadcast.pop(client_id, None)

    def presence_should_broadcast(self, client_id: str, now: float) -> bool:
        last = self._presence_last_broadcast.get(client_id, 0.0)
        if now - last < PRESENCE_MIN_INTERVAL_S:
            return False
        self._presence_last_broadcast[client_id] = now
        return True

    def message_should_send(self, client_id: str, now: float) -> bool:
        """收下这句没有。被拒的那一次**不更新时间**——否则一直按着回车就永远出不了窗口。"""
        last = self._message_last_sent.get(client_id, 0.0)
        if now - last < MESSAGE_MIN_INTERVAL_S:
            return False
        self._message_last_sent[client_id] = now
        return True

    def presence_list(self) -> list[dict[str, Any]]:
        return list(self.presence.values())

    # -- broadcasting ------------------------------------------------------------------

    def broadcast(self, frame: dict[str, Any], *, exclude: str | None = None) -> None:
        """Queue one frame for every member INCLUDING the originator by default.

        The echo is the design: the sender confirms its optimistic write against the
        server's authoritative result via op_id filtering. Filtering per-recipient here
        would need a second ack channel -- the echo is that channel.

        Fire-and-forget on purpose: each ClientConnection marshals the send onto its own
        loop, so a slow or dying client can never stall this room.
        """
        for client_id, conn in list(self.members.items()):
            if client_id == exclude:
                continue
            conn.send_json(frame)

    def broadcast_presence(
        self,
        type_: str,
        client_id: str,
        data: dict[str, Any],
        *,
        seq: int | None,
        exclude: str | None = None,
    ) -> None:
        from app.models.protocol import presence_frame_out

        self.broadcast(presence_frame_out(type_, seq=seq, data=data), exclude=exclude)


class Hub:
    """trip_id -> TripHub, created on demand, torn down when the last member leaves."""

    def __init__(self) -> None:
        self._trips: dict[str, TripHub] = {}
        # 去重集住在这一层而不是房间那一层：房间会生死，重连客户端的记忆不会。
        # LRU 封顶的是「被记住的行程数」，每程内部另有 OP_ID_LRU_SIZE 的上限。
        self._dedupe: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()

    def _dedupe_for(self, trip_id: str) -> OrderedDict[str, None]:
        seen = self._dedupe.get(trip_id)
        if seen is not None:
            self._dedupe.move_to_end(trip_id)
            return seen
        seen = OrderedDict()
        self._dedupe[trip_id] = seen
        while len(self._dedupe) > ROOM_DEDUPE_LIMIT:
            # 只挤掉当前没有开着的房间的历史；全是活房间时宁可暂时超限，也不清活人的记忆。
            victim = next((old for old in self._dedupe if old not in self._trips), None)
            if victim is None:
                break
            self._dedupe.pop(victim)
        return seen

    def get(self, trip_id: str) -> TripHub:
        hub = self._trips.get(trip_id)
        if hub is None:
            hub = TripHub(trip_id, self._dedupe_for(trip_id))
            self._trips[trip_id] = hub
        return hub

    def peek(self, trip_id: str) -> TripHub | None:
        """Look without creating.

        `get()` on a trip nobody has open would leave an empty TripHub in the map forever
        -- only a disconnect calls `release_if_empty`. A REST handler that wants to "shout
        into the room if there is one" must not have to own that room.
        """
        return self._trips.get(trip_id)

    def release_if_empty(self, trip_id: str) -> None:
        hub = self._trips.get(trip_id)
        if hub is not None and hub.is_empty():
            del self._trips[trip_id]

    def trip_count(self) -> int:
        return len(self._trips)


_hub: Hub | None = None


def get_hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


def reset_hub() -> None:
    """For tests."""
    global _hub
    _hub = None


def silence_timeout_s() -> float:
    return settings.ws_silence_timeout_s
