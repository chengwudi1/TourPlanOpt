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
        # M13 地点照片：places/stash 各加一列实拍图直链（老库补列，新库 schema 已含）。
        for table in ("places", "stash"):
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "photo_url" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN photo_url TEXT NOT NULL DEFAULT ''")
            # 高德自有图床两种协议实测同源同图，直接升 https；第三方 http 链不动（不保证有
            # https）。幂等，所以每次启动都跑一遍把 _prefer_https 之前写下的老行补齐。
            conn.execute(
                f"UPDATE {table} SET photo_url = 'https://' || substr(photo_url, 8) "
                "WHERE photo_url LIKE 'http://store.is.autonavi.com/%'"
            )
        # M14 时间轴：start_min 从此只存推导值，每次重算都覆盖；用户手填的时间另存
        # user_start_min，只有它会被排程当作固定时刻。老库里两者混在一列，无法区分，
        # 按 locked=1（手填时间时一起置上的锚点）回填。
        place_cols = {row["name"] for row in conn.execute("PRAGMA table_info(places)").fetchall()}
        if "user_start_min" not in place_cols:
            conn.execute("ALTER TABLE places ADD COLUMN user_start_min INTEGER")
            conn.execute(
                "UPDATE places SET user_start_min = start_min "
                "WHERE locked = 1 AND start_min IS NOT NULL"
            )
        # M21 发现面板排序：缓存键从 (city, category) 扩到 (city, category, sort)。SQLite
        # 改不了主键，只能建新表搬数据——老库里那一份就是默认的综合序。
        cache_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(city_poi_cache)").fetchall()
        }
        if cache_cols and "sort" not in cache_cols:
            conn.executescript(
                """
                CREATE TABLE city_poi_cache_new (
                    city       TEXT NOT NULL,
                    category   TEXT NOT NULL,
                    sort       TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (city, category, sort)
                );
                INSERT INTO city_poi_cache_new
                    SELECT city, category, 'composite', payload, fetched_at FROM city_poi_cache;
                DROP TABLE city_poi_cache;
                ALTER TABLE city_poi_cache_new RENAME TO city_poi_cache;
                """
            )

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
    # The distance cache wraps a Database. Left alone, it would keep reading the file the
    # previous test deleted. Imported lazily: app.amap.cache imports this module.
    from app.amap.cache import set_distance_cache

    set_distance_cache(None)
