"""The project's only algorithmic proof: Held-Karp vs brute force.

On 200 seeded random matrices (n<=9) the exact solver must match the exhaustive
permutation oracle EXACTLY in cost -- for open paths, fixed-end paths, and the
closed-loop invariant. The heuristics must never be worse than their NN seed, and
the schedule recurrence must satisfy its own definition.
"""

from __future__ import annotations

import random
from itertools import permutations

from app.routing.schedule import fill_schedule
from app.routing.tsp import (
    EXACT_MAX_N,
    nearest_neighbour,
    or_opt_open,
    path_cost,
    solve_tsp_open,
    two_opt_open,
)


def random_cost(rng: random.Random, n: int, symmetric: bool = True) -> list[list[float]]:
    cost = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            value = round(rng.uniform(5, 300), 2)
            cost[i][j] = value
            cost[j][i] = value if symmetric else round(rng.uniform(5, 300), 2)
    # A few asymmetric instances exercise the non-symmetric path.
    if not symmetric:
        for i in range(n):
            for j in range(n):
                if i != j and rng.random() < 0.3:
                    cost[i][j] = round(rng.uniform(5, 300), 2)
    return cost


def brute_force_open(cost: list[list[float]], start: int, end: int | None) -> float:
    others = [i for i in range(len(cost)) if i != start]
    if end is not None:
        others = [i for i in others if i != end]
    best = float("inf")
    for perm in permutations(others):
        path = [start, *perm, *([end] if end is not None else [])]
        best = min(best, path_cost(cost, path))
    return best


def test_held_karp_matches_brute_force_200_random_matrices() -> None:
    """THE oracle test. Any mismatch here means the DP is wrong, full stop."""
    rng = random.Random(20260905)
    for trial in range(200):
        n = rng.randint(2, 9)
        cost = random_cost(rng, n, symmetric=(trial % 2 == 0))
        start = rng.randrange(n)
        end = rng.choice([None, *(k for k in range(n) if k != start)])
        result = solve_tsp_open(cost, start, end, exact_max_n=EXACT_MAX_N)
        assert result.exact
        oracle = brute_force_open(cost, start, end)
        assert abs(result.total_cost - oracle) < 1e-6, (
            f"trial {trial}: n={n} start={start} end={end} "
            f"solver={result.total_cost} oracle={oracle}"
        )


def test_closed_loop_invariant() -> None:
    """solve_tsp_open(cost, s, end=s) must equal the optimal closed tour from s.

    Catches the classic open-path DP off-by-one: an edge wrongly added (or dropped)
    at the tail shows up immediately as a wrong closed-loop cost.
    """
    from app.routing.tsp import held_karp_closed

    rng = random.Random(42)
    for trial in range(50):
        n = rng.randint(3, 8)
        cost = random_cost(rng, n)
        start = rng.randrange(n)
        _order, closed = held_karp_closed(cost, start)
        # Brute force the tour: every permutation of the others, back to start.
        others = [i for i in range(n) if i != start]
        best = min(
            path_cost(cost, [start, *list(p), start]) for p in permutations(others)
        )
        assert abs(closed - best) < 1e-6, f"trial {trial}: {closed} vs {best}"


def test_unreachable_edges_are_reported_and_avoided() -> None:
    cost = [
        [0.0, 10.0, None, 20.0],
        [10.0, 0.0, 15.0, None],
        [None, 15.0, 0.0, 12.0],
        [20.0, None, 12.0, 0.0],
    ]
    result = solve_tsp_open(cost, 0)
    assert (0, 2) in result.unreachable_pairs
    assert (1, 3) in result.unreachable_pairs
    # The solver still returns a full order even with holes in the matrix.
    assert sorted(result.order) == [0, 1, 2, 3]


def test_heuristic_never_worse_than_nn_seed() -> None:
    """2-opt + Or-opt start FROM the NN path and only accept strict improvements, so
    their result can never be worse than NN itself."""
    rng = random.Random(7)
    for trial in range(30):
        n = rng.randint(16, 40)
        cost = random_cost(rng, n)
        start = 0
        matrix = [[cost[i][j] for j in range(n)] for i in range(n)]
        nn_path = nearest_neighbour(matrix, start)
        nn_cost = path_cost(matrix, nn_path)

        from app.routing.tsp import heuristic_open

        path, total = heuristic_open(cost, start)
        assert total <= nn_cost + 1e-9, f"trial {trial}: heuristic {total} > NN {nn_cost}"
        assert len(path) == n


def test_heuristic_large_n_completes_quickly() -> None:
    """n beyond EXACT_MAX_N must take the heuristic branch and finish in order."""
    rng = random.Random(99)
    n = 60
    cost = random_cost(rng, n)
    result = solve_tsp_open(cost, 0)
    assert not result.exact
    assert sorted(result.order) == list(range(n))


def test_locked_anchor_segment_keeps_anchor_in_place() -> None:
    """修正 2: a locked anchor partitions the day; the optimizer may not move it."""
    from app.routing.tsp import optimize_day_order

    # 4 places in a deliberately bad order: A(far north), B, C, D... geometry:
    # nodes 0..3 on a line at x=0,1,2,3; anchor at index 2 (x=2).
    ids = ["n0", "n1", "n2", "n3"]
    cost = [
        [0.0, 1.0, 2.0, 3.0],
        [1.0, 0.0, 1.0, 2.0],
        [2.0, 1.0, 0.0, 1.0],
        [3.0, 2.0, 1.0, 0.0],
    ]
    locked = [False, False, True, False]
    has_time = [False, False, False, False]
    new_ids, _costs, exact = optimize_day_order(cost, ids, locked, has_time)
    assert new_ids[2] == "n2"  # the anchor stayed at position 2
    assert sorted(new_ids) == sorted(ids)
    assert exact


def test_schedule_recurrence_is_self_consistent() -> None:
    """start == arrive (no locked time) and arrive == prev.start + prev.duration + travel."""
    ids = ["a", "b", "c"]
    durations = {"a": 60, "b": 45, "c": 90}
    travel = [[0, 10, 20], [10, 0, 15], [20, 15, 0]]
    result = fill_schedule(ids, durations, {"a": None, "b": None, "c": None}, travel, day_start_min=540)
    first = result.places[0]
    assert first.start_min == 540 and first.arrive_min == 540 and first.travel_min_before == 0
    second = result.places[1]
    assert second.travel_min_before == 10
    assert second.arrive_min == first.start_min + durations["a"] + 10
    assert second.start_min == second.arrive_min
    third = result.places[2]
    assert third.arrive_min == second.start_min + durations["b"] + 15
    assert result.end_min == third.start_min + durations["c"]


def test_schedule_locked_time_resets_cursor_and_warns_on_conflict() -> None:
    ids = ["a", "b", "c"]
    durations = {"a": 60, "b": 30, "c": 30}
    travel = [[0, 30, 30], [30, 0, 10], [30, 10, 0]]
    # b demands 08:00 but the earliest arrival is 10:30 -> conflict warning, cursor resets.
    result = fill_schedule(
        ids,
        durations,
        {"a": None, "b": 8 * 60, "c": None},
        travel,
        day_start_min=9 * 60,
    )
    b = result.places[1]
    assert b.start_min == 8 * 60
    assert any("早于预计到达" in w for w in result.warnings)
    # The NEXT place is scheduled from the reset cursor, not from the overrun.
    c = result.places[2]
    assert c.arrive_min == 8 * 60 + durations["b"] + 10


def test_schedule_late_night_warning() -> None:
    ids = ["a", "b"]
    durations = {"a": 600, "b": 600}
    travel = [[0, 120], [120, 0]]
    result = fill_schedule(ids, durations, {"a": None, "b": None}, travel, day_start_min=600)
    assert any("才结束" in w for w in result.warnings)
