"""Domain models. These double as the wire protocol's payload shapes.

`frontend/src/types/domain.ts` is a hand-mirror of this file. If you add a field here,
add it there -- tests/test_protocol.py asserts the two stay in sync.
"""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class TripStatus(StrEnum):
    """存下来的只有这三档，都是人亲手改得动的。

    「即将出发」「进行中」「已结束」不在这儿：那三者由 `days.date` 和今天比出来的，存进
    数据库就是一份会过期的假事实——这项目里没有会去翻旧行程的定时任务。
    """

    PLANNING = "planning"
    FINISHED = "finished"
    ARCHIVED = "archived"


# 费用分类：前端 chips 用同一份字面量。不做成 StrEnum 也不做 DB CHECK——一个手打的未知
# 分类降级成 other 就行，为一笔账的标签把用户正在填的表单打回去不值。
EXPENSE_CATEGORIES: frozenset[str] = frozenset(
    {"transport", "lodging", "food", "ticket", "shopping", "other"}
)


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str = ""
    city: str = ""
    travel_mode: TravelMode = TravelMode.DRIVING
    cost_model: CostModel = CostModel.HAVERSINE
    day_start_min: int = 540
    status: TripStatus = TripStatus.PLANNING
    budget_cents: int = 0
    seq: int = 0
    created_at: str = ""


class TripSummary(BaseModel):
    """One home-dashboard card. Deliberately NOT a Snapshot: no days, no places, no
    presence, so N cards cost one batched read instead of N full snapshots -- and, the
    reason this endpoint exists at all, reading it records nothing (opening a trip is
    what moves it to the top of 「我的活动」; merely showing it must not).

    卡片如今还要回答「这趟走得怎么样了」：清单打勾了几件、已经花掉多少、预算是多少、
    哪天出发。这些都是聚合数，摘要接口一次批量算好，前端不用为每张卡拉一次完整快照。

    `cover_photo` is the first place that actually carries a photo, in the order the trip
    is read (day_index, then sort_index). `updated_at` is the newest edit among the trip's
    places, falling back to the trip's own `created_at` while it has none.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str = ""
    city: str = ""
    travel_mode: str = TravelMode.DRIVING.value
    status: str = TripStatus.PLANNING.value
    day_count: int = 0
    place_count: int = 0
    companion_count: int = 0
    # days.date 里已知的最早/最晚一天。创建时没填日期的行程这两个都是 None——倒计时宁可
    # 整条不显示，也不拿 created_at 冒充出发日。
    start_date: str | None = None
    end_date: str | None = None
    checklist_total: int = 0
    checklist_done: int = 0
    budget_cents: int = 0
    spent_cents: int = 0
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


class ChecklistItemOut(BaseModel):
    """出行清单里的一条待办。与地点无关，不带坐标，也不参与排程。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    sort_index: int
    text: str
    done: bool = False
    added_by: str = ""
    rev: int = 1
    created_at: str = ""
    updated_at: str = ""


class ExpenseOut(BaseModel):
    """一笔开销。分摊人存的是**记下这笔账当时**解析好的一份 client_id 名单。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    title: str
    amount_cents: int
    category: str = "other"
    paid_by: str = ""
    paid_by_name: str = ""
    split_ids: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    rev: int = 1

    @field_validator("split_ids", mode="before")
    @classmethod
    def _parse_split_ids(cls, value: object) -> object:
        # 表里躺着的是 JSON 文本；读一行和收一份 payload 走的是同一个模型。
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return []
            return [str(v) for v in parsed] if isinstance(parsed, list) else []
        return value


class Snapshot(BaseModel):
    """The whole trip. Small enough (tens of records, a few KB) that we always send it
    in full on hello/resync instead of maintaining an op-replay ring buffer."""

    trip: TripOut
    days: list[DayOut] = Field(default_factory=list)
    places: list[PlaceOut] = Field(default_factory=list)
    participants: list[ParticipantOut] = Field(default_factory=list)
    presence: list[Presence] = Field(default_factory=list)
    stash: list[StashItemOut] = Field(default_factory=list)
    checklist: list[ChecklistItemOut] = Field(default_factory=list)
    expenses: list[ExpenseOut] = Field(default_factory=list)


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
    # 关键字搜索不返回距离：按距离排序时由服务端拿坐标自己算直线距离填上。缓存里躺着的是
    # PoiOut.model_dump 的结果、字段齐全，所以其余排序下这个键照样在响应里，只是 null——
    # 前端把 null 当作「这一行没有距离」。
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
    # 绝对插入下标：撤销删除要把行放回原位，而一天里的第一个地点没有 after_place_id 可指。
    # 越界由仓储层夹到端点，所以这里不设 bounds。
    position: int | None = None
    photo_url: str = ""


class ChecklistAdd(BaseModel):
    """一次可以只加一条，也可以一次灌一份模板（「一键补全常用清单」）。

    整批走一个 op、一次广播：逐条发会把 seq 打成一串，别人端上看着像有人连点了十几次
    添加，撤销和重排也都跟着遭殃。
    """

    texts: list[str] = Field(default_factory=list, max_length=60)
    added_by: str = ""


class ExpenseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    # 分、整数、必为正。上限只是挡手滑（多打六个 0），真实旅行开销碰不到。
    amount_cents: int = Field(ge=1, le=1_000_000_000)
    category: str = "other"
    paid_by: str = ""
    paid_by_name: str = ""
    split_ids: list[str] = Field(default_factory=list, max_length=40)


class TripPatch(BaseModel):
    """HTTP 侧改行程：首页没有 WebSocket，「标记完成 / 归档 / 设预算」只能走这条路。

    字段全部可选，且要用 ``model_fields_set`` 区分「没给这个字段」与「给了 null」——
    前者不该改动任何东西，后者是一个真实的清空意图。
    """

    title: str | None = None
    city: str | None = None
    status: str | None = None
    budget_cents: int | None = None
