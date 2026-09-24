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
import time
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

# 写事务撞锁时的退避重跑次数（IMMEDIATE 已让 busy_timeout 生效排队，这里只兜底极端对撞）。
_WRITE_RETRIES = 2


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


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
        # M22 首页看板：trips 补状态与预算两列。SQLite 的 ALTER 只能加裸默认值的列，加不了
        # CHECK，所以老库这一列没有约束兜底——值一律在写入侧校验（TRIP_PATCH_FIELDS 认不下
        # 的 status 直接拒绝整个 patch），新库的 CHECK 只当一道额外的保险。
        if "status" not in columns:
            conn.execute("ALTER TABLE trips ADD COLUMN status TEXT NOT NULL DEFAULT 'planning'")
        if "budget_cents" not in columns:
            conn.execute(
                "ALTER TABLE trips ADD COLUMN budget_cents INTEGER NOT NULL DEFAULT 0"
            )
        # M31 个人偏好：users 补一列整块 JSON。和上面几列一样，schema.sql 的
        # CREATE TABLE IF NOT EXISTS 对已存在的表是空操作，所以老库必须走这条 ALTER。
        user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if user_cols and "prefs" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN prefs TEXT NOT NULL DEFAULT '{}'")
        # M36 封面：老库补一列空串。空串不是「海报被清空过」的标记，就是「没设过海报」，
        # 两者在自动链上行为一致，所以不必回填。
        if "cover_url" not in columns:
            conn.execute("ALTER TABLE trips ADD COLUMN cover_url TEXT NOT NULL DEFAULT ''")

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
        # BEGIN IMMEDIATE 一开事务就拿写锁：deferred 下「先 SELECT 再 UPDATE」会在别的
        # 写事务已提交时撞上 SQLITE_BUSY_SNAPSHOT，而 busy_timeout 对它无效——两人同时编
        # 辑同一行程就是一条 500 打断 WS。改成先占锁后 busy_timeout 才生效地排队等待，另
        # 加有限次退避重跑兜底（闭包整体重跑是幂等的）。
        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(_WRITE_RETRIES + 1):
            conn = self._connect()
            conn.isolation_level = None  # 事务由下面显式 BEGIN/COMMIT 管理
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = fn(conn, *args)
                except BaseException:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:  # 事务已因冲突失效
                        pass
                    raise
                conn.execute("COMMIT")
                return result
            except sqlite3.OperationalError as exc:
                if not _is_busy(exc) or attempt >= _WRITE_RETRIES:
                    raise
                last_exc = exc
                time.sleep(0.02 * (attempt + 1))
            finally:
                conn.close()
        raise last_exc  # pragma: no cover - 循环内要么 return 要么 raise

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
