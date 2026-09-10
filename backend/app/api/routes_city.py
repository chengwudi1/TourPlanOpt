"""City recommendations: famous sights, food streets, night markets.

Evaluated design decision (user question: jump to Amap vs in-app?): in-app, with an
Amap deep-link per category. In-app results plug straight into 「加入行程」 (the POI
objects are the same shape the search flow uses); quota is controlled by a 24h cache
per (city, category, sort) -- one city visit costs at most a couple of Amap calls per
day regardless of how many people browse it. The deep link covers the "navigate / open
in app" case without us paying for routing data.

Categories map onto /v3/place/text parameters:
- scenic: types 风景名胜;公园 (broad sightseeing sweep)
- food:   types 餐饮服务  (the city's well-known eats)
- night:  keywords 夜市|美食街|小吃街  (night markets / food streets)

Sorts (发现面板的三档，默认综合):
- composite: 不传 sortrule，吃高德默认的「数据精度」序，就是原来的综合结果。
- hot:       sortrule=2。实测只有 2 会换序，所以热度必须真打一次上游，占自己那条缓存。
- distance:  **不打上游**。/v3/place/text 没有中心点，sortrule=1 与不传返回同一份顺序
             （实测），距离只能拿到坐标自己算：复用 composite 那份缓存，按到 origin 的
             直线距离重排并回填 distance_m。所以这一档零配额、零新缓存。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from app.amap.client import get_amap_client
from app.db.database import get_db
from app.models.domain import PoiOut
from app.util.coords import Coord, haversine_m, is_in_china

router = APIRouter(prefix="/api/city", tags=["city"])

CACHE_TTL_HOURS = 24

CATEGORIES: dict[str, dict[str, str]] = {
    "scenic": {"types": "风景名胜;公园;博物馆", "keywords": ""},
    "food": {"types": "餐饮服务", "keywords": ""},
    "night": {"types": "", "keywords": "夜市|美食街|小吃街"},
}

# 会真打上游的排序 -> 高德 sortrule。distance 不在这里：它复用 composite 的结果。
UPSTREAM_SORTS: dict[str, int | None] = {"composite": None, "hot": 2}
SORTS = (*UPSTREAM_SORTS, "distance")


def _amap_uri(city: str, category: str) -> str:
    """Deep link into the Amap app/web search for this category."""
    keyword = {"scenic": "景点", "food": "美食", "night": "夜市"}.get(category, "景点")
    from urllib.parse import quote

    return f"https://uri.amap.com/search?keyword={quote(keyword)}&city={quote(city)}&src=tourplanopt"


def _parse_origin(raw: str | None) -> Coord | None:
    """'lng,lat' -> (lng, lat)。读不懂就当作没给，绝不因为一个脏参数把整个面板打空。"""
    if not raw:
        return None
    parts = raw.replace(" ", "").split(",")
    if len(parts) != 2:
        return None
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return (lng, lat) if is_in_china(lng, lat) else None


def _by_distance(pois: list[dict], origin: Coord) -> list[dict]:
    """按到 origin 的直线距离升序重排，并把米数回填进 distance_m。

    副本操作：传进来的是缓存里那份 dict，就地改会污染本次请求之外的东西。
    """
    rows: list[tuple[float | None, dict]] = []
    for poi in pois:
        try:
            coord = (float(poi["lng"]), float(poi["lat"]))
        except (KeyError, TypeError, ValueError):
            rows.append((None, {**poi, "distance_m": None}))
            continue
        metres = haversine_m(coord, origin)
        rows.append((metres, {**poi, "distance_m": int(round(metres))}))
    # 没坐标的排到最后而不是混进序列里——它们只是缺字段，不是远。
    rows.sort(key=lambda item: (item[0] is None, item[0] or 0.0))
    return [row for _, row in rows]


@router.get("/recommendations")
async def recommendations(
    city: str = Query(min_length=1, max_length=30),
    category: str = Query(default="scenic"),
    sort: str = Query(default="composite"),
    origin: str | None = Query(default=None, max_length=40),
) -> dict:
    spec = CATEGORIES.get(category)
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知类目：{category}")
    if sort not in SORTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知排序：{sort}")
    city = city.strip()
    if not city:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "先在行程里填目的地城市")

    # distance 只是 composite 那批候选换个排法，上游查询与缓存都按 composite 走。
    upstream = "composite" if sort == "distance" else sort
    point = _parse_origin(origin) if sort == "distance" else None

    db = get_db()
    cached = await _cache_get(db, city, category, upstream)
    if cached is not None:
        pois, is_cached = cached, True
    else:
        page = await get_amap_client().place_text(
            keyword=spec["keywords"] or None,
            city=city,
            page=1,
            page_size=12,
            types=spec["types"] or None,
            sort_rule=UPSTREAM_SORTS[upstream],
        )
        # client POIs are dataclasses; normalise through the wire model.
        pois = [PoiOut.model_validate(poi).model_dump(mode="json") for poi in page.pois]
        await _cache_put(db, city, category, upstream, pois)
        is_cached = False

    if point is not None:
        pois = _by_distance(pois, point)
    return {
        "city": city,
        "category": category,
        "sort": sort,
        "origin": list(point) if point else None,
        "pois": pois,
        "cached": is_cached,
        "amap_url": _amap_uri(city, category),
    }


async def _cache_get(db, city: str, category: str, sort: str) -> list[dict] | None:
    row = await db.fetch_one(
        "SELECT payload, fetched_at FROM city_poi_cache"
        " WHERE city = ? AND category = ? AND sort = ?",
        (city, category, sort),
    )
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    age = datetime.now(UTC) - fetched
    if age > timedelta(hours=CACHE_TTL_HOURS):
        return None
    return json.loads(row["payload"])


async def _cache_put(db, city: str, category: str, sort: str, payload: list[dict]) -> None:
    def _store(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO city_poi_cache (city, category, sort, payload, fetched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(city, category, sort) DO UPDATE SET
                   payload = excluded.payload, fetched_at = excluded.fetched_at""",
            (city, category, sort, json.dumps(payload, ensure_ascii=False), now_iso_utc()),
        )

    await db.run(_store)


def now_iso_utc() -> str:
    return datetime.now(UTC).isoformat()
