"""SQLite access: plain stdlib sqlite3, no ORM.

Why not SQLModel/SQLAlchemy: the Pydantic models in app/models/domain.py *are* the
WebSocket wire protocol, so they have to exist regardless. An ORM would add a second,
parallel set of models plus a mapping layer between them. This is a fresh database with
no migration history, and sqlite3 is in the standard library.

The async story: SQLite connections are cheap to open (~0.1 ms) and must not be shared
across threads, so we open a short-lived connection per operation and push the whole
thing onto a worker thread with anyio (which ships with Starlette -- no new dependency).
Nothing here ever blocks the event loop.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

import anyio.to_thread

from app.config import settings

logger = logging.getLogger("tourplan.db")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

Params = Sequence[Any] | dict[str, Any]
T = TypeVar("T")

# Set on every connection. journal_mode=WAL is persistent in the database file, so it is
# applied once in init().
_PER_CONNECTION_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
)


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else settings.db_path

    # -- lifecycle ---------------------------------------------------------------

    async def init(self) -> None:
        await anyio.to_thread.run_sync(self._init_sync)

    def _init_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(schema)
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()
        logger.info("sqlite ready at %s", self.path)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Tiny hand-rolled migrations. schema.sql covers NEW tables (IF NOT EXISTS);
        column additions to existing tables need an explicit ALTER guarded here."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trips)").fetchall()}
        if "created_by" not in columns:
            conn.execute("ALTER TABLE trips ADD COLUMN created_by TEXT")


    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        for pragma in _PER_CONNECTION_PRAGMAS:
            conn.execute(pragma)
        return conn

    # -- primitives --------------------------------------------------------------

    async def run(self, fn: Callable[[sqlite3.Connection], T], *args: Any) -> T:
        """Run `fn(conn)` inside one transaction on a worker thread.

        This is the workhorse: a repository method that needs several statements to be
        atomic (insert a place *and* renumber its tail) passes a single closure.
        """
        return await anyio.to_thread.run_sync(self._run_sync, fn, args)

    def _run_sync(self, fn: Callable[[sqlite3.Connection], T], args: tuple[Any, ...]) -> T:
        conn = self._connect()
        try:
            with conn:  # commit on success, rollback on exception
                return fn(conn, *args)
        finally:
            conn.close()

    async def fetch_all(self, sql: str, params: Params = ()) -> list[dict[str, Any]]:
        rows = await self.run(lambda conn: conn.execute(sql, params).fetchall())
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: Params = ()) -> dict[str, Any] | None:
        row = await self.run(lambda conn: conn.execute(sql, params).fetchone())
        return dict(row) if row is not None else None

    async def execute(self, sql: str, params: Params = ()) -> int:
        """Returns the number of affected rows."""
        return await self.run(lambda conn: conn.execute(sql, params).rowcount)


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_db(db: Database) -> None:
    """For tests: install a Database pointed at a temporary file."""
    global _db
    _db = db
