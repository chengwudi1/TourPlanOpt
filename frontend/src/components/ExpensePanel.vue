<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  Banknote,
  BedDouble,
  Copy,
  Receipt,
  ShoppingBag,
  Ticket,
  TrainFront,
  Trash2,
  Users,
  UtensilsCrossed,
  Wallet,
  Check,
} from '@/components/icons'
import { getClientId } from '@/composables/useClientIdentity'
import { useCopy } from '@/composables/useCopy'
import { useDialogStore } from '@/stores/dialog'
import { useTripStore } from '@/stores/trip'
import type { Expense } from '@/types/domain'
import {
  EXPENSE_CATEGORY_ORDER,
  budgetRatio,
  categoryLabel,
  categoryTotals,
  computeShares,
  expenseSplits,
  formatMoney,
  formatSigned,
  parseMoneyToCents,
  suggestTransfers,
  type ExpenseCategoryKey,
} from '@/utils/money'

const store = useTripStore()
const dialog = useDialogStore()
const copy = useCopy()

const CATEGORY_ICONS = {
  transport: TrainFront,
  lodging: BedDouble,
  food: UtensilsCrossed,
  ticket: Ticket,
  shopping: ShoppingBag,
  other: Receipt,
} as const

function categoryIcon(category: string) {
  return CATEGORY_ICONS[category as ExpenseCategoryKey] ?? CATEGORY_ICONS.other
}

const title = ref('')
const amount = ref('')
const category = ref<ExpenseCategoryKey>('other')
const copied = ref(false)
/** 还没记上的那笔为什么记不上：就地说明，不弹横幅。 */
const complaint = computed(() => {
  if (!title.value.trim() && !amount.value.trim()) return ''
  if (!title.value.trim()) return '请填写费用名称'
  if (parseMoneyToCents(amount.value) === null) return '金额需为大于 0 的数值，精确到分'
  return ''
})

const roster = computed(() =>
  store.participants.map((p) => ({ client_id: p.client_id, name: p.name })),
)

/** null = 「全名册均摊」这个默认意图。一旦有人手工点过，就换成明确的名单。 */
const splitSel = ref<string[] | null>(null)
const activeSplits = computed(() => splitSel.value ?? roster.value.map((p) => p.client_id))
const selfId = getClientId()

function toggleSplit(clientId: string) {
  const next = new Set(activeSplits.value)
  if (next.has(clientId)) next.delete(clientId)
  else next.add(clientId)
  // 一个都不留等于「谁都没摊」，那不是记账意图，是误点——退回付款人独担。
  // 补上自己还有一层：不补的话 chips 显示「没人摊」，而实际记下的是「我一个人摊」，
  // 界面和账各说一套。
  if (!next.size) next.add(selfId)
  splitSel.value = [...next]
}

const spent = computed(() => store.spentCents)
const budget = computed(() => store.trip?.budget_cents ?? 0)
const ratio = computed(() => budgetRatio(spent.value, budget.value))
const overCents = computed(() =>
  ratio.value !== null && ratio.value > 1 ? spent.value - budget.value : 0,
)
const totals = computed(() => categoryTotals(store.expenses))
const shares = computed(() => computeShares(store.expenses, roster.value))
const transfers = computed(() => suggestTransfers(shares.value))
const newestFirst = computed(() => store.expensesNewestFirst)

function nameOf(clientId: string): string {
  if (clientId === selfId) return '我'
  return roster.value.find((p) => p.client_id === clientId)?.name || '同伴'
}

function submit() {
  const cents = parseMoneyToCents(amount.value)
  const text = title.value.trim()
  if (!text || cents === null) return
  store.addExpense({
    title: text,
    amount_cents: cents,
    category: category.value,
    split_ids: activeSplits.value,
  })
  title.value = ''
  amount.value = ''
}

/** 原生框时代最坑的一处：读不懂就悄悄不记，用户以为改好了。所以校验前移到输入框里。 */
function moneyError(label: string, raw: string): string | null {
  if (parseMoneyToCents(raw) !== null) return null
  // 「0」不是看不懂，是不能要——两种情况的话得分开说。
  const asNumber = Number((raw ?? '').replace(/[,，\s￥¥元]/g, ''))
  if (raw.trim() && Number.isFinite(asNumber) && asNumber <= 0) {
    return `${label}要大于 0`
  }
  return `${label}无法识别，请输入像 128 或 128.50 这样的数字`
}

async function editAmount(expense: Expense) {
  const text = await dialog.prompt({
    title: '修改金额',
    value: (expense.amount_cents / 100).toString(),
    inputMode: 'decimal',
    unit: '元',
    confirmLabel: '保存',
    validate: (raw) => moneyError('金额', raw),
  })
  if (text === null) return
  const cents = parseMoneyToCents(text)
  if (cents === null || cents === expense.amount_cents) return
  store.updateExpense(expense.id, { amount_cents: cents })
}

async function editTitle(expense: Expense) {
  const text = await dialog.prompt({
    title: '修改费用名称',
    value: expense.title,
    confirmLabel: '保存',
    emptyMessage: '费用名称不能为空',
  })
  if (text === null) return
  const trimmed = text.trim()
  if (trimmed === expense.title) return
  store.updateExpense(expense.id, { title: trimmed })
}

async function setBudget() {
  const current = budget.value ? (budget.value / 100).toString() : ''
  const text = await dialog.prompt({
    title: '设置预算',
    message: '留空表示不设置预算。',
    value: current,
    placeholder: '例如 3000',
    inputMode: 'decimal',
    unit: '元',
    required: false,
    confirmLabel: '保存',
    validate: (raw) => (raw.trim() ? moneyError('预算金额', raw) : null),
  })
  if (text === null) return
  const cents = text.trim() ? parseMoneyToCents(text) : 0
  if (cents === null || cents === budget.value) return
  store.updateTripFields({ budget_cents: cents })
}

/** 结算文案是给群聊看的，所以带上行程名和「谁转谁多少」。 */
function settlementText(): string {
  const lines: string[] = []
  lines.push(`🧾 ${store.trip?.title || '行程'} · 费用结算`)
  lines.push(`总支出 ${formatMoney(spent.value)}（${store.expenses.length} 笔）`)
  for (const s of shares.value) {
    lines.push(`${s.name || '同伴'} 垫了 ${formatMoney(s.paid_cents)}，应摊 ${formatMoney(s.share_cents)} → ${formatSigned(s.net_cents)}`)
  }
  if (transfers.value.length) {
    lines.push('', '转账建议：')
    for (const t of transfers.value) {
      lines.push(`${t.from_name} → ${t.to_name} ${formatMoney(t.cents)}`)
    }
  }
  return lines.join('\n')
}

async function copySettlement() {
  const ok = await copy(settlementText(), {
    receipt: '结算明细已复制，可直接贴进群聊',
    fallbackTitle: '结算明细',
  })
  if (!ok) return
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

/** 删一笔账直接改的是 AA 的结算结果，得先问一句（S2 那把尺子）。
 * 不走「删了再撤销」：重新记一笔会把付款人记成我自己，那才是把账改错。 */
async function drop(expense: Expense) {
  const ok = await dialog.confirm({
    title: '删除这笔开销？',
    message: `${expense.title} ${formatMoney(expense.amount_cents)} 将从账本移除，分摊与结算跟着变。`,
    confirmLabel: '删除',
    danger: true,
  })
  if (ok) store.removeExpense(expense.id)
}
</script>

<template>
  <section class="exp card">
    <div class="exp__head">
      <strong class="exp__title"><Wallet class="ic" :size="14" /> 费用</strong>
      <span class="exp__total mono">{{ formatMoney(spent) }}</span>
      <span v-if="budget" class="tiny muted">/ 预算 {{ formatMoney(budget) }}</span>
      <span v-else class="tiny muted">未设置预算</span>
      <button class="btn btn--sm btn--ghost exp__budget" type="button" @click="setBudget">
        <Banknote class="ic" :size="13" /> 预算
      </button>
    </div>

    <div v-if="ratio !== null" class="exp__bar">
      <i
        :class="{ 'exp__bar--warn': ratio > 0.8 && ratio <= 1, 'exp__bar--over': ratio > 1 }"
        :style="{ width: `${Math.min(100, Math.round(ratio * 100))}%` }"
      />
    </div>
    <p v-if="overCents" class="tiny exp__over">已超出预算 {{ formatMoney(overCents) }}</p>

    <div v-if="totals.length" class="exp__cats">
      <span v-for="t in totals" :key="t.category" class="exp__cat">
        <component :is="categoryIcon(t.category)" class="ic" :size="12" />
        {{ categoryLabel(t.category) }}
        <b class="mono">{{ formatMoney(t.cents) }}</b>
      </span>
    </div>

    <div class="exp__form">
      <div class="exp__amount">
        <span class="exp__yuan">¥</span>
        <input
          v-model="amount"
          class="input exp__amount-input"
          type="text"
          inputmode="decimal"
          placeholder="0"
          @keyup.enter="submit"
        />
      </div>
      <input
        v-model="title"
        class="input exp__title-input"
        type="text"
        maxlength="80"
        placeholder="费用名称，例如高铁票、午餐"
        @keyup.enter="submit"
      />
      <button class="btn btn--sm btn--primary" type="button" :disabled="Boolean(complaint)" @click="submit">
        添加
      </button>
    </div>
    <p v-if="complaint" class="tiny exp__complain">{{ complaint }}</p>

    <div class="exp__picks">
      <div class="exp__pickrow">
        <button
          v-for="key in EXPENSE_CATEGORY_ORDER"
          :key="key"
          class="chip"
          :class="{ 'chip--on': category === key }"
          type="button"
          @click="category = key"
        >
          <component :is="categoryIcon(key)" class="ic" :size="12" /> {{ categoryLabel(key) }}
        </button>
      </div>
      <div v-if="roster.length > 1" class="exp__pickrow exp__pickrow--split">
        <span class="tiny muted exp__split-label"><Users class="ic" :size="12" /> 分摊</span>
        <button
          v-for="p in roster"
          :key="p.client_id"
          class="chip chip--person"
          :class="{ 'chip--on': activeSplits.includes(p.client_id) }"
          type="button"
          :title="activeSplits.length === roster.length ? '默认由全员均摊，可改为仅分摊给指定成员' : ''"
          @click="toggleSplit(p.client_id)"
        >
          {{ p.client_id === selfId ? '我' : p.name }}
        </button>
      </div>
    </div>

    <ul v-if="newestFirst.length" class="exp__list">
      <li v-for="e in newestFirst" :key="e.id" class="entry">
        <component :is="categoryIcon(e.category)" class="ic entry__icon" :size="14" />
        <div class="entry__info">
          <button class="entry__name" type="button" title="点击改名" @click="editTitle(e)">
            {{ e.title }}
          </button>
          <div class="tiny muted entry__meta">
            {{ e.paid_by === selfId ? '我' : e.paid_by_name || '同伴' }} 付的
            <template v-if="expenseSplits(e).length > 1">
              · {{ expenseSplits(e).length }} 人均摊
            </template>
            <template v-else-if="expenseSplits(e)[0] && expenseSplits(e)[0] !== e.paid_by">
              · 只摊给 {{ nameOf(expenseSplits(e)[0]) }}
            </template>
          </div>
        </div>
        <button class="entry__sum mono" type="button" title="点击改金额" @click="editAmount(e)">
          {{ formatMoney(e.amount_cents) }}
        </button>
        <button class="iconbtn entry__drop" type="button" title="删除这笔记录" @click="void drop(e)">
          <Trash2 class="ic" :size="14" />
        </button>
      </li>
    </ul>
    <p v-else class="tiny muted exp__empty">暂无记录。首笔通常为交通费用，记账后 AA 分摊将自动结算。</p>

    <div v-if="shares.length > 1" class="exp__aa">
      <div class="exp__aa-head">
        <strong class="tiny">AA 结算</strong>
        <button class="btn btn--sm btn--ghost" type="button" @click="copySettlement">
          <Check v-if="copied" class="ic" :size="12" />
          <Copy v-else class="ic" :size="12" />
          {{ copied ? '已复制' : '复制明细' }}
        </button>
      </div>
      <div v-for="s in shares" :key="s.client_id" class="aa">
        <span class="aa__name">{{ s.client_id === selfId ? '我' : s.name || '同伴' }}</span>
        <span class="tiny muted aa__detail">
          实付 {{ formatMoney(s.paid_cents) }} · 应摊 {{ formatMoney(s.share_cents) }}
        </span>
        <span
          class="mono aa__net"
          :class="{ 'aa__net--in': s.net_cents > 0, 'aa__net--out': s.net_cents < 0 }"
        >
          {{ formatSigned(s.net_cents) }}
        </span>
      </div>
      <ul v-if="transfers.length" class="exp__tips">
        <li v-for="(t, i) in transfers" :key="`${t.from}-${t.to}-${i}`" class="tiny">
          {{ t.from_name }} → {{ t.to_name }}
          <b class="mono">{{ formatMoney(t.cents) }}</b>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.exp {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}

.exp__head {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
}

.exp__title {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--ember-deep);
}

.exp__total {
  font-size: 15px;
  font-weight: 600;
  color: var(--ember-deep);
}

.exp__budget {
  margin-left: auto;
}

.exp__bar {
  height: 8px;
  overflow: hidden;
  background: var(--surface-3);
  border-radius: var(--radius-pill);
  /* 空槽要看得见：一条 4px 的实心线在「已经花满」时和一条分隔线没有区别。 */
  box-shadow: inset 0 0 0 1px var(--hairline);
}

.exp__bar i {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: var(--radius-pill);
  transition: width var(--dur-slow) var(--ease-out);
}

/* 状态类挂在 <i> 上，所以选择器也要带上 i：`.exp__bar i` 是「类+元素」，
   只写一个类的状态规则压不过它，超预算的条会一直停在底色上。 */
.exp__bar i.exp__bar--warn {
  background: var(--warn);
}

.exp__bar i.exp__bar--over {
  background: var(--danger);
}

.exp__over {
  margin: 0;
  color: var(--danger);
}

.exp__cats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.exp__cat {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--text-2);
  background: var(--surface-2);
  border-radius: var(--radius-pill);
}

.exp__cat b {
  color: var(--text);
}

.exp__form {
  display: flex;
  gap: 6px;
  align-items: center;
}

.exp__amount {
  display: flex;
  flex: 0 0 84px;
  align-items: center;
  background: var(--surface);
  border: 1.5px solid var(--ink);
  border-radius: var(--radius-sm);
  box-shadow: var(--edge);
}

/* 焦点归 accent，不归 ember：陶土一律不承担「可交互」的暗示，否则两个强调色抢同一层注意力。 */
.exp__amount:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--edge);
}

.exp__yuan {
  padding: 0 2px 0 8px;
  font-size: 13px;
  color: var(--text-3);
}

.exp__amount-input {
  width: 100%;
  padding-left: 2px;
  background: none;
  border: 0;
}

.exp__amount-input:focus {
  outline: none;
  box-shadow: none;
}

.exp__title-input {
  flex: 1;
  min-width: 0;
}

.exp__complain {
  margin: -2px 0 0;
  color: var(--danger);
}

.exp__picks {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.exp__pickrow {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.exp__split-label {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.chip {
  display: inline-flex;
  gap: var(--s1);
  align-items: center;
  padding: 3px 9px;
  font-size: var(--t-meta);
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.chip:hover {
  background: var(--surface-3);
}

/* 选中态归 accent，不归 ember：陶土是「费用/氛围」的颜色，不上可点控件。
   分类自己的色相走 ramp 小圆点，和「这一个被选中了」分开说两件事。 */
.chip--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.chip--person {
  padding: 2px 8px;
}

/* 手机上这些 chip 是「谁来摊」的唯一开关，26px 高的落点按不准（O6）。 */
@media (max-width: 860px) {
  .chip {
    min-height: 30px;
  }
}

.exp__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.entry {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 6px 4px 2px;
  border-radius: var(--radius-sm);
}

.entry:hover {
  background: var(--surface-hover);
}

.entry__icon {
  flex: 0 0 auto;
  color: var(--text-3);
}

.entry__info {
  flex: 1;
  min-width: 0;
}

.entry__name {
  display: block;
  width: 100%;
  padding: 0;
  overflow: hidden;
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  color: inherit;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: none;
  border: 0;
  cursor: pointer;
}

.entry__meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry__sum {
  flex: 0 0 auto;
  padding: 2px 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.entry__sum:hover {
  border-color: var(--accent);
}

.entry__drop {
  padding: 3px;
  opacity: 0;
}

.entry:hover .entry__drop,
.entry:focus-within .entry__drop {
  opacity: 1;
}

.exp__empty {
  margin: 0;
}

.exp__aa {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.exp__aa-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.aa {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.aa__name {
  font-size: 13px;
  font-weight: 600;
}

.aa__detail {
  flex: 1;
}

.aa__net {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-3);
}

.aa__net--in {
  color: var(--ok);
}

.aa__net--out {
  color: var(--ember-deep);
}

.exp__tips {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 2px 0 0;
  list-style: none;
  color: var(--text-2);
}

.exp__tips b {
  color: var(--text);
}
</style>
