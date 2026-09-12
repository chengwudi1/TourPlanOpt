/**
 * 行程「走得怎么样了」——首页卡片与看板唯一的事实来源。
 *
 * 库里只存人亲手改得动的三档 status（planning / finished / archived）。
 * 「几天后出发」「进行中」「已经结束」都不存：那三者是 days.date 与今天比出来的，
 * 存下来就是一份会过期的假事实（没有定时任务会去翻旧行程）。所以派生放在这里，
 * 每天第一次渲染自然就是对的。
 *
 * 日期一律按「本地零点」算。`new Date('2026-10-01')` 会当成 UTC 零点，在东八区看着没事，
 * 换个时区就整日错位——倒计时差一天的 bug 全是这么来的。
 */

import type { TripStatus } from '@/types/domain'

export type TripPhase = 'undated' | 'upcoming' | 'ongoing' | 'ended'

export interface Countdown {
  phase: TripPhase
  /** 距今多少天：负数 = 已经过去了；没日期时是 null。 */
  days: number | null
  /** 卡片上那一颗胶囊的文案，没日期时为空串（宁可不显示，也不显示「NaN 天后」）。 */
  label: string
  /** 配色档位，样式靠它选，不靠文案猜。 */
  tone: 'none' | 'soon' | 'live' | 'past'
}

const DAY_MS = 86_400_000

/** 'YYYY-MM-DD' -> 本地零点；读不懂就当没填（null），不抛错。 */
export function parseDateOnly(value: string | null | undefined): Date | null {
  if (!value || typeof value !== 'string') return null
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value.trim())
  if (!match) return null
  const [, y, m, d] = match
  const date = new Date(Number(y), Number(m) - 1, Number(d))
  // JS 的 Date 不会为 2月30日 报错，它会顺成 3月2日。不回头核对，一个根本不存在的
  // 日期就能带着「3 天后出发」的口气上首页。对不上就按没填处理。
  if (
    date.getFullYear() !== Number(y) ||
    date.getMonth() !== Number(m) - 1 ||
    date.getDate() !== Number(d)
  )
    return null
  return Number.isNaN(date.getTime()) ? null : date
}

export function todayLocal(): Date {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

function wholeDays(from: Date, to: Date): number {
  return Math.round((to.getTime() - from.getTime()) / DAY_MS)
}

export function phaseOf(
  startDate: string | null | undefined,
  endDate: string | null | undefined,
  today: Date = todayLocal(),
): TripPhase {
  const start = parseDateOnly(startDate)
  if (!start) return 'undated'
  const end = parseDateOnly(endDate) ?? start
  if (wholeDays(today, start) > 0) return 'upcoming'
  if (wholeDays(today, end) >= 0) return 'ongoing'
  return 'ended'
}

/** 卡片上那枚「X 天后出发 / 进行中 / 已结束」胶囊。 */
export function countdownOf(
  startDate: string | null | undefined,
  endDate: string | null | undefined,
  today: Date = todayLocal(),
): Countdown {
  const phase = phaseOf(startDate, endDate, today)
  if (phase === 'undated') return { phase, days: null, label: '', tone: 'none' }

  const start = parseDateOnly(startDate) as Date
  const end = parseDateOnly(endDate) ?? start
  const days = wholeDays(today, start)

  if (phase === 'upcoming') {
    return {
      phase,
      days,
      label: days === 1 ? '明天出发' : `${days} 天后出发`,
      tone: days <= 14 ? 'soon' : 'none',
    }
  }
  if (phase === 'ongoing') {
    const n = wholeDays(start, today) + 1
    const total = wholeDays(start, end) + 1
    return { phase, days: 0, label: `进行中 · 第 ${n}/${total} 天`, tone: 'live' }
  }
  return { phase, days, label: '已结束', tone: 'past' }
}

/**
 * 还没归档但日期已经走完的行程：首页给一个「标记完成」的一键动作。
 * 这一档值得单独存在，是因为「回来了但懒得整理」是旅行产品里最常见的沉默流失。
 */
export function needsWrapUp(status: TripStatus, phase: TripPhase): boolean {
  return status === 'planning' && phase === 'ended'
}

function shortDate(value: string | null | undefined): string {
  const date = parseDateOnly(value)
  return date ? `${date.getMonth() + 1}月${date.getDate()}日` : ''
}

/** '10月1日 - 10月3日'；只有一天或只有一天有日期时收成一段。 */
export function formatDateRange(
  startDate: string | null | undefined,
  endDate: string | null | undefined,
): string {
  const first = shortDate(startDate)
  if (!first) return ''
  const last = shortDate(endDate)
  if (!last || last === first) return first
  return `${first} - ${last}`
}

export const HOME_TABS: { key: TripStatus; label: string }[] = [
  { key: 'planning', label: '规划中' },
  { key: 'finished', label: '已完成' },
  { key: 'archived', label: '已归档' },
]

export const STATUS_LABELS: Record<TripStatus, string> = {
  planning: '规划中',
  finished: '已完成',
  archived: '已归档',
}

/** 首页看板上「还差什么」的一条提醒。进度类的一律给出分子分母，不说教。 */
export function readinessHints(card: {
  checklist_total: number
  checklist_done: number
  day_count: number
  place_count: number
  budget_cents: number
  spent_cents: number
}): string[] {
  const hints: string[] = []
  if (card.day_count && !card.place_count) hints.push('尚未安排地点')
  if (card.checklist_total === 0) hints.push('出行清单未填写')
  else if (card.checklist_done < card.checklist_total) {
    hints.push(`清单尚有 ${card.checklist_total - card.checklist_done} 项待确认`)
  }
  if (!card.budget_cents) hints.push('未设置预算')
  return hints
}
