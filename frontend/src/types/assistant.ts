/**
 * 对话式助手的线协议（M27/M28）。
 *
 * 手抄镜像自 `backend/app/assistant/schema.py`，规则与 `types/domain.ts` 一致：
 * 服务端 pydantic 是唯一权威，这里只跟着改，不自己发明字段。
 *
 * 一条要看进眼里的约定：这里的每个 `*Action` 都是**候选**指令。服务端解析接口只读
 * （不落库、不发 op、不消耗 seq），所以界面上的确认卡是唯一让它落地的一条路 ——
 * 尤其 `destructive` 为真的两种删除，永远不许自动执行。
 */

export type AssistantMood = 'idle' | 'thinking' | 'happy' | 'asking' | 'warn'
export type AssistantEngine = 'llm' | 'rules'

interface ActionBase {
  /** 确认卡直接显示的人话标签，服务端生成，前端不再自己拼句子。 */
  label: string
}

export interface PlaceAddAction extends ActionBase {
  kind: 'place_add'
  day_id: string
  name: string
  lng: number
  lat: number
  address: string
  amap_poi_id: string
  photo_url: string
  duration_min: number
  note: string
  after_place_id: string | null
}

export interface PlaceMoveAction extends ActionBase {
  kind: 'place_move'
  place_id: string
  name: string
  day_id: string
  /** 仅用于确认卡回显「从第 2 天挪走」，不进 op payload。 */
  from_day_id: string
}

export interface PlaceUpdateAction extends ActionBase {
  kind: 'place_update'
  place_id: string
  name: string
  /** 只含 duration_min / name / note。服务端解析接口绝不写 start_min。 */
  patch: Partial<Pick<import('@/types/domain').Place, 'name' | 'duration_min' | 'note'>>
}

export interface PlaceDeleteAction extends ActionBase {
  kind: 'place_delete'
  place_id: string
  name: string
  day_id: string
  destructive: true
}

export interface PlaceLockAction extends ActionBase {
  kind: 'place_lock'
  place_id: string
  name: string
  locked: boolean
  /** 解锁会连带清掉手填时间（求解器眼里的锚点），必须让人看见。 */
  clears_time: boolean
}

export interface DayAddAction extends ActionBase {
  kind: 'day_add'
  title: string
  date: string | null
}

export interface ChecklistAddAction extends ActionBase {
  kind: 'checklist_add'
  texts: string[]
}

export interface ChecklistUpdateAction extends ActionBase {
  kind: 'checklist_update'
  item_id: string
  text: string
  patch: { text?: string; done?: boolean }
}

export interface ChecklistDeleteAction extends ActionBase {
  kind: 'checklist_delete'
  item_id: string
  text: string
  destructive: true
}

export interface ExpenseAddAction extends ActionBase {
  kind: 'expense_add'
  title: string
  /** 整数分，与 domain.ts 里的所有金额同一口径。 */
  amount_cents: number
  category: string
}

export interface TripUpdateAction extends ActionBase {
  kind: 'trip_update'
  patch: Record<string, unknown>
}

export interface RunOptimizeAction extends ActionBase {
  kind: 'run_optimize'
  day_id: string
  day_title: string
}

export type AssistantAction =
  | PlaceAddAction
  | PlaceMoveAction
  | PlaceUpdateAction
  | PlaceDeleteAction
  | PlaceLockAction
  | DayAddAction
  | ChecklistAddAction
  | ChecklistUpdateAction
  | ChecklistDeleteAction
  | ExpenseAddAction
  | TripUpdateAction
  | RunOptimizeAction

export interface AssistantReply {
  /** 服务端模板生成的唯一一句人话。模型输出的文本永远不会出现在这里。 */
  reply: string
  mood: AssistantMood
  engine: AssistantEngine
  actions: AssistantAction[]
  questions: string[]
  warnings: string[]
}

export interface AssistantStatus {
  llm_ready: boolean
  model: string
  endpoint: string
  max_chars: number
}

/** 带 kind 的判别联合，删/改一类需要额外状态：确认卡自己的落地进度。
 * `skipped` 是人主动忽略，`failed` 是 op 被拒 —— 两者文案与颜色都不该混用。 */
export type CardState = 'pending' | 'done' | 'skipped' | 'failed'

export interface ActionCard {
  action: AssistantAction
  state: CardState
  error: string
}

export interface ChatLine {
  id: string
  /** me = 用户说的；pet = 游游说的（文案全部来自服务端 reply/warnings/questions）。 */
  from: 'me' | 'pet'
  text: string
  /** 本次解析的 warnings 与 questions。全部要显示：藏一条就等于报了一半的假成功。 */
  notes?: string[]
  cards?: ActionCard[]
  /** 这条回复出自哪套解析器。rules 不是故障，但也不该被显示成模型的成果。 */
  engine?: AssistantEngine
}
