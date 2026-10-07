"""站间耗时按 origin_index 对位，不按「第几个结果」对位。

高德 /v3/distance 会少返条目（client.distance 因此把结果按 origin_id 回映射，并带着
批内下标 origin_index 交回来）。矩阵这一层如果改用位置下标，少一条就让那一列之后每一格
整体错一格，而且会把错值连同**错误的坐标对**写进距离缓存——脏数据此后会被别的行程复用，
直到 TTL 过期都读得到。这条判据钉的就是这两件事。
"""

from __future__ import annotations

import asyncio

from app.amap.client import DistanceResult
from app.models.domain import TravelMode
from app.routing.matrix import build_matrix

# 两位小数：1e5 取整之后仍是原值，替身里可以直接用 index 反查是谁。
NODES = [(104.00, 30.60), (104.02, 30.61), (104.04, 30.62), (104.06, 30.63)]


def seconds_for(i: int, j: int) -> int:
    """可识别的耗时：错一格就对不上，且每格唯一。"""
    return 1000 + 100 * i + j


class FakeCache:
    def __init__(self) -> None:
        self.rows: list = []

    async def get(self, pairs, mode):  # 全部未命中：这一轮所有格子都要实时调用
        return {}

    async def put(self, rows):
        self.rows.extend(rows)
        return len(rows)


class FakeClient:
    """像高德一样回话：可能少给一条，但 origin_index 一定指向送出去的那个起点。"""

    def __init__(self, drop: tuple[int, int] | None = None) -> None:
        self.drop = drop  # (目的地下标 j, 批内位置 pos)
        self.calls: list[tuple[int, int]] = []

    async def distance(self, origins, destination, mode):
        j = NODES.index(tuple(destination))
        self.calls.append((j, len(origins)))
        out = []
        for pos, origin in enumerate(origins):
            if self.drop and j == self.drop[0] and pos == self.drop[1]:
                continue
            i = NODES.index(tuple(origin))
            out.append(
                DistanceResult(
                    origin_index=pos,
                    distance_m=6000 + 10 * i + j,
                    duration_s=seconds_for(i, j),
                    ok=True,
                )
            )
        return out


def test_matrix_keeps_a_missing_cell_missing_instead_borrowing_a_neighbours_time():
    cache, client = FakeCache(), FakeClient(drop=(2, 1))
    res = asyncio.run(
        build_matrix(NODES, travel_mode=TravelMode.DRIVING, cache=cache, client=client)
    )

    # 第 1 站到第 2 站这一格高德没回：必须是「没有值」，不能拿第 3 站的耗时顶上。
    assert res.seconds[1][2] is None, (
        f'少返的那格拿到了 {res.seconds[1][2]}（第 1→2 站应为 {seconds_for(1, 2)}，'
        f'第 3→2 站是 {seconds_for(3, 2)}）——整列错位一格'
    )
    for j in (1, 2, 3):
        for i in range(len(NODES)):
            if i == j or (i, j) == (1, 2):
                continue
            assert res.seconds[i][j] == seconds_for(i, j), (
                f'第 {i + 1} 个地点到第 {j + 1} 个地点拿成了别人的耗时：'
                f'{res.seconds[i][j]} ≠ {seconds_for(i, j)}'
            )


def test_cache_rows_pair_each_duration_with_the_origin_it_came_from():
    """缓存写脏比矩阵算错更久：它会活过这一次请求，被别的行程读到。"""
    cache, client = FakeCache(), FakeClient(drop=(2, 1))
    asyncio.run(build_matrix(NODES, travel_mode=TravelMode.DRIVING, cache=cache, client=client))

    assert cache.rows, '替身没被调用，这条判据什么都没测'
    for row in cache.rows:
        i = NODES.index(tuple(row.origin))
        j = NODES.index(tuple(row.destination))
        assert row.duration_s == seconds_for(i, j), (
            f'缓存把第 {i}→{j} 站存成了 {row.duration_s} 秒，而第 {i} 站到第 {j} 站其实是 '
            f'{seconds_for(i, j)} 秒：这一对坐标之后每次重算都会读到脏值'
        )
