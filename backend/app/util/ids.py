"""Identifier generation.

Trip/day/place ids are 8-char base32: URL-safe, copy-pasteable into a chat message,
and short enough to read aloud. The RFC 4648 base32 alphabet (A-Z, 2-7) deliberately
omits 0 and 1, so there is no digit/letter ambiguity when someone types a share link
by hand.
"""

from __future__ import annotations

import secrets
import uuid

_BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def new_id(n_chars: int = 8) -> str:
    """A random base32 id. 8 chars = 40 bits ~ 1.1e12 values."""
    value = int.from_bytes(secrets.token_bytes((n_chars * 5 + 7) // 8), "big")
    out = [_BASE32[(value >> (5 * i)) & 31] for i in range(n_chars)]
    out.reverse()
    return "".join(out)


def new_uuid() -> str:
    """For op ids and client ids, where length does not matter."""
    return str(uuid.uuid4())
