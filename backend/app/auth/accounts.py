"""Accounts: registration, login, cookie sessions. Deliberately small.

Auth is OPTIONAL by design -- guests can still create and join trips through the
share-link flow (the product's core loop). An account buys exactly one thing today:
「我的行程」 history (recently opened trips). Everything here is stdlib only --
pbkdf2_hmac for hashing, SQLite for session storage, no JWT, no email.

Password rules kept honest for a LAN tool: scrypt/pbkdf2 with a per-user salt, tokens
are 32 random bytes stored server-side (revocable by logout/clear), cookie is
httpOnly + SameSite=Lax. Login attempts are NOT rate-limited yet -- noted as a known
limitation for public deployment in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import hashlib
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
