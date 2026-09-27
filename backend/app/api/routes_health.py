from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Query

from app.amap.client import get_amap_client
from app.amap.health import full_self_check
from app.config import START_TIME, settings

router = APIRouter(tags=["health"])

# 高德探活是要花钱的：每放行一次就消耗一发 /v3/distance 调用。上了公网，
# 每个陌生访客首次进页面都会各打一发，而这个端点又是无鉴权的公开 GET、可被直接刷。
# 所以结果全局缓存，TTL 窗口内整站共用同一份；探活全程持锁，挡住「冷缓存被 N 个并发
# 请求同时打穿」——那会退化成 N 发调用。前端「重新检查」带 ?fresh=1 绕缓存，但 fresh
# 另有更小的下限间隔，免得有人拿这个参数当刷量口子。
_AMAP_HEALTH_TTL_S = 300.0
_AMAP_FRESH_MIN_INTERVAL_S = 60.0
_amap_health_cache: tuple[float, dict] | None = None
_amap_health_lock = asyncio.Lock()


@router.get("/api/health")
async def health() -> dict:
    """Liveness probe. Must stay fast: no Amap calls, no DB queries."""
    return {
        "ok": True,
        "version": settings.version,
        "uptime_s": round(time.monotonic() - START_TIME, 1),
    }


@router.get("/api/amap/health")
async def amap_health(fresh: bool = Query(False)) -> dict:
    """The M1 gate: one live /v3/distance probe plus config-presence checks.

    Cached for ``_AMAP_HEALTH_TTL_S`` so a public endpoint can't be used to burn
    the daily Amap quota. ``?fresh=1`` (the manual re-check button) bypasses the
    cache, but is itself floored at ``_AMAP_FRESH_MIN_INTERVAL_S`` between live
    probes.
    """
    global _amap_health_cache
    async with _amap_health_lock:
        now = time.monotonic()
        cached = _amap_health_cache
        if cached:
            age = now - cached[0]
            floor = _AMAP_FRESH_MIN_INTERVAL_S if fresh else _AMAP_HEALTH_TTL_S
            if age < floor:
                return cached[1]
        report = await full_self_check(get_amap_client())
        data = report.to_dict()
        _amap_health_cache = (time.monotonic(), data)
        return data
