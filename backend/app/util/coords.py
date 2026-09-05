"""Coordinate utilities.

The GCJ-02 invariant: everything in this project is already GCJ-02 (Amap native), so
NO conversion happens between map, DB and API. The single exception would be
navigator.geolocation (WGS-84); when that lands in a later milestone,
``wgs84_to_gcj02`` is the ONLY place a transform may go, guarded by ``is_in_china()``
(the offset is undefined outside the Chinese grid).
"""

from __future__ import annotations

import math

Coord = tuple[float, float]

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: Coord, b: Coord) -> float:
    """Great-circle distance in metres. The zero-API cost model for TSP scoring."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


# Rough urban speeds for turning a straight-line cost into an ETA. Estimates by
# design -- real road times come from the Amap matrix; this only serves the default
# haversine cost model, where a plausible ordering beats no ordering.
_URBAN_KMH = {"driving": 30.0, "walking": 4.5, "straight": 30.0}


def estimate_seconds(metres: float, mode: str = "driving") -> int:
    kmh = _URBAN_KMH.get(mode, 30.0)
    return int(round(metres / 1000.0 / kmh * 3600.0))


def is_in_china(lng: float, lat: float) -> bool:
    """Coarse Chinese bounding box. Good enough as a transform guard."""
    return 73.0 <= lng <= 136.0 and 17.0 <= lat <= 54.0


def wgs84_to_gcj02(lng: float, lat: float) -> Coord:
    """WGS-84 -> GCJ-02. UNUSED until geolocation support lands; deliberately the only
    transform in the project. Outside China the offset is undefined -> identity."""
    if not is_in_china(lng, lat):
        return lng, lat

    a = 6378245.0  # semi-major axis of the克拉索夫斯基 ellipsoid
    ee = 0.00669342162296594323

    def _t_lat(x: float, y: float) -> float:
        ret = (
            -100.0
            + 2.0 * x
            + 3.0 * y
            + 0.2 * y * y
            + 0.1 * x * y
            + 0.2 * math.sqrt(abs(x))
        )
        ret += (
            20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)
        ) * 2.0 / 3.0
        ret += (
            20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)
        ) * 2.0 / 3.0
        ret += (
            160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)
        ) * 2.0 / 3.0
        return ret

    def _t_lng(x: float, y: float) -> float:
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (
            20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)
        ) * 2.0 / 3.0
        ret += (
            20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)
        ) * 2.0 / 3.0
        ret += (
            150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)
        ) * 2.0 / 3.0
        return ret

    dlat = _t_lat(lng - 105.0, lat - 35.0)
    dlng = _t_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat
