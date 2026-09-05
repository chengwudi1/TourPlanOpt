"""Domain models. These double as the wire protocol's payload shapes.

`frontend/src/types/domain.ts` is a hand-mirror of this file. If you add a field here,
add it there -- tests/test_protocol.py asserts the two stay in sync.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TravelMode(StrEnum):
    """Maps onto the Amap /v3/distance `type` parameter: driving=1, walking=3, straight=0."""

    DRIVING = "driving"
    WALKING = "walking"
    STRAIGHT = "straight"


AMAP_MODE_BY_TRAVEL: dict[TravelMode, int] = {
    TravelMode.STRAIGHT: 0,
    TravelMode.DRIVING: 1,
    TravelMode.WALKING: 3,
}


class CostModel(StrEnum):
    """HAVERSINE costs zero Amap calls; AMAP buys real road distances."""

    HAVERSINE = "haversine"
    AMAP = "amap"


class PlaceStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str = ""
    city: str = ""
    travel_mode: TravelMode = TravelMode.DRIVING
    cost_model: CostModel = CostModel.HAVERSINE
    day_start_min: int = 540
    seq: int = 0
    created_at: str = ""


class DayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    day_index: int
    date: str | None = None
    title: str = ""
    start_place_id: str | None = None
    end_place_id: str | None = None
    start_min: int | None = None
    travel_mode: TravelMode | None = None
    rev: int = 1


class PlaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    day_id: str
    trip_id: str
    sort_index: int
    name: str
    amap_poi_id: str = ""
    address: str = ""
    lng: float
    lat: float
    duration_min: int = 60
    start_min: int | None = None
    arrive_min: int | None = None
    travel_min_before: int | None = None
    locked: bool = False
    status: PlaceStatus = PlaceStatus.PENDING
    note: str = ""
    added_by: str = ""
    rev: int = 1
    created_at: str = ""
    updated_at: str = ""


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: str
    client_id: str
    name: str
    color: str = ""
    joined_at: str = ""
    last_seen: str = ""


class Presence(BaseModel):
    """Who is online *right now*. In-memory only -- never persisted, because a crash
    would otherwise leave zombie rows in the roster."""

    client_id: str
    name: str
    color: str = ""
    current_day_id: str | None = None
    focusing_place_id: str | None = None
    joined_at: float = 0.0


class Snapshot(BaseModel):
    """The whole trip. Small enough (tens of records, a few KB) that we always send it
    in full on hello/resync instead of maintaining an op-replay ring buffer."""

    trip: TripOut
    days: list[DayOut] = Field(default_factory=list)
    places: list[PlaceOut] = Field(default_factory=list)
    participants: list[ParticipantOut] = Field(default_factory=list)
    presence: list[Presence] = Field(default_factory=list)


class MatrixOut(BaseModel):
    """The day's travel-cost matrix, aligned with `place_ids` order.

    seconds[i][j] is the cost of going from place i to place j: real seconds for
    cost_model=amap, haversine-derived estimates otherwise. `None` marks an
    unreachable pair (also listed in `unreachable_pairs`, 0-based indices)."""

    place_ids: list[str]
    seconds: list[list[int | None]]
    api_calls: int = 0
    cache_hits: int = 0
    mode_used: str = "driving"
    fallback_from: str | None = None
    warnings: list[str] = Field(default_factory=list)
    unreachable_pairs: list[tuple[int, int]] = Field(default_factory=list)


class PoiOut(BaseModel):
    """A place from Amap, normalised. The Web服务 key never reaches the browser --
    POI search is proxied for exactly that reason."""

    model_config = ConfigDict(from_attributes=True)

    id: str = ""
    name: str
    address: str = ""
    lng: float
    lat: float
    city: str = ""
    district: str = ""


# -- request bodies ---------------------------------------------------------------


class TripCreate(BaseModel):
    title: str = ""
    city: str = ""
    travel_mode: TravelMode = TravelMode.DRIVING


class TripCreateResult(BaseModel):
    trip_id: str
    day_id: str
    share_url: str


class ParticipantUpsert(BaseModel):
    client_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=40)
    color: str = ""


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lng: float
    lat: float
    address: str = ""
    amap_poi_id: str = ""
    duration_min: int = Field(default=60, ge=0, le=24 * 60)
    note: str = ""
    added_by: str = ""
    after_place_id: str | None = None
