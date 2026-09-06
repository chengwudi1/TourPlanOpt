"""POI search proxy.

These exist because the Web服务 key must NEVER reach the browser. Proxying also gives one
place to normalise Amap's response shape and turn infocodes into readable Chinese.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.amap.client import get_amap_client
from app.models.domain import PoiOut

router = APIRouter(prefix="/api/poi", tags=["poi"])


class PoiSearchResponse(BaseModel):
    count: int
    pois: list[PoiOut]


class PoiTipsResponse(BaseModel):
    tips: list[PoiOut]


@router.get("/search", response_model=PoiSearchResponse)
async def search_places(
    keyword: str = Query(min_length=1, max_length=80),
    city: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1, le=100),
) -> PoiSearchResponse:
    """Full-text POI search (/v3/place/text). For a deliberate search."""
    result = await get_amap_client().place_text(keyword.strip(), city, page=page)
    return PoiSearchResponse(
        count=result.count,
        pois=[PoiOut.model_validate(poi) for poi in result.pois],
    )


@router.get("/tips", response_model=PoiTipsResponse)
async def search_tips(
    keyword: str = Query(min_length=1, max_length=80),
    city: str | None = Query(default=None, max_length=40),
) -> PoiTipsResponse:
    """Autocomplete (/v3/assistant/inputtips). Cheaper and faster than /search, which is
    why the debounced input box uses this one."""
    tips = await get_amap_client().inputtips(keyword.strip(), city)
    return PoiTipsResponse(tips=[PoiOut.model_validate(tip) for tip in tips])


@router.get("/regeo")
async def regeo(lng: float = Query(...), lat: float = Query(...)) -> dict:
    """坐标 -> 地址与最近 POI 名。地图选点添加时预填名称/地址用。"""
    result = await get_amap_client().regeo(lng, lat)
    return result
