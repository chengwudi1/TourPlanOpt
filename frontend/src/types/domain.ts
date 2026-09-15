/**
 * Hand-mirror of backend/app/models/domain.py.
 *
 * Field names stay snake_case on purpose: these objects travel the wire unchanged, and
 * a camelCase mirror would need a mapping layer on every read and write. If you add a
 * field to the Python model, add it here too.
 */

export type TravelMode = 'driving' | 'walking' | 'straight'
export type CostModel = 'haversine' | 'amap'
export type PlaceStatus = 'confirmed' | 'pending'
/** 存下来的只有这三档。「即将出发 / 进行中 / 已结束」由日期现算，不存。 */
export type TripStatus = 'planning' | 'finished' | 'archived'
export type ExpenseCategory = 'transport' | 'lodging' | 'food' | 'ticket' | 'shopping' | 'other'

export interface Trip {
  id: string
  title: string
  city: string
  travel_mode: TravelMode
  cost_model: CostModel
  day_start_min: number
  status: TripStatus
  /** 分。0 = 还没设预算，不是「预算为零」。 */
  budget_cents: number
  seq: number
  created_at: string
}

/**
 * 首页仪表盘用的只读摘要（GET /api/trips/summary）。
 *
 * 不复用 Trip + 完整快照：`GET /api/trips/{id}` 在登录态下会顺手写一条 trip_visits
 * 足迹，把仪表盘渲染变成「打开过」——刚排好的「最近打开」顺序会被自己的渲染刷掉。
 * 摘要接口只读，且一次请求换一批行程。
 *
 * 卡片如今还要回答「这趟走得怎么样了」：哪天出发、清单打勾几件、花掉多少、预算多少。
 */
export interface TripSummary {
  id: string
  title: string
  city: string
  travel_mode: TravelMode
  status: TripStatus
  day_count: number
  place_count: number
  companion_count: number
  /** days.date 里已知的最早/最晚一天；创建时没填日期就是 null。 */
  start_date: string | null
  end_date: string | null
  checklist_total: number
  checklist_done: number
  budget_cents: number
  spent_cents: number
  cover_photo: string
  updated_at: string
  created_at: string
}

export interface Day {
  id: string
  trip_id: string
  day_index: number
  date: string | null
  title: string
  start_place_id: string | null
  end_place_id: string | null
  start_min: number | null
  travel_mode: TravelMode | null
  rev: number
}

export interface Place {
  id: string
  day_id: string
  trip_id: string
  sort_index: number
  name: string
  amap_poi_id: string
  address: string
  photo_url: string
  lng: number
  lat: number
  duration_min: number
  /** 用户手填的时刻：排程唯一当作固定的时间。null = 从没设过。 */
  user_start_min: number | null
  /** 以下三个都由服务端排程推导，每次顺序/时长变更都会覆盖，只当展示值用。 */
  start_min: number | null
  arrive_min: number | null
  travel_min_before: number | null
  locked: boolean
  status: PlaceStatus
  note: string
  added_by: string
  rev: number
  created_at: string
  updated_at: string
}

export interface Participant {
  trip_id: string
  client_id: string
  name: string
  color: string
  joined_at: string
  last_seen: string
}

/** Who is online right now. Server-side this is in-memory only, never persisted. */
export interface Presence {
  client_id: string
  name: string
  color: string
  current_day_id: string | null
  focusing_place_id: string | null
  dragging_day_id?: string | null
  joined_at: number
}

export interface StashItem {
  id: string
  name: string
  address: string
  photo_url: string
  lng: number
  lat: number
  amap_poi_id: string
  added_by: string
  created_at: string
}

/** 出行清单里的一条待办：不带坐标，也不参与排程。 */
export interface ChecklistItem {
  id: string
  trip_id: string
  sort_index: number
  text: string
  done: boolean
  added_by: string
  rev: number
  created_at: string
  updated_at: string
}

/** 一笔开销。金额以「分」为整数存，浮点只在显示的最后一刻出现。 */
export interface Expense {
  id: string
  trip_id: string
  title: string
  amount_cents: number
  category: ExpenseCategory | string
  paid_by: string
  paid_by_name: string
  /** 记下这笔账当时解析好的分摊名单。名册以后变了也不回溯。 */
  split_ids: string[]
  created_at: string
  updated_at: string
  rev: number
}

/**
 * The whole trip over REST. Deliberately WITHOUT any schedule summary: recomputing one
 * for every day would turn the read-only snapshot path into a routing pass, so day-level
 * results arrive separately as `timeline_updated` frames (and ride along with optimize).
 */
export interface Snapshot {
  trip: Trip
  days: Day[]
  places: Place[]
  participants: Participant[]
  presence: Presence[]
  stash: StashItem[]
  checklist: ChecklistItem[]
  expenses: Expense[]
}

/** One day's authoritative schedule: mirrored from timeline.py DayTimeline.payload(). */
export interface DayTimeline {
  day_id: string
  places: Place[]
  end_min: number
  travel_min: number
  warnings: string[]
  exact: boolean
}

/** A POI from the backend proxy. Coordinates are GCJ-02 -- never convert them. */
export interface Poi {
  id: string
  name: string
  address: string
  lng: number
  lat: number
  city: string
  district: string
  /** 首张实拍图直链（place_text extensions=all 才有；inputtips 没有）。 */
  photo?: string
  /** 「发现」按距离排序时服务端回填的直线距离（米）；其余排序为 null。 */
  distance_m?: number | null
}

/** 与后端 `TripCreate` 对齐。除 title 外全部可选：建行程这一步不许因为缺字段而失败。 */
export interface TripCreate {
  title: string
  city: string
  travel_mode: TravelMode
  days: number
  /** 'YYYY-MM-DD'；服务端读不懂就当没填，不会报错。 */
  start_date: string | null
  /** null 表示沿用服务端默认的 09:00。 */
  day_start_min: number | null
}

export interface TripCreateResult {
  trip_id: string
  day_id: string
  /** 服务端实际建出的天数——`days` 会被夹取，不能拿自己发出去的那个数显示。 */
  day_count: number
  share_url: string
}

export interface PlaceCreateInput {
  name: string
  lng: number
  lat: number
  address?: string
  photo_url?: string
  amap_poi_id?: string
  duration_min?: number
  note?: string
  added_by?: string
  after_place_id?: string | null
  /** 绝对插入下标，越界由服务端夹到端点。排在第一位的地点没有 after_place_id 可指。 */
  position?: number | null
}
