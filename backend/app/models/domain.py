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


class TripSummary(BaseModel):
    """One home-dashboard card. Deliberately NOT a Snapshot: no days, no places, no
    presence, so N cards cost one batched read instead of N full snapshots -- and, the
    reason this endpoint exists at all, reading it records nothing (opening a trip is
    what moves it to the top of 「我的活动」; merely showing it must not).

    `cover_photo` is the first place that actually carries a photo, in the order the trip
    is read (day_index, then sort_index). `updated_at` is the newest edit among the trip's
    places, falling back to the trip's own `created_at` while it has none.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str = ""
    city: str = ""
    travel_mode: str = TravelMode.DRIVING.value
    day_count: int = 0
    place_count: int = 0
    companion_count: int = 0
    cover_photo: str = ""
    updated_at: str = ""
    created_at: str = ""


class TripSummaryList(BaseModel):
    """`GET /api/trips/summary` 的响应体。请求里不存在的 id 被静默丢弃（不报错、不给
    null），所以这个数组可以比请求的 ids 短 —— 首页据此清理本地失效记录。"""

    trips: list[TripSummary] = Field(default_factory=list)


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
    user_start_min: int | None = None
    start_min: int | None = None
    arrive_min: int | None = None
    travel_min_before: int | None = None
    locked: bool = False
    status: PlaceStatus = PlaceStatus.PENDING
    note: str = ""
    added_by: str = ""
    photo_url: str = ""
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
    dragging_day_id: str | None = None  # 该成员正在拖动哪一天的顺序（他人列表给柔和提示）
    joined_at: float = 0.0


class StashItemOut(BaseModel):
    """暂存区条目：想去但还没排进某一天的点子。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    address: str = ""
    lng: float
    lat: float
    amap_poi_id: str = ""
    added_by: str = ""
    photo_url: str = ""
    created_at: str = ""


class Snapshot(BaseModel):
    """The whole trip. Small enough (tens of records, a few KB) that we always send it
    in full on hello/resync instead of maintaining an op-replay ring buffer."""

    trip: TripOut
    days: list[DayOut] = Field(default_factory=list)
    places: list[PlaceOut] = Field(default_factory=list)
    participants: list[ParticipantOut] = Field(default_factory=list)
    presence: list[Presence] = Field(default_factory=list)
    stash: list[StashItemOut] = Field(default_factory=list)


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
    photo: str = ""
    # 关键字搜索不返回距离：按距离排序时由服务端拿坐标自己算直线距离填上。其余排序这个键
    # 根本不在响应里（接口把缓存的上游原始字典直接透出），前端把缺失当作「没有距离」。
    distance_m: int | None = None


# -- request bodies ---------------------------------------------------------------


class TripCreate(BaseModel):
    """Everything except `title` is optional on purpose: the create sheet must be able
    to build a trip with a single tap and enrich it later inside the trip."""

    title: str = ""
    city: str = ""
    travel_mode: TravelMode = TravelMode.DRIVING
    days: int = 1
    start_date: str | None = None  # 'YYYY-MM-DD'; junk is ignored, never rejected
    day_start_min: int | None = None  # NULL keeps the schema default (09:00)


class TripCreateResult(BaseModel):
    trip_id: str
    day_id: str
    day_count: int = 1  # what the server actually built -- `days` gets clamped
    share_url: str


class ParticipantUpsert(BaseModel):
    client_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=40)
    color: str = ""


class StashCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lng: float
    lat: float
    address: str = ""
    amap_poi_id: str = ""
    added_by: str = ""
    photo_url: str = ""


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
    photo_url: str = ""
