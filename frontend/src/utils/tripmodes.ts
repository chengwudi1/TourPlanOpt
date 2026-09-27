import type { TravelMode } from '@/types/domain'

/**
 * 交通方式的选项与短名：行程级（新建抽屉、设置面板）与天级（行程页每一天）两层共用这一份。
 *
 * 单独成一个文件只有一个理由——同一个状态在每个界面上必须叫同一个名字。分开各写一份
 * 字面量时，改一处忘两处是必然，而症状是「设置里选了『直线』，新建抽屉里却显示
 * 『straight』或者干脆是另一个词」，用户只能认定这两个不是同一件事。
 *
 * `value` 用 `TravelMode` 而不是 string：传给分段控件（`options: readonly SegOption[]`）
 * 是协变的、照样收；反过来从控件的 string 回读时必须过 `isTravelMode`，否则会把自己
 * 拼出来的字符串当成合法模式写进 op。
 */
export interface TripModeOption {
  value: TravelMode
  label: string
  hint?: string
}

export const TRAVEL_MODE_OPTIONS: readonly TripModeOption[] = [
  { value: 'driving', label: '驾车 / 打车', hint: '按真实路况计算' },
  { value: 'walking', label: '步行', hint: '适合城市漫步' },
  { value: 'straight', label: '直线', hint: '按直线距离估算' },
]

/** 卡片与摘要行里的短名：整句「驾车 / 打车」在那两处放不下。 */
export const TRAVEL_MODE_TEXT: Record<TravelMode, string> = {
  driving: '驾车',
  walking: '步行',
  straight: '直线',
}

export function isTravelMode(value: unknown): value is TravelMode {
  return value === 'driving' || value === 'walking' || value === 'straight'
}

// -- 天级覆盖 --------------------------------------------------------------------------

/**
 * 「这一天跟随行程默认」在天里存的是 `travel_mode: null`（`days.travel_mode` 的 CHECK
 * 允许 NULL，后端排程与优化都按 `day.travel_mode or trip.travel_mode` 取值）。
 */
export const DAY_MODE_FOLLOW = 'follow' as const

export type DayModeChoice = TravelMode | typeof DAY_MODE_FOLLOW

export interface DayModeOption {
  value: DayModeChoice
  label: string
  hint?: string
}

/**
 * 天级选择器。**「默认」必须是独立的一档**，不能靠「让用户选一个和行程一样的值」来表达
 * 跟随：那样界面上一律显示为驾车，看不出这一天到底是跟着行程还是自己钉死了驾车，而它俩的
 * 行为完全不同——行程默认改成步行时前者跟着变、后者不变。一个状态两个名字会误导，
 * 一个名字两个状态更糟。
 *
 * 不带 hint：四等分挤在左栏里，一句「按真实路况计算」能把整条控件撑破。
 */
export const DAY_TRAVEL_MODE_OPTIONS: readonly DayModeOption[] = [
  { value: DAY_MODE_FOLLOW, label: '默认' },
  { value: 'driving', label: TRAVEL_MODE_TEXT.driving },
  { value: 'walking', label: TRAVEL_MODE_TEXT.walking },
  { value: 'straight', label: TRAVEL_MODE_TEXT.straight },
]

export function isDayModeChoice(value: unknown): value is DayModeChoice {
  return value === DAY_MODE_FOLLOW || isTravelMode(value)
}

/** 两级交通方式的唯一取值出口：这一天的段图标、导航链接、优化目标都从这里读。 */
export function effectiveTravelMode(
  day: { travel_mode: TravelMode | null } | null | undefined,
  trip: { travel_mode: TravelMode } | null | undefined,
): TravelMode {
  return day?.travel_mode ?? trip?.travel_mode ?? 'driving'
}
