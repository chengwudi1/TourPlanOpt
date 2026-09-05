"""One WebSocket, from accept to close.

Responsibilities kept here (and only here): frame size cap, rate limiting, silence
detection, ordered sending. Message *semantics* live in handlers.py so the transport
rules can be tested and read without the domain logic getting in the way.

Rate limiting is two buckets:
- strict: settings.ws_rate_limit_messages per window -- applies to every frame EXCEPT
  presence. Presence is exempt because a 250 ms-throttled presence stream (~40
  frames/10 s) would trip a 30-msgs/10 s limit and a client would silence itself.
- loose: settings.ws_rate_limit_frames per window -- applies to everything, presence
  included, as the runaway-client backstop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.config import settings
from app.models.protocol import PROTOCOL_VERSION
from app.ws import handlers
from app.ws.hub import Hub, TripHub

logger = logging.getLogger("tourplan.ws")

_CLOSE_TOO_BIG = 1009
_CLOSE_POLICY = 1008


class _SlidingWindow:
    """Timestamps of recent frames; cheap at the sizes involved (tens of entries)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: deque[float] = deque()

    def _drop_expired(self, now: float) -> None:
        cutoff = now - self.window_s
        hits = self._hits
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def allow(self, now: float) -> bool:
        self._drop_expired(now)
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True


class ClientConnection:
    """State for one accepted socket. Identity is set by the hello frame, not the URL:
    names must never end up in access logs or URLs."""

    def __init__(self, websocket: WebSocket, hub: Hub, trip_id: str) -> None:
        self.websocket = websocket
        self.hub = hub
        self.trip_id = trip_id
        self.trip_hub: TripHub | None = None
        self.client_id: str | None = None
        self.name = ""
        self.color = ""

        # Set in run(): the loop this connection lives on. Other clients (and their
        # loops) may broadcast to us at any time, so every send is marshaled onto THIS
        # loop -- also the reason broadcasts are fire-and-forget: a slow client must
        # never stall the sender's room.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send_lock = asyncio.Lock()
        self._strict = _SlidingWindow(
            settings.ws_rate_limit_messages, settings.ws_rate_limit_window_s
        )
        self._loose = _SlidingWindow(
            settings.ws_rate_limit_frames, settings.ws_rate_limit_window_s
        )

    # -- sending ---------------------------------------------------------------------

    async def _send_text(self, text: str) -> None:
        """Serialized socket writes: two tasks must not interleave on one socket."""
        async with self._send_lock:
            try:
                await self.websocket.send_text(text)
            except Exception:  # noqa: BLE001 - any send failure means the socket is gone
                pass  # the receive loop notices and cleans up

    def send_json(self, frame: dict) -> None:
        """Queue one frame for delivery. Thread-safe and non-blocking: callable from any
        connection's task (and therefore any event loop the test harness creates)."""
        if self._loop is None or self._loop.is_closed():
            return
        text = json.dumps(frame, ensure_ascii=False)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            asyncio.create_task(self._send_text(text))
        else:
            asyncio.run_coroutine_threadsafe(self._send_text(text), self._loop)

    def schedule_close(self) -> None:
        """Close from a foreign loop safely (e.g. a second tab replacing this one)."""
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    async def _close(self) -> None:
        try:
            await self.websocket.close()
        except Exception:  # noqa: BLE001 - already closed
            pass

    # -- lifecycle -------------------------------------------------------------------

    async def run(self) -> None:
        """Receive loop. Returns when the socket dies, times out, or violates policy."""
        self._loop = asyncio.get_running_loop()
        silence_s = settings.ws_silence_timeout_s
        while True:
            try:
                raw = await asyncio.wait_for(self.websocket.receive_text(), timeout=silence_s)
            except TimeoutError:
                # Half-open TCP never raises on receive -- the silence timeout is what
                # actually detects a dead peer (e.g. laptop asleep behind NAT).
                logger.info("ws %s timed out after %ss silence", self.client_id, silence_s)
                break
            except WebSocketDisconnect:
                break

            now = time.monotonic()
            if len(raw.encode("utf-8")) > settings.ws_max_payload_bytes:
                await self.websocket.close(code=_CLOSE_TOO_BIG)
                break
            if not self._loose.allow(now):
                await self.websocket.close(code=_CLOSE_POLICY)
                break
            try:
                frame = json.loads(raw)
            except ValueError:
                self.send_json(handlers.error_frame("帧不是合法 JSON"))
                continue

            if frame.get("v") != PROTOCOL_VERSION:
                self.send_json(
                    handlers.error_frame(
                        f"协议版本不支持：{frame.get('v')}", hint=f"本服务只讲 v{PROTOCOL_VERSION}"
                    )
                )
                continue

            is_presence = frame.get("type") == handlers.ClientMsg.PRESENCE
            if not is_presence and not self._strict.allow(now):
                self.send_json(
                    handlers.error_frame(
                        "发送太快被限流",
                        hint="普通消息上限 30 条/10 秒。presence 帧不受此限。",
                    )
                )
                continue

            handled = await handlers.dispatch(self, frame)
            if handled == handlers.Dispatch.CLOSED:
                break

    async def finish(self) -> None:
        """Idempotent cleanup: leave the room, drop presence, tell the others."""
        if self.trip_hub is not None and self.client_id is not None:
            hub = self.trip_hub
            hub.members.pop(self.client_id, None)
            if self.client_id in hub.presence:
                hub.drop_presence(self.client_id)
                await handlers.broadcast_presence_leave(hub, self.client_id)
            self.hub.release_if_empty(self.trip_id)
        await self._close()
