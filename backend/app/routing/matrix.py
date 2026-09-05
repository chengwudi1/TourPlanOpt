"""Build the travel-cost matrix for one day.

Layered cost model (the plan's 修正 1): the DEFAULT haversine cost needs zero Amap
calls; the opt-in ``amap`` cost does one /v3/distance call per destination with all
other nodes as origins, skipping the column of the anchor start (nobody *arrives* at
the day's first node), so N nodes cost N-1 calls, all behind the cache.

The walking 5 km guard runs BEFORE any calls: a bounding-box diagonal over 5 km means
every walking call would fail with 20800, so we fall back to driving and say so.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.amap.cache import CacheRow, DistanceCache
from app.amap.client import MAX_ORIGINS_PER_CALL, AmapWebClient, Coord
from app.models.domain import AMAP_MODE_BY_TRAVEL, TravelMode
from app.util.coords import haversine_m

logger = logging.getLogger("tourplan.routing")

# Above this the matrix itself (and the heuristic solver) is the wall; refuse earlier.
HARD_MAX_N = 80

# A pair farther apart than this cannot yield a walking route (Amap's own limit).
WALKING_MAX_M = 5_000.0

# Concurrency cap for matrix columns. The client's token bucket already enforces QPS;
# this bounds in-flight HTTP work.
MAX_COLUMN_CONCURRENCY = 3


@dataclass(slots=True)
class MatrixResult:
    """seconds[i][j] = travel time from node i to node j. Diagonal is always 0.

    ``seconds`` carries the COST used by the solver: for cost_model=amap that is real
    travel seconds; for haversine it is seconds *estimated* from the great-circle
    distance (so both models share one solver and one schedule recurrence).
    """

    seconds: list[list[int | None]]
    api_calls: int = 0
    cache_hits: int = 0
    mode_used: str = "driving"
    fallback_from: str | None = None
    warnings: list[str] = field(default_factory=list)
    unreachable_pairs: list[tuple[int, int]] = field(default_factory=list)


def _bbox_diagonal_m(nodes: list[Coord]) -> float:
    lats = [n[1] for n in nodes]
    lngs = [n[0] for n in nodes]
    return haversine_m((min(lngs), min(lats)), (max(lngs), max(lats)))


def haversine_seconds_matrix(nodes: list[Coord], travel_mode: TravelMode) -> list[list[int]]:
    n = len(nodes)
    matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matrix[i][j] = max(1, round(haversine_m(nodes[i], nodes[j]) / 1000.0 / {
                    "driving": 30.0,
                    "walking": 4.5,
                    "straight": 30.0,
                }[str(travel_mode)] * 3600))
    return matrix


async def build_matrix(
    nodes: list[Coord],
    *,
    travel_mode: TravelMode = TravelMode.DRIVING,
    cost_model: str = "amap",
    cache: DistanceCache,
    client: AmapWebClient,
    progress: Callable[[int, int], None] | None = None,
) -> MatrixResult:
    """Full n×n seconds matrix. Node 0 is the day's anchor start (first place).

    cost_model='haversine' never touches Amap. cost_model='amap' does one call per
    destination column (skipping column 0), all coordinates 1e5-rounded by the cache
    layer, with negative caching for unreachable pairs.
    """
    n = len(nodes)
    if n < 2:
        return MatrixResult(seconds=[[0] * max(n, 1)])
    if n > HARD_MAX_N:
        raise ValueError(f"一天最多优化 {HARD_MAX_N} 个地点（当前 {n} 个）")

    amap_mode = AMAP_MODE_BY_TRAVEL[travel_mode]
    result = MatrixResult(seconds=[[0] * n for _ in range(n)], mode_used=str(travel_mode))

    if cost_model == "haversine":
        result.seconds = haversine_seconds_matrix(nodes, travel_mode)
        return result

    # --- walking guard: fail cheap instead of N guaranteed-20800 calls ---------------
    if travel_mode == TravelMode.WALKING and _bbox_diagonal_m(nodes) > WALKING_MAX_M:
        result.mode_used = "driving"
        result.fallback_from = "walking"
        result.warnings.append(
            "步行模式跨度超过 5 公里，已自动改用驾车估算（高德步行路径不支持超过 5 公里）"
        )
        amap_mode = AMAP_MODE_BY_TRAVEL[TravelMode.DRIVING]

    # --- assemble the pairs we need: every (i, j) with j >= 1, i != j ---------------
    pairs: list[tuple[Coord, Coord]] = []
    for j in range(1, n):
        for i in range(n):
            if i != j:
                pairs.append((nodes[i], nodes[j]))

    cached = await cache.get(pairs, amap_mode)

    def cache_key(o: Coord, d: Coord, scale: int = 100_000) -> tuple[int, int, int, int, int]:
        return (
            round(o[0] * scale),
            round(o[1] * scale),
            round(d[0] * scale),
            round(d[1] * scale),
            amap_mode,
        )

    result.cache_hits = len(cached)

    # Warm entries (positive and negative) fill their cells without any call.
    for j in range(1, n):
        for i in range(n):
            if i == j:
                continue
            entry = cached.get(cache_key(nodes[i], nodes[j]))
            if entry is None:
                continue
            if entry.ok and entry.duration_s is not None:
                result.seconds[i][j] = entry.duration_s
            else:
                result.seconds[i][j] = None
                result.unreachable_pairs.append((i, j))

    # Missing pairs group by destination: one origins-batch per destination call.
    missing_by_dest: dict[int, list[int]] = {}
    for j in range(1, n):
        for i in range(n):
            if i == j:
                continue
            if cache_key(nodes[i], nodes[j]) not in cached:
                missing_by_dest.setdefault(j, []).append(i)

    api_calls = 0
    store_rows: list[CacheRow] = []
    sem = asyncio.Semaphore(MAX_COLUMN_CONCURRENCY)
    columns_total = len(missing_by_dest)
    columns_done = 0

    async def column(j: int, origins: list[int]) -> None:
        nonlocal api_calls, columns_done
        async with sem:
            # The rounded coords are what we send: identical to the cache key space.
            origin_coords = [
                (
                    round(nodes[i][0] * 100_000) / 100_000,
                    round(nodes[i][1] * 100_000) / 100_000,
                )
                for i in origins
            ]
            destination = (
                round(nodes[j][0] * 100_000) / 100_000,
                round(nodes[j][1] * 100_000) / 100_000,
            )
            results = []
            for start in range(0, len(origin_coords), MAX_ORIGINS_PER_CALL):
                batch = origin_coords[start : start + MAX_ORIGINS_PER_CALL]
                results.extend(await client.distance(batch, destination, amap_mode))
                api_calls += 1
        for origin_index, res in enumerate(results):
            i = origins[origin_index]
            store_rows.append(
                CacheRow(
                    origin=origin_coords[origin_index],
                    destination=destination,
                    mode=amap_mode,
                    distance_m=res.distance_m,
                    duration_s=res.duration_s,
                    ok=res.ok,
                    infocode=res.infocode,
                )
            )
            result.seconds[i][j] = res.duration_s if res.ok and res.duration_s is not None else None
            if not res.ok:
                result.unreachable_pairs.append((i, j))
                result.warnings.append(
                    f"第 {i + 1} 个地点到第 {j + 1} 个地点不可达（{res.infocode}），已按不可用处理"
                )
        columns_done += 1
        if progress is not None:
            progress(columns_done, columns_total)

    await asyncio.gather(*(column(j, origins) for j, origins in missing_by_dest.items()))

    if store_rows:
        await cache.put(store_rows)

    result.api_calls = api_calls
    return result
