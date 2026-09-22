import type { TravelMode } from '@/types/domain'

/**
 * 交通方式的选项与短名：新建抽屉、行程页、设置面板三处共用一份。
 *
 * 单独成一个文件只有一个理由——同一个状态在三个地方必须叫同一个名字。分开各写一份
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
