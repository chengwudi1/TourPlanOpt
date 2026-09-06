"""City recommendations: famous sights, food streets, night markets.

Evaluated design decision (user question: jump to Amap vs in-app?): in-app, with an
Amap deep-link per category. In-app results plug straight into 「加入行程」 (the POI
objects are the same shape the search flow uses); quota is controlled by a 24h cache
per (city, category) -- one city visit costs at most 3 Amap calls per day regardless
of how many people browse it. The deep link covers the "navigate / open in app" case
without us paying for routing data.

Categories map onto /v3/place/text parameters:
- scenic: types 风景名胜;公园 (broad sightseeing sweep)
- food:   types 餐饮服务  (the city's well-known eats)
- night:  keywords 夜市|美食街|小吃街  (night markets / food streets)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from app.amap.client import get_amap_client
from app.db.database import get_db
from app.models.domain import PoiOut

router = APIRouter(prefix="/api/city", tags=["city"])

CACHE_TTL_HOURS = 24

CATEGORIES: dict[str, dict[str, str]] = {
    "scenic": {"types": "风景名胜;公园;博物馆", "keywords": ""},
    "food": {"types": "餐饮服务", "keywords": ""},
    "night": {"types": "", "keywords": "夜市|美食街|小吃街"},
}


def _amap_uri(city: str, category: str) -> str:
    """Deep link into the Amap app/web search for this category."""
    keyword = {"scenic": "景点", "food": "美食", "night": "夜市"}.get(category, "景点")
    from urllib.parse import quote

    return f"https://uri.amap.com/search?keyword={quote(keyword)}&city={quote(city)}&src=tourplanopt"


@router.get("/recommendations")
async def recommendations(
    city: str = Query(min_length=1, max_length=30),
    category: str = Query(default="scenic"),
) -> dict:
    spec = CATEGORIES.get(category)
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知类目：{category}")
    city = city.strip()
    if not city:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "先在行程里填目的地城市")

    db = get_db()
    cached = await _cache_get(db, city, category)
    if cached is not None:
        return {"city": city, "category": category, "pois": cached, "cached": True,
                "amap_url": _amap_uri(city, category)}

    client = get_amap_client()
    page = await client.place_text(
        keyword=spec["keywords"] or None,
        city=city,
        page=1,
        page_size=12,
        types=spec["types"] or None,
    )
    # client POIs are dataclasses; normalise through the wire model.
    payload = [PoiOut.model_validate(poi).model_dump(mode="json") for poi in page.pois]
    await _cache_put(db, city, category, payload)
    return {"city": city, "category": category, "pois": payload, "cached": False,
            "amap_url": _amap_uri(city, category)}


async def _cache_get(db, city: str, category: str) -> list[dict] | None:
    row = await db.fetch_one(
        "SELECT payload, fetched_at FROM city_poi_cache WHERE city = ? AND category = ?",
        (city, category),
    )
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    age = datetime.now(UTC) - fetched
    if age > timedelta(hours=CACHE_TTL_HOURS):
        return None
    return json.loads(row["payload"])


async def _cache_put(db, city: str, category: str, payload: list[dict]) -> None:
    def _store(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO city_poi_cache (city, category, payload, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(city, category) DO UPDATE SET
                   payload = excluded.payload, fetched_at = excluded.fetched_at""",
            (city, category, json.dumps(payload, ensure_ascii=False), now_iso_utc()),
        )

    await db.run(_store)


def now_iso_utc() -> str:
    return datetime.now(UTC).isoformat()
