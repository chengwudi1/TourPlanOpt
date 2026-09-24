"""Persistent distance cache in front of the Amap /v3/distance endpoint.

Keys are round(coord * 1e5) integers (~1.1 m), matching the schema's intent: with raw
float keys a marker nudged 30 cm would miss the cache and burn quota. The rounded
coordinates are ALSO what gets sent to Amap -- 1.1 m is far below routing noise.

ok=0 rows are negative-cache entries: a permanently unreachable pair (walking beyond
5 km, island with no road network) must not be re-requested on every optimize.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db.database import Database
from app.util.coords import Coord
from app.util.timefmt import now_iso

COORD_SCALE = 100_000


def _fetched_at_cutoff() -> str:
    """fetched_at 全是 `now_iso()` 写的 UTC 串（同格式同偏移），字典序即时间序。"""
    stale_before = datetime.now(UTC) - timedelta(days=settings.amap_cache_ttl_days)
    return stale_before.isoformat(timespec="seconds")


def round_key(c: Coord) -> tuple[int, int]:
    return round(c[0] * COORD_SCALE), round(c[1] * COORD_SCALE)


@dataclass(slots=True)
class CacheEntry:
    distance_m: int | None
    duration_s: int | None
    ok: bool
    infocode: str | None


@dataclass(slots=True)
class CacheRow:
    """What the caller hands back after a live request."""

    origin: Coord
    destination: Coord
    mode: int
    distance_m: int | None
    duration_s: int | None
    ok: bool
    infocode: str | None


class DistanceCache:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(
        self, pairs: Sequence[tuple[Coord, Coord]], mode: int
    ) -> dict[tuple[int, int, int, int, int], CacheEntry]:
        """Look up (origin, destination) pairs; missing pairs are simply absent."""
        if not pairs:
            return {}

        # SQLite 3.15+ supports row-value IN, which keeps this one indexed query
        # against the composite primary key.
        by_origin: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for o, d in pairs:
            by_origin.setdefault(round_key(o), []).append(round_key(d))

        found: dict[tuple[int, int, int, int, int], CacheEntry] = {}
        cutoff = _fetched_at_cutoff()

        def _load(conn):
            rows = []
            for (o_lng, o_lat), dests in by_origin.items():
                row_placeholders = ",".join("(?, ?)" for _ in dests)
                # fetched_at >= cutoff 让 TTL 到期的高德结果视同未命中：道路与限行会变，
                # 一条永远不过期的实测耗时会把旧路况冻死。过期的行留着，下次实测 upsert 覆盖。
                query = (
                    "SELECT o_lng_r, o_lat_r, d_lng_r, d_lat_r, mode, distance_m, duration_s,"
                    " ok, infocode FROM amap_distance_cache"
                    " WHERE o_lng_r = ? AND o_lat_r = ? AND mode = ? AND fetched_at >= ?"
                    f" AND (d_lng_r, d_lat_r) IN ({row_placeholders})"
                )
                params: list = [o_lng, o_lat, mode, cutoff]
                for d_lng, d_lat in dests:
                    params.extend([d_lng, d_lat])
                rows.extend(conn.execute(query, params).fetchall())
            return rows

        rows = await self._db.run(_load)
        for r in rows:
            key = (r["o_lng_r"], r["o_lat_r"], r["d_lng_r"], r["d_lat_r"], r["mode"])
            found[key] = CacheEntry(
                distance_m=r["distance_m"],
                duration_s=r["duration_s"],
                ok=bool(r["ok"]),
                infocode=r["infocode"],
            )
        return found

    async def put(self, rows: Sequence[CacheRow]) -> int:
        if not rows:
            return 0

        def _store(conn) -> int:
            conn.executemany(
                """INSERT INTO amap_distance_cache
                       (o_lng_r, o_lat_r, d_lng_r, d_lat_r, mode, distance_m, duration_s,
                        ok, infocode, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(o_lng_r, o_lat_r, d_lng_r, d_lat_r, mode) DO UPDATE SET
                       distance_m = excluded.distance_m,
                       duration_s = excluded.duration_s,
                       ok = excluded.ok,
                       infocode = excluded.infocode,
                       fetched_at = excluded.fetched_at""",
                [
                    (
                        *round_key(r.origin),
                        *round_key(r.destination),
                        r.mode,
                        r.distance_m,
                        r.duration_s,
                        1 if r.ok else 0,
                        r.infocode,
                        now_iso(),
                    )
                    for r in rows
                ],
            )
            return len(rows)

        return await self._db.run(_store)

    async def clear(self) -> int:
        return await self._db.execute("DELETE FROM amap_distance_cache")

    async def count(self) -> int:
        row = await self._db.fetch_one("SELECT COUNT(*) AS n FROM amap_distance_cache")
        return int(row["n"]) if row else 0


_cache: DistanceCache | None = None


def get_distance_cache() -> DistanceCache:
    global _cache
    if _cache is None:
        from app.db.database import get_db

        _cache = DistanceCache(get_db())
    return _cache


def set_distance_cache(cache: DistanceCache | None) -> None:
    """For tests."""
    global _cache
    _cache = cache
