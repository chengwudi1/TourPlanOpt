"""Protocol drift guard.

backend/app/models/protocol.py and frontend/src/types/protocol.ts are a hand-maintained
pair. A cheap string check beats a silent drift that only shows up as a runtime
"未知 op" in someone's browser.
"""

from __future__ import annotations

from pathlib import Path

from app.models import protocol

FRONTEND_PROTOCOL = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "types" / "protocol.ts"
)


def _ts_source() -> str:
    assert FRONTEND_PROTOCOL.exists(), f"missing mirror file: {FRONTEND_PROTOCOL}"
    return FRONTEND_PROTOCOL.read_text(encoding="utf-8")


def _literals(cls: type) -> list[str]:
    """The uppercase string constants of a namespace class, skipping dunders."""
    return [
        value for key, value in vars(cls).items()
        if not key.startswith("_") and isinstance(value, str)
    ]


def test_every_client_message_exists_in_ts_mirror():
    ts = _ts_source()
    for name in _literals(protocol.ClientMsg):
        assert f"'{name}'" in ts, f"ClientMsg '{name}' missing in protocol.ts"


def test_every_server_message_exists_in_ts_mirror():
    ts = _ts_source()
    for name in _literals(protocol.ServerMsg):
        assert f"'{name}'" in ts, f"ServerMsg '{name}' missing in protocol.ts"


def test_every_op_exists_in_ts_mirror():
    ts = _ts_source()
    for name in _literals(protocol.Ops):
        assert f"'{name}'" in ts, f"Op '{name}' missing in protocol.ts"


def test_ops_map_to_handlers():
    """Every op defined in the protocol must be routed in ops.py's handler table."""
    import app.ws.ops as ops_module

    source = Path(ops_module.__file__).read_text(encoding="utf-8")
    for const_name, value in vars(protocol.Ops).items():
        if const_name.startswith("_") or not isinstance(value, str):
            continue
        assert f"Ops.{const_name}" in source, f"op {value} has no handler in ops.py"
