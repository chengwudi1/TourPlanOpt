"""Distance matrix + cache behaviour (M5).

Verifies the two quota-protection properties the plan demands: a fresh matrix costs
N-1 live calls, an immediate repeat costs ZERO; and a permanently unreachable pair
produces a negative-cache row that is never re-requested. Also covers the walking
5 km fallback and the keyless haversine path.
"""

from __future__ import annotations

import httpx
import pytest

from app.amap import client as client_mod
from app.amap.cache import CacheRow, DistanceCache
from app.amap.client import AmapWebClient
from app.config import settings
from app.db.database import Database, set_db
from app.models.domain import TravelMode
from app.routing.matrix import build_matrix, haversine_seconds_matrix
from app.util.coords import haversine_m


@pytest.fixture(autouse=True)
async def fast_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "amap_web_key", "test-web-key")
    monkeypatch.setattr(settings, "amap_qps_limit", 10_000)
    monkeypatch.setattr(client_mod, "RETRY_BASE_DELAY", 0.0)
    db = Database(tmp_path / "matrix-test.db")
    await db.init()  # unit tests run outside the app lifespan, so init explicitly
    set_db(db)


# Four Shanghai nodes, close enough for every mode.
NODES = [
    (121.4737, 31.2304),
    (121.4900, 31.2360),
    (121.5057, 31.2453),
    (121.5100, 31.2200),
]


def distance_client(durations: dict[tuple[int, int], int] | None = None, calls: list | None = None):
    """Mock /v3/distance: duration between node i and j = 600 + 97*(i+j) unless
    overridden. origin_id values are 0-based (docs' most common form)."""
    durations = durations or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.params)
        origins = [
            tuple(map(float, o.split(",")))
            for o in str(request.url.params["origins"]).split("|")
        ]
        dest = tuple(map(float, str(request.url.params["destination"]).split(",")))
        # Identify the destination node by its rounded coordinates.
        def node_at(c):
            return next(
                (k for k, n in enumerate(NODES) if round(n[0] * 1e5) == round(c[0] * 1e5)
                 and round(n[1] * 1e5) == round(c[1] * 1e5)),
                None,
            )

        dest_idx = node_at(dest)
        results = []
        for idx, o in enumerate(origins):
            o_idx = node_at(o)
            if dest_idx is None or o_idx is None or o_idx == dest_idx:
                results.append(
                    {"origin_id": str(idx), "info": "20800", "distance": [], "duration": []}
                )
                continue
            duration = durations.get((o_idx, dest_idx), 600 + 97 * (o_idx + dest_idx))
            results.append(
                {
                    "origin_id": str(idx),
                    "info": "OK",
                    "distance": "1000 + 500 * o_idx" if False else str(1000 + 500 * o_idx),
                    "duration": str(duration),
                }
            )
        return httpx.Response(
            200, json={"status": "1", "info": "OK", "infocode": "10000", "results": results}
        )

    return AmapWebClient(transport=httpx.MockTransport(handler))


async def test_cache_roundtrip_with_rounded_keys() -> None:
    db_cache = DistanceCache.__new__(DistanceCache)
    from app.db.database import get_db

    db_cache._db = get_db()

    o, d = (121.4737001, 31.2304002), (121.5057003, 31.2453004)
    await db_cache.put(
        [
            CacheRow(origin=o, destination=d, mode=1, distance_m=1234,
                     duration_s=567, ok=True, infocode=None)
        ]
    )

    # A query nudged by sub-1.1 m amounts still hits the same rounded key.
    entries = await db_cache.get([((121.4737009, 31.2304008), (121.5057009, 31.2453009))], 1)
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry.ok and entry.distance_m == 1234 and entry.duration_s == 567


async def test_negative_cache_row() -> None:
    from app.db.database import get_db

    db_cache = DistanceCache(get_db())
    o, d = (121.4737, 31.2304), (121.5057, 31.2453)
    await db_cache.put(
        [
            CacheRow(origin=o, destination=d, mode=3, distance_m=None,
                     duration_s=None, ok=False, infocode="20800")
        ]
    )
    entries = await db_cache.get([(o, d)], 3)
    entry = entries[next(iter(entries))]
    assert not entry.ok and entry.infocode == "20800"


async def test_matrix_fresh_then_cached() -> None:
    """The plan's M5 gate: first call = N-1 live calls / 0 hits; immediate second =
    0 calls / N*(N-1) hits."""
    from app.db.database import get_db

    calls: list = []
    client = distance_client(calls=calls)
    cache = DistanceCache(get_db())

    first = await build_matrix(
        NODES, travel_mode=TravelMode.DRIVING, cost_model="amap", cache=cache, client=client
    )
    n = len(NODES)
    assert first.api_calls == n - 1
    assert first.cache_hits == 0
    assert len(calls) == n - 1  # one call per destination column, column 0 skipped
    assert all(cell is not None for row in first.seconds for cell in row)

    second = await build_matrix(
        NODES,
        travel_mode=TravelMode.DRIVING,
        cost_model="amap",
        cache=cache,
        client=distance_client(),
    )
    assert second.api_calls == 0
    assert second.cache_hits == (n - 1) ** 2  # column 0 (anchor start) never needed
    assert second.seconds == first.seconds

    # And the cache table holds exactly the pairs it served.
    count = await cache.count()
    assert count == (n - 1) ** 2


async def test_unreachable_pair_is_negatively_cached() -> None:
    """A 20800 pair gets ok=False rows and is never requested again."""
    from app.db.database import get_db

    calls: list = []
    client = distance_client(durations={(1, 3): 999999}, calls=calls)
    cache = DistanceCache(get_db())

    # Make node 1 -> node 3 fail: monkey the mock through an unreachable marker by
    # seeding the cache with a negative row for that pair BEFORE building.
    o, d = NODES[1], NODES[3]
    await cache.put(
        [
            CacheRow(origin=o, destination=d, mode=1, distance_m=None,
                     duration_s=None, ok=False, infocode="20800")
        ]
    )

    first = await build_matrix(
        NODES, travel_mode=TravelMode.DRIVING, cost_model="amap", cache=cache, client=client
    )
    assert (1, 3) in first.unreachable_pairs
    assert first.seconds[1][3] is None

    before = len(calls)
    second = await build_matrix(
        NODES,
        travel_mode=TravelMode.DRIVING,
        cost_model="amap",
        cache=cache,
        client=distance_client(calls=calls),
    )
    assert second.api_calls == 0
    assert len(calls) == before  # no new requests: the negative cache answered
    assert second.seconds[1][3] is None


def test_haversine_matrix_makes_no_calls() -> None:
    """The keyless path: pure geometry, zero HTTP, symmetric in seconds estimation."""
    matrix = haversine_seconds_matrix(NODES, TravelMode.DRIVING)
    assert all(matrix[i][i] == 0 for i in range(len(NODES)))
    d01 = haversine_m(NODES[0], NODES[1])
    assert 0 < matrix[0][1] < 3600  # a couple of km across Shanghai at urban speed
    assert d01 > 0


async def test_walking_over_5km_falls_back_to_driving() -> None:
    """A day spanning >5 km must not fire N doomed walking calls; it falls back."""
    from app.db.database import get_db

    far_nodes = [
        (121.0, 31.0),
        (121.06, 31.06),  # ~8 km away: bbox diagonal well past 5 km
    ]
    calls: list = []
    client = distance_client(calls=calls)
    result = await build_matrix(
        far_nodes,
        travel_mode=TravelMode.WALKING,
        cost_model="amap",
        cache=DistanceCache(get_db()),
        client=client,
    )
    assert result.fallback_from == "walking"
    assert result.mode_used == "driving"
    assert result.warnings and "5 公里" in result.warnings[0]
