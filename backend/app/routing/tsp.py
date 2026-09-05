"""The one algorithmically subtle module in the project.

Open-path TSP: given a cost matrix, visit every node exactly once starting at ``start``
and (optionally) ending at ``end``, minimising total cost. NO closing edge unless end
is given -- the day ends wherever it ends.

Two regimes (threshold rationale in the approved plan):
- n <= EXACT_MAX_N: Held-Karp exact DP, O(2^n * n^2), on a FLAT ``array('d')`` indexed
  by ``mask * (n - 1) + last``. Nested dicts are 5-10x slower and would push the
  comfortable interactive threshold down to ~10.
- n > EXACT_MAX_N: nearest-neighbour seed -> 2-opt -> Or-opt, looped until no
  improvement. Or-opt is NOT optional: moving 1-3 node segments catches improvements
  2-opt structurally cannot see on open paths. Typically within 2-5% of optimal,
  far below traffic noise.

Unreachable edges (``None``/``inf``) become a large finite cost so the solver still
returns a usable order; ``TspResult.unreachable_pairs`` always reports them.
"""

from __future__ import annotations

import time
from array import array
from dataclasses import dataclass, field

EXACT_MAX_N = 14
HARD_MAX_N = 80
INF = float("inf")


@dataclass(slots=True)
class TspResult:
    order: list[int]  # indices into the cost matrix, starting with `start`
    total_cost: float
    exact: bool
    nodes: int
    elapsed_ms: float
    unreachable_pairs: list[tuple[int, int]] = field(default_factory=list)


def _finite_cost(cost: list[list[float | None]]) -> list[list[float]]:
    """Materialise a float matrix, substituting a large finite penalty for edges that
    are None/inf. The penalty (a hair above every finite path) keeps such edges out of
    the solution without poisoning the arithmetic."""
    finite = [
        c for row in cost for c in row if c is not None and c != INF and c == c
    ]
    big = (max(finite) + sum(finite) + 1.0) if finite else 1e9
    return [
        [big if (c is None or c != c) else float(c) for c in row] for row in cost
    ], big


def _unreachable_pairs(cost: list[list[float | None]]) -> list[tuple[int, int]]:
    return [
        (i, j)
        for i, row in enumerate(cost)
        for j, c in enumerate(row)
        if i != j and (c is None or c != c or c == INF)
    ]


def held_karp_open(
    cost: list[list[float | None]], start: int, end: int | None = None
) -> tuple[list[int], float]:
    """Exact optimal open path. Returns (order, total)."""
    n = len(cost)
    if n == 1:
        return [start], 0.0
    matrix, _big = _finite_cost(cost)
    m = n - 1  # nodes other than the start
    # Compressed index: position among non-start nodes.
    comp = [i if i < start else i - 1 for i in range(n)]

    size = (1 << m) * m
    dp = array("d", [INF]) * size if size else array("d")
    parent = [-1] * size

    for j in range(n):
        if j == start:
            continue
        cj = comp[j]
        dp[(1 << cj) * m + cj] = matrix[start][j]

    for mask in range(1, 1 << m):
        base = mask * m
        for cj in range(m):
            cur = dp[base + cj]
            if cur == INF:
                continue
            last = cj if cj < start else cj + 1
            row = matrix[last]
            for k in range(n):
                if k == start:
                    continue
                ck = comp[k]
                bit = 1 << ck
                if mask & bit:
                    continue
                new_cost = cur + row[k]
                idx = (mask | bit) * m + ck
                if new_cost < dp[idx]:
                    dp[idx] = new_cost
                    parent[idx] = last

    full = (1 << m) - 1
    if end is not None:
        ce = comp[end]
        total = dp[full * m + ce]
        cl, last = ce, end
    else:
        cl = min(range(m), key=lambda c: dp[full * m + c])
        total = dp[full * m + cl]
        last = cl if cl < start else cl + 1

    # Reconstruct backwards through compressed indices.
    seq: list[int] = []
    mask, cc = full, cl
    while mask:
        seq.append(cc if cc < start else cc + 1)
        prev = parent[mask * m + cc]
        if prev < 0:
            break
        mask ^= 1 << cc
        cc = comp[prev]
    seq.reverse()
    return [start, *seq], total


def held_karp_closed(cost: list[list[float | None]], start: int) -> tuple[list[int], float]:
    """Optimal tour returning to `start`. A cheap invariant oracle: an open-path DP
    with an off-by-one produces a visibly wrong closed-loop cost.

    The optimal closed tour is NOT "optimal open path + return edge" (the best open
    endpoint is rarely the best tour) -- it is min over end of open(start,end) + back,
    which needs one DP run per candidate endpoint."""
    n = len(cost)
    if n == 1:
        return [start], 0.0
    best_order: list[int] = []
    best_total = INF
    for end in range(n):
        if end == start:
            continue
        order, total = held_karp_open(cost, start, end)
        back = cost[order[-1]][start]
        if back is None or back != back:
            continue
        tour_total = total + float(back)
        if tour_total < best_total:
            best_total = tour_total
            best_order = order
    return best_order, best_total


def nearest_neighbour(cost: list[list[float]], start: int) -> list[int]:
    n = len(cost)
    visited = [False] * n
    visited[start] = True
    path = [start]
    cur = start
    for _ in range(n - 1):
        best, best_cost = -1, INF
        for j in range(n):
            if not visited[j] and cost[cur][j] < best_cost:
                best, best_cost = j, cost[cur][j]
        if best < 0:
            break
        visited[best] = True
        path.append(best)
        cur = best
    return path


def path_cost(cost: list[list[float]], path: list[int]) -> float:
    return sum(cost[path[i]][path[i + 1]] for i in range(len(path) - 1))


def two_opt_open(
    cost: list[list[float]], path: list[int], pin_end: bool
) -> list[int]:
    """Open-path 2-opt: reverse path[i..j] for 1 <= i <= j. The start is pinned (the
    edge INTO it never changes), and when `end` is pinned the last node is too. There
    is no closing edge -- the delta is only ever the two boundary edges of the
    reversed segment."""
    improved = True
    while improved:
        improved = False
        n = len(path)
        last_j = n - 2 if pin_end else n - 1
        for i in range(1, n - 1):
            for j in range(i, last_j + 1):
                a, b = path[i - 1], path[i]
                c, d = path[j], (path[j + 1] if j + 1 < n else None)
                before = cost[a][b] + (cost[c][d] if d is not None else 0.0)
                after = cost[a][c] + (cost[b][d] if d is not None else 0.0)
                if after < before - 1e-9:
                    path[i : j + 1] = reversed(path[i : j + 1])
                    improved = True
    return path


def or_opt_open(
    cost: list[list[float]], path: list[int], pin_end: bool, max_segment: int = 3
) -> list[int]:
    """Move segments of 1..max_segment nodes to a better position. The authoritative
    check is the full path cost, which keeps the boundary cases (segment at the tail,
    insertion at the end) from needing their own delta algebra."""
    improved = True
    while improved:
        improved = False
        n = len(path)
        for seg_len in range(1, min(max_segment, n - 1) + 1):
            for i in range(1, n - seg_len + 1):
                j = i + seg_len - 1
                if pin_end and j == n - 1:
                    continue  # would move the pinned end
                seg = path[i : j + 1]
                rest = path[:i] + path[j + 1 :]
                for k in range(1, len(rest) + 1):
                    if pin_end and k == len(rest):
                        continue  # would displace the pinned end
                    candidate = rest[:k] + seg + rest[k:]
                    if path_cost(cost, candidate) < path_cost(cost, path) - 1e-9:
                        path[:] = candidate
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break
    return path


def heuristic_open(
    cost: list[list[float | None]], start: int, end: int | None = None
) -> tuple[list[int], float]:
    matrix, _big = _finite_cost(cost)
    path = nearest_neighbour(matrix, start)
    pin_end = end is not None
    # If end is pinned, make sure it is last before optimising (NN does not know it).
    if pin_end:
        path = [n for n in path if n != end] + [end]
    path = two_opt_open(matrix, path, pin_end)
    path = or_opt_open(matrix, path, pin_end)
    path = two_opt_open(matrix, path, pin_end)
    return path, path_cost(matrix, path)


def solve_tsp_open(
    cost: list[list[float | None]],
    start: int,
    end: int | None = None,
    exact_max_n: int = EXACT_MAX_N,
) -> TspResult:
    """Entry point. start/end are node indices; end=None means an open path."""
    began = time.perf_counter()
    n = len(cost)
    if n > HARD_MAX_N:
        raise ValueError(f"一天最多优化 {HARD_MAX_N} 个地点（当前 {n} 个）")
    if n <= max(2, exact_max_n):
        order, total = held_karp_open(cost, start, end)
        exact = True
    else:
        order, total = heuristic_open(cost, start, end)
        exact = False
    return TspResult(
        order=order,
        total_cost=total,
        exact=exact,
        nodes=n,
        elapsed_ms=(time.perf_counter() - began) * 1000.0,
        unreachable_pairs=_unreachable_pairs(cost),
    )


def optimize_day_order(
    cost: list[list[float | None]],
    place_ids: list[str],
    locked_flags: list[bool],
    has_time_flags: list[bool],
) -> tuple[list[str], list[float], bool]:
    """Anchor-segmented optimization for one day (修正 2).

    Anchors are locked places and places with a hand-set time; they stay put and the
    free places between them are optimized per segment. Returns (ordered place ids,
    per-segment costs, any_exact).
    """
    n = len(place_ids)
    if n < 3:
        return place_ids, [0.0], True

    is_anchor = [
        locked or timed
        for locked, timed in zip(locked_flags, has_time_flags, strict=False)
    ]

    # Split the day into runs: fixed anchors and runs of free places between them.
    runs: list[tuple[str, int | list[int]]] = []
    free_run: list[int] = []
    for idx in range(n):
        if is_anchor[idx]:
            if free_run:
                runs.append(("free", free_run))
                free_run = []
            runs.append(("anchor", idx))
        else:
            free_run.append(idx)
    if free_run:
        runs.append(("free", free_run))

    def subcost(nodes: list[int]) -> list[list[float | None]]:
        return [[cost[a][b] for b in nodes] for a in nodes]

    new_order: list[int] = []
    costs: list[float] = []
    any_exact = True
    for run_index, (kind, payload) in enumerate(runs):
        if kind == "anchor":
            new_order.append(payload)  # the anchor itself: never moved
            continue
        frees = payload
        prev_anchor = (
            new_order[-1]
            if new_order and is_anchor[new_order[-1]]
            else None
        )
        next_anchor = (
            runs[run_index + 1][1]
            if run_index + 1 < len(runs) and runs[run_index + 1][0] == "anchor"
            else None
        )
        if len(frees) < 2:
            # Not enough movable points; keep current relative order (TREK's rule too).
            new_order.extend(frees)
            continue
        head = [prev_anchor] if prev_anchor is not None else []
        tail = [next_anchor] if next_anchor is not None else []
        nodes = [*head, *frees, *tail]
        sub = subcost(nodes)
        start_idx = 0  # leading free run: its current first node stays first
        end_idx = len(nodes) - 1 if tail else None
        result = solve_tsp_open(sub, start_idx, end_idx)
        solved = [nodes[k] for k in result.order]
        if head:
            solved = solved[1:]  # the anchor endpoints were only scaffolding
        if tail:
            solved = solved[:-1]
        new_order.extend(solved)
        costs.append(result.total_cost)
        any_exact = any_exact and result.exact

    if not new_order:
        return place_ids, [0.0], True
    return [place_ids[k] for k in new_order], costs, any_exact
