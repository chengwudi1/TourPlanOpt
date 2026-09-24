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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.amap.cache import CacheRow, DistanceCache, round_key
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

# -- distance single-flight -------------------------------------------------------------
# cache.put 要等整列 gather 完才落盘，两个人同刻 optimize 会双双未命中、把同一批坐标
# 烧两遍配额。这里把在途调用按 (origins, destination, mode) 去重：后来者复用发起者的结果。
_inflight: dict[tuple, asyncio.Task] = {}


async def _distance_shared(
    client: AmapWebClient, batch: list[Coord], destination: Coord, mode: int
) -> list:
    key = (tuple(batch), tuple(destination), mode)
    loop = asyncio.get_running_loop()
    task = _inflight.get(key)
    if task is None or task.get_loop() is not loop:
        task = loop.create_task(client.distance(batch, destination, mode))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key, t=task: _inflight.pop(k, None) if _inflight.get(k) is t else None)
    # shield：等待者被断开（客户端跳页/取消）不许掐掉别人正等的那次已付费调用。
    return await asyncio.shield(task)


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


def permute_matrix(
    cost: list[list[int | None]], ids: Sequence[str], ordered_ids: Sequence[str]
) -> list[list[int | None]]:
    """Reindex a node-keyed cost matrix along ``ordered_ids``.

    The solver works purely in positional indices, so anything that reorders the node
    list -- the 起点锚 rotation, the leg matrix read off a solution -- has to carry the
    matrix along. Passing reordered ids with the old matrix silently optimizes pairs
    that were never compared.
    """
    origin = [ids.index(pid) for pid in ordered_ids]
    return [[cost[i][j] for j in origin] for i in origin]


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
                results.extend(await _distance_shared(client, batch, destination, amap_mode))
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

    # 未命中的格子先置 None：某列实时调用失败时会留下初值 0，solver 会把「0 成本」当相邻
    # 直接排过去，比 None（判不可达）更坏。
    for j, origins in missing_by_dest.items():
        for i in origins:
            result.seconds[i][j] = None

    outcomes = await asyncio.gather(
        *(column(j, origins) for j, origins in missing_by_dest.items()),
        return_exceptions=True,
    )

    # 先落盘已付费且成功的列，再谈报错：不这样做时一列失败抛异常会跳过 cache.put，
    # 成功那几十次调用既白烧配额、下次 optimize 又要重烧一遍。
    if store_rows:
        await cache.put(store_rows)

    failures = [o for o in outcomes if isinstance(o, BaseException)]
    if failures and len(failures) == len(outcomes):
        raise failures[0]
    if failures:
        result.warnings.append(f"{len(failures)} 个目的地调用高德失败，已按不可达处理")

    result.api_calls = api_calls
    return result


async def cached_or_estimate_matrix(
    nodes: list[Coord],
    *,
    travel_mode: TravelMode = TravelMode.DRIVING,
    cache: DistanceCache,
) -> MatrixResult:
    """Zero-call cost matrix: haversine estimate, overlaid with cached real seconds.

    The timeline recomputes on every edit, so it cannot spend quota. Legs already paid
    for by an earlier precise run are reused exactly; the rest are estimates -- which is
    what the UI already labels 「约 X 分钟」.
    """
    n = len(nodes)
    if n < 2:
        return MatrixResult(seconds=[[0] * n for _ in range(n)])

    result = MatrixResult(
        seconds=[list(row) for row in haversine_seconds_matrix(nodes, travel_mode)],
        mode_used=str(travel_mode),
    )

    amap_mode = AMAP_MODE_BY_TRAVEL[travel_mode]
    pairs = [(nodes[i], nodes[j]) for j in range(1, n) for i in range(n) if i != j]
    # The cache rounds to its own 1e5 key space, so resolve hits through the same
    # round_key rather than re-implementing the scale here.
    position = {round_key(c): i for i, c in enumerate(nodes)}
    for key, entry in (await cache.get(pairs, amap_mode)).items():
        if not entry.ok or entry.duration_s is None:
            continue
        # key is (o_lng_r, o_lat_r, d_lng_r, d_lat_r, mode).
        i, j = position.get(key[:2]), position.get(key[2:4])
        if i is None or j is None:  # pragma: no cover - same coords, cannot miss
            continue
        result.seconds[i][j] = entry.duration_s
        result.cache_hits += 1
    return result
