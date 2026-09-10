"""Trip and place REST endpoints.

M2 is REST-only on purpose: it proves the schema, the repositories and the POI proxy
before any concurrency exists. From M4 the WebSocket handlers call these same
repository functions, so there is one implementation of every mutation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.amap.client import fetch_photo_best_effort
from app.auth.routes_auth import current_user
from app.db.database import get_db
from app.db.repositories import (
    add_place,
    create_trip,
    delete_place,
    get_snapshot,
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
)

router = APIRouter(prefix="/api/trips", tags=["trips"])


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
    )
    return TripCreateResult(
        trip_id=trip_id, day_id=day_id, share_url=share_url(request, trip_id)
    )


@router.get("/{trip_id}", response_model=Snapshot)
async def read_trip(trip_id: str, request: Request) -> Snapshot:
    """The full snapshot over plain HTTP. Deliberately not socket-dependent: this is the
    debug path and the fallback if the WebSocket never comes up."""
    snapshot = await get_snapshot(get_db(), trip_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"行程 {trip_id} 不存在")
    # 历史足迹: opening a trip while logged in records the visit for 我的活动 feed.
    user = await current_user(request)
    if user is not None:
        from app.auth import accounts

        await accounts.record_visit(get_db(), trip_id, user["id"])
    return snapshot


@router.post("/{trip_id}/participants", response_model=ParticipantOut)
async def register_participant(trip_id: str, body: ParticipantUpsert) -> ParticipantOut:
    if await get_snapshot(get_db(), trip_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"行程 {trip_id} 不存在")
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"第 {day_id} 天不存在")
    return place


@router.delete("/{trip_id}/places/{place_id}")
async def delete_place_endpoint(trip_id: str, place_id: str) -> dict:
    result = await delete_place(get_db(), place_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"地点 {place_id} 不存在")
    day_id, place_ids = result
    return {"place_id": place_id, "day_id": day_id, "place_ids": place_ids}
