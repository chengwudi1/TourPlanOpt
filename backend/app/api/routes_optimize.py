"""Distance-matrix, optimization and cache endpoints.

The matrix endpoint is the quota observability window: it reports how many live Amap
calls it made versus how many cache entries served the request. cost_model=haversine
makes zero calls, so the whole optimization pipeline is verifiable without keys.

optimize is an HTTP endpoint (not a WS op) because it can take seconds on a cold
cache; the RESULT is broadcast over the WebSocket as `route_optimized` so every
connected client converges, with `prev_place_ids` for one-click undo.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.amap.cache import get_distance_cache
from app.amap.client import get_amap_client
from app.db import repositories
from app.db.database import get_db
from app.models.domain import CostModel, MatrixOut, TravelMode
from app.routing.matrix import build_matrix
from app.routing.schedule import fill_schedule
from app.routing.tsp import optimize_day_order

router = APIRouter(tags=["optimize"])


class OptimizeRequest(BaseModel):
    cost_model: CostModel = CostModel.HAVERSINE
    mode: TravelMode = TravelMode.DRIVING
    apply: bool = True


class OptimizeSummary(BaseModel):
    before_min: int
    after_min: int
    saved_min: int


class OptimizeResult(BaseModel):
    day_id: str
    place_ids: list[str]
    prev_place_ids: list[str]
    summary: OptimizeSummary
    places: list[dict]
    warnings: list[str] = Field(default_factory=list)
    exact: bool = True


@router.get("/api/trips/{trip_id}/days/{day_id}/matrix", response_model=MatrixOut)
async def day_matrix(
    trip_id: str,
    day_id: str,
    mode: Annotated[TravelMode, Query()] = TravelMode.DRIVING,
    cost_model: Annotated[CostModel, Query()] = CostModel.HAVERSINE,
) -> MatrixOut:
    await get_day_checked(trip_id, day_id)
    rows = await day_places(day_id)
    place_ids = [row["id"] for row in rows]
    nodes = [(row["lng"], row["lat"]) for row in rows]

    result = await build_matrix(
        nodes,
        travel_mode=mode,
        cost_model=str(cost_model),
        cache=get_distance_cache(),
        client=get_amap_client(),
    )
    return MatrixOut(
        place_ids=place_ids,
        seconds=result.seconds,
        api_calls=result.api_calls,
        cache_hits=result.cache_hits,
        mode_used=result.mode_used,
        fallback_from=result.fallback_from,
        warnings=result.warnings,
        unreachable_pairs=result.unreachable_pairs,
    )


@router.post(
    "/api/trips/{trip_id}/days/{day_id}/optimize",
    response_model=OptimizeResult,
)
async def optimize_day(trip_id: str, day_id: str, body: OptimizeRequest) -> OptimizeResult:
    day = await get_day_checked(trip_id, day_id)
    places = await repositories.get_db_places(get_db(), day_id)
    if len(places) < 3:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"至少 3 个地点才值得优化（当前 {len(places)} 个）"
        )

    place_ids = [p.id for p in places]
    nodes = [(p.lng, p.lat) for p in places]
    durations = {p.id: p.duration_min for p in places}
    locked_times = {p.id: p.start_min for p in places}

    db = get_db()
    matrix = await build_matrix(
        nodes,
        travel_mode=body.mode,
        cost_model=str(body.cost_model),
        cache=get_distance_cache(),
        client=get_amap_client(),
    )

    cost = matrix.seconds
    locked_flags = [p.locked for p in places]
    has_time = [p.start_min is not None for p in places]
    new_ids, _seg_costs, any_exact = optimize_day_order(cost, place_ids, locked_flags, has_time)

    def order_cost(ids: list[str]) -> int:
        index = {pid: i for i, pid in enumerate(place_ids)}
        total = 0
        for a, b in zip(ids, ids[1:], strict=False):
            leg = cost[index[a]][index[b]]
            if leg is not None:
                total += int(leg) // 60
        return total

    before_min = order_cost(place_ids)
    after_min = order_cost(new_ids)

    warnings = list(matrix.warnings)

    # Travel legs along the NEW order, position-indexed, in whole minutes.
    index = {pid: i for i, pid in enumerate(place_ids)}
    leg_matrix = [
        [
            (None if cost[index[a]][index[b]] is None else int(cost[index[a]][index[b]]) // 60)
            for b in new_ids
        ]
        for a in new_ids
    ]
    schedule = fill_schedule(
        new_ids,
        durations,
        locked_times,
        leg_matrix,
        day_start_min=day.start_min or 540,
    )
    warnings.extend(schedule.warnings)

    if body.apply:
        reorder = await repositories.reorder_day(db, day_id, new_ids)
        if not reorder.ok:  # pragma: no cover - new_ids is a permutation by construction
            raise HTTPException(status.HTTP_409_CONFLICT, "顺序在优化期间被并发修改，请重试")
        await repositories.persist_schedule(
            db,
            [(s.place_id, s.travel_min_before, s.arrive_min, s.start_min) for s in schedule.places],
        )

    updated = await repositories.get_db_places(db, day_id)
    updated_by_id = {p.id: p for p in updated}

    result = OptimizeResult(
        day_id=day_id,
        place_ids=new_ids,
        prev_place_ids=place_ids,
        summary=OptimizeSummary(
            before_min=before_min, after_min=after_min, saved_min=max(0, before_min - after_min)
        ),
        places=[
            updated_by_id[pid].model_dump(mode="json")
            for pid in new_ids
            if pid in updated_by_id
        ],
        warnings=warnings,
        exact=any_exact,
    )

    if body.apply:
        # Everyone in the room converges, including clients that never called the
        # endpoint. The caller applies the HTTP response directly.
        from app.models.protocol import broadcast_op_frame
        from app.ws.hub import get_hub

        hub = get_hub().get(trip_id)
        seq = await repositories.next_seq(db, trip_id)
        frame = broadcast_op_frame(
            seq,
            "route_optimized",
            origin="server",
            op_id=f"optimize-{day_id}-{seq}",
            data=result.model_dump(mode="json"),
        )
        hub.broadcast(frame)

    return result


async def get_day_checked(trip_id: str, day_id: str):
    day = await repositories.get_day(get_db(), day_id)
    if day is None or day.trip_id != trip_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"行程 {trip_id} 下没有第 {day_id} 天")
    return day


async def day_places(day_id: str):
    return await get_db().run(
        lambda conn: conn.execute(
            "SELECT id, lng, lat FROM places WHERE day_id = ? ORDER BY sort_index",
            (day_id,),
        ).fetchall()
    )


@router.delete("/api/cache/distance")
async def clear_distance_cache() -> dict:
    cleared = await get_distance_cache().clear()
    return {"cleared": cleared}
