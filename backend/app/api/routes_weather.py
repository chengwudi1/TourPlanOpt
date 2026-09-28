"""日头天气：一座城市一次请求，换四天预报。

三条不写下来就会被违反的纪律：

1. **城市名必须先换成 adcode。** /v3/weather/weatherInfo 的 city 只认 6 位 adcode，
   填「成都」会拿到 status=1 / info=OK 而 forecasts 为空——和「权限没开通」长得
   一模一样。所以这一层单独缓存（行政区划几乎不变，30 天）。
2. **按城市缓存，不按天。** 一次调用就给整座城市未来 4 天，一趟 5 天的行程共用这一份。
   预报几小时才刷一次，所以命中给 6 小时。
3. **拿不到就什么也不画。** 天气是锦上添花，绝不因为高德限流/超时把行程页打成错误：
   失败降级成空 casts + reason，前端凭 reason 决定要不要说一句。失败本身也缓存，
   但只顶 5 分钟（ok=0 行）——一次偶发的 CUQPS 不该把这座城市冻死半天，而没缓存
   又叫人每次开页重问一遍。鉴权类失败例外：那是配置状态，不写缓存，改了 .env 立刻生效。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from app.amap.client import WeatherCast, get_amap_client
from app.amap.errors import AmapAuthError, AmapError
from app.db.database import get_db
from app.util.timefmt import now_iso

router = APIRouter(prefix="/api/weather", tags=["weather"])

ADCODE_TTL = timedelta(days=30)
ADCODE_MISS_TTL = timedelta(hours=1)
CAST_TTL = timedelta(hours=6)
CAST_MISS_TTL = timedelta(minutes=5)

# 冷缓存被同房间的几个人同时撞开时，只让第一个去打上游，其余等结果。整站一把锁足够：
# 上游本来就被 client 的令牌桶按秒排过队，这里的串行只挡住「同一座城市同时问三次」。
_gate = asyncio.Lock()

# 缓存里没有的每一发都是真配额，而这个接口没有鉴权：公网上的扫描器完全可以拿随机
# 字符串把本服务当成免费的高德代理（每一个新城市名都换一次未命中）。所以未命中另有预算
# ——60 秒窗口内最多 20 发，超了直接降级成 reason=upstream，不打上游。
# 20 发够一整屋子人同时打开各自的行程（同城只花一发），又掐死了无脑刷。
_MISS_WINDOW_S = 60.0
_MISS_BUDGET = 20
_misses: deque[float] = deque()


def _budget_allows() -> bool:
    """花掉一发未命中预算；花不出去就说明这一分钟已经被刷满。"""
    now = time.monotonic()
    while _misses and now - _misses[0] > _MISS_WINDOW_S:
        _misses.popleft()
    if len(_misses) >= _MISS_BUDGET:
        return False
    _misses.append(now)
    return True


@router.get("/forecast")
async def forecast(city: str = Query(min_length=1, max_length=40)) -> dict:
    """这座城市的 4 天预报。`reason` 为空即有 casts；非空说明为什么没有。"""
    city = city.strip()
    if not city:
        return {"city": city, "adcode": "", "casts": [], "cached": False, "reason": "no_city"}

    db = get_db()
    adcode, cached, reason = await _adcode_for(db, city)
    if adcode == "":
        return {"city": city, "adcode": "", "casts": [], "cached": cached, "reason": reason}

    casts, hit, reason = await _casts_for(db, adcode)
    return {
        "city": city,
        "adcode": adcode,
        "casts": [asdict(c) for c in casts],
        "cached": hit,
        "reason": reason,
    }


async def _adcode_for(db, city: str) -> tuple[str, bool, str]:
    """(adcode, 是否命中缓存, reason)。adcode 为空串时 reason 说明为什么。"""
    row = await db.fetch_one(
        "SELECT adcode, ok, fetched_at FROM city_adcode_cache WHERE city = ?", (city,)
    )
    entry = _usable(row, ADCODE_TTL, ADCODE_MISS_TTL)
    if entry is not None:
        adcode = str(entry["adcode"])
        return adcode, True, "" if adcode else "not_found"

    async with _gate:
        # 等锁期间别人可能已经把这一格填上了——读的是缓存，不重问上游。
        row = await db.fetch_one(
            "SELECT adcode, ok, fetched_at FROM city_adcode_cache WHERE city = ?", (city,)
        )
        entry = _usable(row, ADCODE_TTL, ADCODE_MISS_TTL)
        if entry is not None:
            adcode = str(entry["adcode"])
            return adcode, True, "" if adcode else "not_found"

        # 走到这里就是要真打上游了：预算在调用**之前**扣，哪怕这一发随后失败/超时，
        # 配额也确实花掉了，记这一笔不算冤枉。
        if not _budget_allows():
            # 刷满了也不写缓存：这一格什么也没学到，下一分钟自然恢复。
            return "", False, "upstream"
        try:
            adcode = await get_amap_client().district_adcode(city)
        except AmapAuthError:
            # 没配 Key / Key 不合法是配置状态，不是这座城市的属性：不写缓存，
            # 改了 .env 重启立刻生效，不必再等一格过期。
            return "", False, "upstream"
        except AmapError:
            await _put_adcode(db, city, "", 0)
            return "", False, "upstream"
        await _put_adcode(db, city, adcode, 1 if adcode else 0)
        return adcode, False, "" if adcode else "not_found"


async def _casts_for(db, adcode: str) -> tuple[list[WeatherCast], bool, str]:
    row = await db.fetch_one(
        "SELECT payload, ok, fetched_at FROM weather_cache WHERE adcode = ?", (adcode,)
    )
    entry = _usable(row, CAST_TTL, CAST_MISS_TTL)
    if entry is not None:
        return _load(entry["payload"]), True, "" if entry["ok"] else "upstream"

    async with _gate:
        row = await db.fetch_one(
            "SELECT payload, ok, fetched_at FROM weather_cache WHERE adcode = ?", (adcode,)
        )
        entry = _usable(row, CAST_TTL, CAST_MISS_TTL)
        if entry is not None:
            return _load(entry["payload"]), True, "" if entry["ok"] else "upstream"

        # 走到这里就是要真打上游了：预算在调用**之前**扣，哪怕这一发随后失败/超时，
        # 配额也确实花掉了，记这一笔不算冤枉。
        if not _budget_allows():
            return [], False, "upstream"
        try:
            casts = await get_amap_client().weather_forecast(adcode)
        except AmapAuthError:
            return [], False, "upstream"
        except AmapError:
            await _put_casts(db, adcode, [], 0)
            return [], False, "upstream"
        # 空 casts 不是成功：adcode 有效时高德一定给 4 天，给空说明这一格问法不对，
        # 按失败记账（5 分钟后重试），而不是把「今日无天气」冻成 6 小时的事实。
        await _put_casts(db, adcode, casts, 1 if casts else 0)
        return casts, False, "" if casts else "upstream"


def _usable(row, hit_ttl: timedelta, miss_ttl: timedelta) -> sqlite3.Row | None:
    """没过期的缓存行原样返回，过期的当作没有。

    fetched_at 全是 now_iso() 写的 UTC 串（同格式同偏移），字典序即时间序，
    所以这里比字符串而不是解析日期。ok=0 走更短的那档 TTL。
    """
    if row is None:
        return None
    ttl = hit_ttl if row["ok"] else miss_ttl
    fetched = datetime.fromisoformat(str(row["fetched_at"]))
    if datetime.now(UTC) - fetched > ttl:
        return None
    return row


def _load(payload: object) -> list[WeatherCast]:
    rows = json.loads(str(payload or "[]"))
    return [
        WeatherCast(
            date=str(r.get("date", "")),
            week=str(r.get("week", "")),
            day_weather=str(r.get("day_weather", "")),
            night_weather=str(r.get("night_weather", "")),
            day_temp=r.get("day_temp"),
            night_temp=r.get("night_temp"),
            day_wind=str(r.get("day_wind", "")),
            day_power=str(r.get("day_power", "")),
        )
        for r in rows
        if isinstance(r, dict) and r.get("date")
    ]


async def _put_adcode(db, city: str, adcode: str, ok: int) -> None:
    def _store(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO city_adcode_cache (city, adcode, ok, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(city) DO UPDATE SET
                   adcode = excluded.adcode, ok = excluded.ok, fetched_at = excluded.fetched_at""",
            (city, adcode, ok, now_iso()),
        )

    await db.run(_store)


async def _put_casts(db, adcode: str, casts: list[WeatherCast], ok: int) -> None:
    def _store(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO weather_cache (adcode, payload, ok, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(adcode) DO UPDATE SET
                   payload = excluded.payload, ok = excluded.ok, fetched_at = excluded.fetched_at""",
            (
                adcode,
                json.dumps([asdict(c) for c in casts], ensure_ascii=False),
                ok,
                now_iso(),
            ),
        )

    await db.run(_store)


__all__ = ["router"]
