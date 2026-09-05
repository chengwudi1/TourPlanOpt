"""Boot config handed to the browser.

The Web服务 key is deliberately absent: it must never reach the client. Only the
JS API key and its security code are exposed, and only through this one route.

The plaintext securityJsCode is an accepted localhost trade-off, structurally
contained here. Switching to Amap's recommended ``serviceHost`` proxy for any
non-localhost deployment is a change to this single function.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["config"])

JSAPI_PLUGINS = [
    "AMap.AutoComplete",
    "AMap.PlaceSearch",
    "AMap.Geolocation",
    "AMap.Scale",
]


def amap_frontend_config() -> dict:
    return {
        "js_key": settings.amap_js_key,
        "scode": settings.amap_js_scode,
        "jsapi_version": settings.amap_jsapi_version,
        "plugins": JSAPI_PLUGINS,
    }


@router.get("/api/config")
async def get_config() -> dict:
    return {
        "amap": amap_frontend_config(),
        "features": {
            "cost_models": ["haversine", "amap"],
            "travel_modes": ["driving", "walking", "straight"],
        },
    }
