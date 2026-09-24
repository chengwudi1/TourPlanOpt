"""Trip and place REST endpoints.

M2 is REST-only on purpose: it proves the schema, the repositories and the POI proxy
before any concurrency exists. From M4 the WebSocket handlers call these same
repository functions, so there is one implementation of every mutation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, status

from app.amap.client import fetch_photo_best_effort
from app.auth.routes_auth import current_user
from app.config import settings
from app.db.database import get_db
from app.db.repositories import (
    add_place,
    clamp_trip_days,
    create_trip,
    delete_place,
    get_snapshot,
    get_trip_summaries,
    next_seq,
    update_trip,
    upsert_participant,
)
from app.models.domain import (
    ParticipantOut,
    ParticipantUpsert,
    PlaceCreate,
    PlaceOut,
    Snapshot,
    TripCreate,
    TripCreateResult,
    TripOut,
    TripPatch,
    TripSummaryList,
)
from app.uploads import CoverReject, drop_cover_file, save_cover, sweep_covers

router = APIRouter(prefix="/api/trips", tags=["trips"])

# 首页仪表盘一次最多要 24 张卡：超出部分直接忽略，而不是回 400 —— 前端数不准自己
# 本地攒了多少条，让它少画几张比让它处理一个失败请求便宜得多。
SUMMARY_MAX_IDS = 24


def parse_summary_ids(raw: str | None) -> list[str]:
    """`?ids=a, b,,a` -> ['a', 'b']: trimmed, empties dropped, deduped in first-seen
    order, capped at SUMMARY_MAX_IDS."""
    if not raw:
        return []
    ordered: dict[str, None] = {}  # a dict is the ordered set this needs no more of
    for chunk in raw.split(","):
        trip_id = chunk.strip()
        if trip_id:
            ordered.setdefault(trip_id, None)
        if len(ordered) >= SUMMARY_MAX_IDS:
            break
    return list(ordered)


def share_url(request: Request, trip_id: str) -> str:
    """Built from Origin so the link points at whatever host the user actually typed.

    Behind the Vite dev proxy `request.base_url` is the *backend* origin, and on the LAN
    it would be 127.0.0.1 -- a link a friend cannot open. Browsers send Origin on every
    POST, so it is the honest base.
    """
    base = request.headers.get("origin") or str(request.base_url)
    return f"{base.rstrip('/')}/trip/{trip_id}"


@router.post("", response_model=TripCreateResult, status_code=status.HTTP_201_CREATED)
async def create_trip_endpoint(body: TripCreate, request: Request) -> TripCreateResult:
    user = await current_user(request)
    trip_id, day_id = await create_trip(
        get_db(),
        title=body.title.strip(),
        city=body.city.strip(),
        travel_mode=body.travel_mode,
        created_by=user["id"] if user else None,
        days=body.days,
        start_date=body.start_date,
        day_start_min=body.day_start_min,
    )
    return TripCreateResult(
        trip_id=trip_id,
        day_id=day_id,
        day_count=clamp_trip_days(body.days),
        share_url=share_url(request, trip_id),
    )


@router.get("/summary", response_model=TripSummaryList)
async def read_trip_summary(ids: str | None = Query(default=None)) -> TripSummaryList:
    """Batched card data for the home dashboard: N trips in one request, and strictly
    read-only -- unlike `read_trip` below it never calls `accounts.record_visit`, so
    displaying a wall of cards cannot rewrite 「我的活动」 ordering as a side effect of
    looking at it, and it never touches `trips.seq`. Unknown ids are omitted rather than
    404'd, because the frontend prunes the stale local records that no longer come back.

    路由顺序是契约的一部分：本条必须声明在 ``/{trip_id}`` **之前**。FastAPI 按声明顺序
    匹配，挪到下面 "summary" 就会被当成一个行程 id，这个接口会静默变成 404。
    """
    trip_ids = parse_summary_ids(ids)
    trips = await get_trip_summaries(get_db(), trip_ids) if trip_ids else []
    return TripSummaryList(trips=trips)


@router.get("/{trip_id}", response_model=Snapshot)
async def read_trip(trip_id: str, request: Request) -> Snapshot:
    """The full snapshot over plain HTTP. Deliberately not socket-dependent: this is the
    debug path and the fallback if the WebSocket never comes up."""
    snapshot = await get_snapshot(get_db(), trip_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这份行程不存在，可能已被删除")
    # 历史足迹: opening a trip while logged in records the visit for 我的活动 feed.
    user = await current_user(request)
    if user is not None:
        from app.auth import accounts

        await accounts.record_visit(get_db(), trip_id, user["id"])
    return snapshot


async def _broadcast_trip_updated(db, trip_id: str, updated: TripOut) -> None:
    """写完必须朝房间广播一条 `trip_updated`，否则同时开着的行程页会停在旧状态，而且它下次
    自己发 `trip_update` 时按 LWW 会把刚写下的值盖回去——那边根本不知道有人改过。
    """
    from app.models.protocol import broadcast_op_frame
    from app.ws.hub import get_hub

    hub = get_hub().peek(trip_id)
    if hub is None:
        return
    seq = await next_seq(db, trip_id)
    hub.broadcast(
        broadcast_op_frame(
            seq,
            "trip_updated",
            origin="server",
            op_id=f"patch-{trip_id}-{seq}",
            data={"trip": updated.model_dump(mode="json")},
        )
    )


@router.patch("/{trip_id}", response_model=TripOut)
async def patch_trip(trip_id: str, body: TripPatch) -> TripOut:
    """HTTP 侧改行程：首页没有 WebSocket，「标记完成 / 归档 / 设预算」只能走这条路。

    `exclude_unset` 是必要的：``{"city": null}`` 是一次真实的清空意图，而字段缺席意味着
    「这一项别碰」，两者不能都读成「改成空」。
    """
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有要修改的内容")
    db = get_db()
    if await db.fetch_one("SELECT id FROM trips WHERE id = ?", (trip_id,)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这份行程不存在，可能已被删除")
    updated = await update_trip(db, trip_id, patch)
    if updated is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "提交的内容无效")
    await _broadcast_trip_updated(db, trip_id, updated)
    return updated


@router.post("/{trip_id}/cover", response_model=TripOut)
async def upload_cover(trip_id: str, file: UploadFile) -> TripOut:
    """M36 本机上传封面：落文件 + 写 `trips.cover_url` + 广播，一步做完。

    分两步（先上传拿 URL、再由前端发 op 写库）会留下「文件已经在磁盘上却没人引用」的半截
    状态，而失败的那一次没人负责回收。被换下的旧图由 `update_trip` 在写库成功后删（三个
    写入口共用那一处）；这里只负责本次上传自己写出来、却没能落库的那一个。
    """
    db = get_db()
    if await db.fetch_one("SELECT id FROM trips WHERE id = ?", (trip_id,)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这份行程不存在，可能已被删除")
    try:
        url = await save_cover(trip_id, await file.read())
    except CoverReject as exc:
        limit_mb = settings.cover_max_bytes // (1024 * 1024)
        code, message = {
            "type": (
                status.HTTP_400_BAD_REQUEST,
                "不是可识别的图片文件，支持 JPEG、PNG 与 WebP",
            ),
            "too_large": (
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"图片超过 {limit_mb} MB",
            ),
            "id": (status.HTTP_404_NOT_FOUND, "这份行程不存在，可能已被删除"),
        }[exc.kind]
        raise HTTPException(code, message) from exc
    updated = await update_trip(db, trip_id, {"cover_url": url})
    if updated is None:
        # 走到这里 patch 一定合法（值是自己刚生成的 /uploads/ 路径），留这一手只为不静默吞掉失败。
        drop_cover_file(url, trip_id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "提交的内容无效")
    await sweep_covers(trip_id, url)
    await _broadcast_trip_updated(db, trip_id, updated)
    return updated


@router.post("/{trip_id}/participants", response_model=ParticipantOut)
async def register_participant(trip_id: str, body: ParticipantUpsert) -> ParticipantOut:
    if await get_snapshot(get_db(), trip_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这份行程不存在，可能已被删除")
    return await upsert_participant(
        get_db(), trip_id, body.client_id, body.name.strip(), body.color
    )


@router.post(
    "/{trip_id}/days/{day_id}/places",
    response_model=PlaceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_place_endpoint(trip_id: str, day_id: str, body: PlaceCreate) -> PlaceOut:
    # 与 WS 路径同一套照片补抓：搜索联想不带图，落库前用 POI id 换一张。
    if not body.photo_url and body.amap_poi_id:
        body = body.model_copy(
            update={"photo_url": await fetch_photo_best_effort(body.amap_poi_id)}
        )
    place = await add_place(get_db(), day_id, body)
    if place is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这一天不存在，可能已被删除")
    return place


@router.delete("/{trip_id}/places/{place_id}")
async def delete_place_endpoint(trip_id: str, place_id: str) -> dict:
    result = await delete_place(get_db(), place_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这个地点不存在，可能已被删除")
    day_id, place_ids = result
    return {"place_id": place_id, "day_id": day_id, "place_ids": place_ids}
