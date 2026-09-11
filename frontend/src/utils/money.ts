/**
 * 金额与 AA 分摊。
 *
 * 一条硬规矩：钱全程是整数「分」，浮点只在最后一刻出现（而且只出现在显示上）。
 * 用元做单位累加会漂——19.99 + 29.99 在二进制小数里不等于 49.98，用户拿计算器一加
 * 就发现对不上账。
 *
 * 分摊与结算都在客户端算，不落库：账本是事实（谁付了多少、跟谁分摊），谁该给谁转
 * 多少钱只是这份事实的一个视图。名册以后变了，历史记录也不会被改写。
 */

import type { Expense } from '@/types/domain'

export interface PersonShare {
  client_id: string
  name: string
  /** 这个人垫出去的钱。 */
  paid_cents: number
  /** 这个人应摊的钱（各笔按人数均摊后的合计）。 */
  share_cents: number
  /** 垫得多就该收回来（>0），垫得少就该付（<0）。恒等于 paid - share。 */
  net_cents: number
}

export interface Transfer {
  from: string
  to: string
  from_name: string
  to_name: string
  cents: number
}

/** 均摊：除不尽的余数按顺序多给前面几分（每人最多多一分，合计永远等于原始金额）。 */
export function splitEvenly(cents: number, n: number): number[] {
  if (n <= 0) return []
  const base = Math.floor(cents / n)
  const remainder = cents - base * n
  return Array.from({ length: n }, (_, i) => base + (i < remainder ? 1 : 0))
}

/** 一笔账实际摊在谁头上。split_ids 为空时退回「付款人独担」，不凭空造一个空成员。 */
export function expenseSplits(expense: Expense): string[] {
  const ids = expense.split_ids.length
    ? expense.split_ids
    : expense.paid_by
      ? [expense.paid_by]
      : []
  return [...new Set(ids.filter(Boolean))]
}

/**
 * 把账本折算成每人一份收支。
 *
 * roster 是行程名册（client_id + 展示名）。账本里出现过但不在名册上的人（例如记账之后
 * 再没进过房间的成员）照样占一行：钱不能凭空消失，否则合计对不上。
 */
export function computeShares(
  expenses: Expense[],
  roster: { client_id: string; name: string }[],
): PersonShare[] {
  const names = new Map(roster.map((p) => [p.client_id, p.name]))
  const paid = new Map<string, number>()
  const share = new Map<string, number>()
  const touch = (map: Map<string, number>, id: string, cents: number) => {
    map.set(id, (map.get(id) ?? 0) + cents)
  }

  for (const expense of expenses) {
    if (expense.paid_by) {
      touch(paid, expense.paid_by, expense.amount_cents)
      // 名册是「谁在线」，账本才是「谁付的钱」：退场的人靠这笔账上留的名字继续叫得出。
      if (!names.get(expense.paid_by) && expense.paid_by_name) names.set(expense.paid_by, expense.paid_by_name)
    }
    const splits = expenseSplits(expense)
    if (!splits.length) continue
    const parts = splitEvenly(expense.amount_cents, splits.length)
    splits.forEach((id, i) => touch(share, id, parts[i]))
  }

  const ids = [...new Set([...paid.keys(), ...share.keys()])]
  return ids
    .map((id) => {
      const paid_cents = paid.get(id) ?? 0
      const share_cents = share.get(id) ?? 0
      return {
        client_id: id,
        name: names.get(id) ?? '',
        paid_cents,
        share_cents,
        net_cents: paid_cents - share_cents,
      }
    })
    .sort((a, b) => b.net_cents - a.net_cents || a.name.localeCompare(b.name, 'zh'))
}

/**
 * 最少转账建议：欠款最多的人先还应收最多的人，一笔结清一个。
 *
 * 这是贪心，不是数学上最少的笔数（最优解要枚举子集和，旅行场景里那点人不值得）。
 * 好处是结果直观、且合计一定平账——每次转账都同时从两边扣掉，收尾时两边必然归零。
 */
export function suggestTransfers(shares: PersonShare[]): Transfer[] {
  const nameOf = new Map(shares.map((s) => [s.client_id, s.name || '成员']))
  const debtors = shares
    .filter((s) => s.net_cents < 0)
    .map((s) => ({ id: s.client_id, left: -s.net_cents }))
    .sort((a, b) => b.left - a.left)
  const creditors = shares
    .filter((s) => s.net_cents > 0)
    .map((s) => ({ id: s.client_id, left: s.net_cents }))
    .sort((a, b) => b.left - a.left)

  const transfers: Transfer[] = []
  let d = 0
  let c = 0
  while (d < debtors.length && c < creditors.length) {
    const cents = Math.min(debtors[d].left, creditors[c].left)
    if (cents > 0) {
      transfers.push({
        from: debtors[d].id,
        to: creditors[c].id,
        from_name: nameOf.get(debtors[d].id) ?? '成员',
        to_name: nameOf.get(creditors[c].id) ?? '成员',
        cents,
      })
    }
    debtors[d].left -= cents
    creditors[c].left -= cents
    if (debtors[d].left === 0) d += 1
    if (creditors[c].left === 0) c += 1
  }
  return transfers
}

export const EXPENSE_CATEGORY_LABELS: Record<string, string> = {
  transport: '交通',
  lodging: '住宿',
  food: '餐饮',
  ticket: '门票',
  shopping: '购物',
  other: '其他',
}

export const EXPENSE_CATEGORY_ORDER = [
  'transport',
  'lodging',
  'food',
  'ticket',
  'shopping',
  'other',
] as const

export type ExpenseCategoryKey = (typeof EXPENSE_CATEGORY_ORDER)[number]

export function categoryLabel(category: string): string {
  return EXPENSE_CATEGORY_LABELS[category] ?? EXPENSE_CATEGORY_LABELS.other
}

/** 按分类合计，顺序固定，金额为 0 的分类不出现。 */
export function categoryTotals(expenses: Expense[]): { category: string; cents: number }[] {
  const map = new Map<string, number>()
  for (const e of expenses) map.set(e.category, (map.get(e.category) ?? 0) + e.amount_cents)
  return EXPENSE_CATEGORY_ORDER.filter((c) => (map.get(c) ?? 0) > 0).map((c) => ({
    category: c,
    cents: map.get(c) ?? 0,
  }))
}

// -- 显示与输入解析 ---------------------------------------------------------------------

const yuan = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })

/** ¥1,280 / ¥1,280.50——整数元不显示小数，有角分才补齐两位。 */
export function formatMoney(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '¥0'
  const sign = cents < 0 ? '-' : ''
  const abs = Math.abs(Math.round(cents))
  const whole = Math.floor(abs / 100)
  const frac = abs % 100
  if (!frac) return `${sign}¥${yuan.format(whole)}`
  return `${sign}¥${yuan.format(whole)}.${String(frac).padStart(2, '0')}`
}

/** 带符号：结算里「应收 ¥120 / 应付 ¥80」比 -8000 分好读。 */
export function formatSigned(cents: number): string {
  if (cents === 0) return '持平'
  return `${cents > 0 ? '应收 ' : '应付 '}${formatMoney(Math.abs(cents))}`
}

/**
 * 用户在输入框里打的字符串换成分。认「12」「12.5」「12,345」「￥12元」这类写法。
 * 读不懂就返回 null——调用方拿它决定「这条先不记」，而不是悄悄记成 0。
 */
export function parseMoneyToCents(text: string): number | null {
  const cleaned = (text ?? '')
    .replace(/[,\s￥¥,]/g, '')
    .replace(/元$/, '')
  if (!cleaned) return null
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null
  const cents = Math.round(Number(cleaned) * 100)
  if (!Number.isFinite(cents) || cents <= 0) return null
  return Math.min(cents, 1_000_000_000)
}

/** 已花/预算：预算为 0 表示「还没设」，不能算成 0% 或 Infinity。 */
export function budgetRatio(spentCents: number, budgetCents: number): number | null {
  if (!budgetCents || budgetCents <= 0) return null
  return Math.max(0, spentCents / budgetCents)
}
