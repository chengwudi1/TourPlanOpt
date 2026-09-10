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
from app.routing.matrix import build_matrix, permute_matrix
from app.routing.timeline import apply_timeline, trip_defaults
from app.routing.tsp import optimize_day_order

router = APIRouter(tags=["optimize"])


class OptimizeRequest(BaseModel):
    cost_model: CostModel = CostModel.HAVERSINE
    mode: TravelMode | None = None
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
    end_min: int = 0
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

    db = get_db()
    trip_mode, trip_day_start_min = await trip_defaults(db, trip_id)
    # 没显式指定 mode 就跟随这天/这行程的设置：步行行程不该按车速优化。
    mode = body.mode or day.travel_mode or trip_mode

    matrix = await build_matrix(
        nodes,
        travel_mode=mode,
        cost_model=str(body.cost_model),
        cache=get_distance_cache(),
        client=get_amap_client(),
    )

    cost = matrix.seconds
    locked_by_id = {p.id: p.locked for p in places}
    # 锚点只认用户手填的时间。start_min 是排程推导出来的展示值，每次重算都会覆盖，
    # 把它当锚点等于第一次优化之后所有地点都变成钉子，之后的优化全部空转。
    timed_by_id = {p.id: p.user_start_min is not None for p in places}

    # 起点锚（day.start_place_id，卡片菜单「设为起点」设置）：旋到序列首位，优化时
    # leading block 的首节点钉死规则会把它固定为出发地。终点锚（end_place_id）同理
    # 旋到末位，但还得多一层：末位本身要成为锚点，否则开放路径会把酒店挪回中间。
    start_id = day.start_place_id if day.start_place_id in place_ids else None
    end_id = day.end_place_id if day.end_place_id in place_ids else None
    if start_id is not None and end_id == start_id:
        end_id = None  # 同一点两头钉没有意义，按起点处理

    solver_ids = place_ids
    if start_id is not None and solver_ids[0] != start_id:
        idx = solver_ids.index(start_id)
        solver_ids = solver_ids[idx:] + solver_ids[:idx]
    if end_id is not None and solver_ids[-1] != end_id:
        solver_ids = [pid for pid in solver_ids if pid != end_id] + [end_id]

    # cost 按节点下标存，重排过的那套 ids 只有配上同样重排的 cost 才对得齐。
    solver_cost = permute_matrix(cost, place_ids, solver_ids)
    new_ids, _seg_costs, any_exact = optimize_day_order(
        solver_cost,
        solver_ids,
        [locked_by_id[pid] or pid == end_id for pid in solver_ids],
        [timed_by_id[pid] for pid in solver_ids],
    )

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

    # Travel legs along the NEW order, position-indexed, in whole minutes.
    legs_min = [
        [None if leg is None else int(leg) // 60 for leg in row]
        for row in permute_matrix(cost, place_ids, new_ids)
    ]

    if body.apply:
        reorder = await repositories.reorder_day(db, day_id, new_ids)
        if not reorder.ok:  # pragma: no cover - new_ids is a permutation by construction
            raise HTTPException(status.HTTP_409_CONFLICT, "顺序在优化期间被并发修改，请重试")

    place_by_id = {p.id: p for p in places}
    timeline = await apply_timeline(
        db,
        day=day,
        ordered=[place_by_id[pid] for pid in new_ids],
        legs_min=legs_min,
        day_start_min=day.start_min or trip_day_start_min,
        warnings=list(matrix.warnings),
        exact=any_exact,
        persist=body.apply,
    )

    result = OptimizeResult(
        day_id=day_id,
        place_ids=new_ids,
        prev_place_ids=place_ids,
        summary=OptimizeSummary(
            before_min=before_min, after_min=after_min, saved_min=max(0, before_min - after_min)
        ),
        places=[p.model_dump(mode="json") for p in timeline.places],
        end_min=timeline.end_min,
        warnings=timeline.warnings,
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
