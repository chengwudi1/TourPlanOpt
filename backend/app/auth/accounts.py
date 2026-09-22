"""Accounts: registration, login, cookie sessions. Deliberately small.

Auth is OPTIONAL by design -- guests can still create and join trips through the
share-link flow (the product's core loop). An account buys two things today:
「我的行程」 history (recently opened trips) and 「偏好」 (users.prefs: theme, font size,
motion, basemap, sprite). Everything here is stdlib only --
pbkdf2_hmac for hashing, SQLite for session storage, no JWT, no email.

Password rules kept honest for a LAN tool: scrypt/pbkdf2 with a per-user salt, tokens
are 32 random bytes stored server-side (revocable by logout/clear), cookie is
httpOnly + SameSite=Lax. Login attempts are NOT rate-limited yet -- noted as a known
limitation for public deployment in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from app.db.database import Database
from app.util.ids import new_id
from app.util.timefmt import now_iso

PBKDF2_ITERATIONS = 200_000
SESSION_TTL_DAYS = 7
COOKIE_NAME = "tourplan_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _expiry_iso() -> str:
    return (datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS)).isoformat()


async def create_user(db: Database, name: str, password: str) -> dict:
    user_id = new_id()

    def _create(conn: sqlite3.Connection) -> dict:
        conn.execute(
            "INSERT INTO users (id, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, name, hash_password(password), now_iso()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row)

    return await db.run(_create)


async def find_user_by_name(db: Database, name: str) -> dict | None:
    return await db.fetch_one("SELECT * FROM users WHERE name = ?", (name,))


async def create_session(db: Database, user_id: str) -> str:
    token = secrets.token_hex(32)

    def _create(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, _expiry_iso(), now_iso()),
        )
        # opportunistic cleanup: dropping this user's expired sessions keeps the table
        # from growing forever without a background job.
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))

    await db.run(_create)
    return token


async def user_for_token(db: Database, token: str) -> dict | None:
    row = await db.fetch_one(
        """SELECT u.id, u.name FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, now_iso()),
    )
    return dict(row) if row else None


async def drop_session(db: Database, token: str) -> None:
    await db.execute("DELETE FROM sessions WHERE token = ?", (token,))


async def record_visit(db: Database, trip_id: str, user_id: str) -> None:
    def _upsert(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO trip_visits (trip_id, user_id, last_seen) VALUES (?, ?, ?)
               ON CONFLICT(trip_id, user_id) DO UPDATE SET last_seen = excluded.last_seen""",
            (trip_id, user_id, now_iso()),
        )

    await db.run(_upsert)


async def ensure_trip_member(db: Database, trip_id: str, user_id: str) -> None:
    """A trip is 'mine' if I created it OR opened it while logged in."""
    await record_visit(db, trip_id, user_id)


async def my_trips(db: Database, user_id: str) -> list[dict]:
    """Trips I created or visited, most recent first, with a place count."""

    def _load(conn: sqlite3.Connection) -> list[dict]:
        rows = conn.execute(
            """SELECT t.id, t.title, t.city, t.created_at,
                      COALESCE(v.last_seen, t.created_at) AS last_seen,
                      (SELECT COUNT(*) FROM places p WHERE p.trip_id = t.id) AS place_count,
                      EXISTS (SELECT 1 FROM trips tt WHERE tt.id = t.id AND tt.created_by = ?)
                          AS owned
               FROM trips t
               LEFT JOIN trip_visits v ON v.trip_id = t.id AND v.user_id = ?
               WHERE t.created_by = ?
                  OR v.user_id = ?
               ORDER BY COALESCE(v.last_seen, t.created_at) DESC""",
            (user_id, user_id, user_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]

    return await db.run(_load)


async def unfollow(db: Database, trip_id: str, user_id: str) -> bool:
    """删掉「我打开过这趟」的那一行足迹。

    边界要说清：这只抹掉 visit。自己创建的行程照样会因为 ``created_by`` 出现在
    ``my_trips`` 里——那是所有权，不是浏览史，一个「从首页移除」的按钮无权收回。
    """

    def _delete(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "DELETE FROM trip_visits WHERE trip_id = ? AND user_id = ?", (trip_id, user_id)
        )
        return cur.rowcount > 0

    return await db.run(_delete)


# ---------- M31 个人偏好（users.prefs 那一整块 JSON） ----------

PREF_MAX_BYTES = 4096

_THEME_VALUES = frozenset({"auto", "light", "dark"})
_FONT_VALUES = frozenset({"md", "lg", "xl"})
_MOTION_VALUES = frozenset({"auto", "reduce"})
_BASEMAP_VALUES = frozenset({"auto", "light"})


def sanitize_prefs(raw: object) -> dict:
    """只留认识的键和取值，其余丢掉。

    丢掉而不是报错是刻意的：一个还没升级的前端多传了键，不该让自己的偏好保存整个失败；
    但库里也不能堆没人读的垃圾——白名单是这两件事唯一的交点。
    """
    if not isinstance(raw, dict):
        raise ValueError("prefs 需要是一个 JSON 对象")
    out: dict[str, object] = {}
    for key, allowed in (
        ("theme", _THEME_VALUES),
        ("font_size", _FONT_VALUES),
        ("motion", _MOTION_VALUES),
        ("basemap", _BASEMAP_VALUES),
    ):
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
    pet = raw.get("pet_visible")
    if isinstance(pet, bool):
        out["pet_visible"] = pet
    return out


async def get_prefs(db: Database, user_id: str) -> dict:
    row = await db.fetch_one("SELECT prefs FROM users WHERE id = ?", (user_id,))
    if row is None:
        return {}
    try:
        stored = json.loads(row["prefs"] or "{}")
    except (json.JSONDecodeError, TypeError):
        # 库里那一份坏掉就当没有：界面照样能开，下一次保存会顺手写回一份干净的。
        return {}
    return sanitize_prefs(stored)


async def put_prefs(db: Database, user_id: str, prefs: dict) -> dict:
    """逐键合并，不整块覆盖。

    同一账号可能在手机上刚把界面调暗、在电脑上刚把字号调大，两边各自发一次 PUT。整块
    覆盖的话后发的那次会把前一次抹掉，而且用户完全看不出发生过什么。合并之后最坏只是
    同一键后写者赢，那是可以接受的。
    """
    merged = {**(await get_prefs(db, user_id)), **prefs}

    def _write(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE users SET prefs = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False, sort_keys=True), user_id),
        )

    await db.run(_write)
    return merged
