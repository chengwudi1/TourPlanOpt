"""Shared WebSocket frame readers for the collaboration tests."""

from __future__ import annotations

from app.models import protocol


def join(ws, client_id: str = "c-1", name: str = "小明") -> dict | None:
    """Hello and consume the handshake frames, returning the schedule summary if any.

    A joiner whose trip already has places gets one unicast ``timeline_updated`` between
    the welcome and its own presence_join (app/ws/handlers.py): the snapshot is a pure
    read, so that frame is how a freshly loaded page learns the day-level schedule.
    Returns None when there is nothing to schedule yet.
    """
    ws.send_json(protocol.hello_frame(client_id, name, "#123456"))
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome"
    frame = ws.receive_json()
    summary = None
    if frame.get("op") == "timeline_updated":
        summary = frame
        frame = ws.receive_json()
    assert frame["type"] == "presence_join", frame
    return summary


def read_op(ws, op: str) -> dict:
    """Read frames until the broadcast of ``op`` arrives, discarding what came before.

    Every op that moves or re-times a place is followed by its own ``timeline_updated``
    frame (app/routing/timeline.py), so a test can no longer assume the frame right
    after its send is its own. ``op_reject`` also ends the read: a rejection means the
    op frame will never come.
    """
    while True:
        frame = ws.receive_json()
        if frame.get("op") == op or frame.get("type") == "op_reject":
            return frame
