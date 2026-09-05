from __future__ import annotations

import time

from fastapi import APIRouter

from app.amap.client import get_amap_client
from app.amap.health import full_self_check
from app.config import START_TIME, settings

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    """Liveness probe. Must stay fast: no Amap calls, no DB queries."""
    return {
        "ok": True,
        "version": settings.version,
        "uptime_s": round(time.monotonic() - START_TIME, 1),
    }


@router.get("/api/amap/health")
async def amap_health() -> dict:
    """The M1 gate: one live /v3/distance probe plus config-presence checks.

    Costs exactly one Amap call per request, so it is never called on a timer.
    """
    report = await full_self_check(get_amap_client())
    return report.to_dict()
