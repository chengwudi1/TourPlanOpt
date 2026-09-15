<script setup lang="ts">
/**
 * 游游的对话面板（M27）。
 *
 * 这里没有解析，也没有一行写数据的代码：输入交给 `stores/assistant.ts` 去问后端，
 * 回来的每条候选指令渲染成一张确认卡，点了才走既有 store 方法 → 既有 WS op。
 *
 * 三条渲染纪律，都来自后端的接口约定：
 * 1. 卡上那句人话用服务端给的 `label`，前端不重拼一遍（两处拼必然会对不上）。
 * 2. `notes`（questions + warnings）逐条显示，一条都不藏。
 * 3. 没有「一键全部应用」。删除类天然要求手点，其它几条同样逐张确认，
 *    因为一批候选里混着只靠规则认出来的东西。
 */
import type { Component } from 'vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import type { ActionCard, AssistantAction } from '@/types/assistant'
import {
  ArrowRight,
  CalendarDays,
  CheckCheck,
  CircleDashed,
  ListChecks,
  Lock,
  LockOpen,
  MapPin,
  Navigation,
  Receipt,
  Route,
  Timer,
  Trash2,
  X,
} from '@/components/icons'
import { useAssistantStore } from '@/stores/assistant'
import { useTripStore } from '@/stores/trip'
import { formatDuration } from '@/utils/time'
import { categoryLabel, formatMoney } from '@/utils/money'

const assistant = useAssistantStore()
const store = useTripStore()

const input = ref('')
const logEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLInputElement | null>(null)

/**
 * 示例语料按当前能力给（O8）：只有一天时还演示「第2天加个…」，第一句就撞上反问，
 * 看着像没听懂；而「最后一天别排太满」这种说法本机词表接不住，只在接入云端模型时才摆出来。
 */
const SUGGESTIONS = computed(() => {
  const add = store.days.length >= 2 ? '第2天加个玄武湖玩两小时' : '加个玄武湖玩两小时'
  const base = [add, '记得带雨伞和充电宝', '门票花了240', '优化一下今天的顺序']
  return assistant.status?.llm_ready ? [...base, '最后一天别排太满'] : base
})

/** 「规则 / 模型」是开发者词汇；用户要读的是这台机器现在听得懂多自然的话。 */
const engineLabel = computed(() => (assistant.status?.llm_ready ? '在线理解' : '本机理解'))
const engineHint = computed(() =>
  assistant.status?.llm_ready
    ? '已接入云端模型：自然一些的说法也能理解'
    : '未接入云端模型：由本机词表按「哪天、哪个地点、多少钱」这类明确句式理解',
)

const KIND_META: Record<AssistantAction['kind'], { icon: Component; op: string }> = {
  place_add: { icon: MapPin, op: 'place_add' },
  place_move: { icon: ArrowRight, op: 'place_move' },
  place_update: { icon: Timer, op: 'place_update' },
  place_delete: { icon: Trash2, op: 'place_delete' },
  place_lock: { icon: Lock, op: 'place_lock' },
  day_add: { icon: CalendarDays, op: 'day_add' },
  checklist_add: { icon: ListChecks, op: 'checklist_add' },
  checklist_update: { icon: CheckCheck, op: 'checklist_update' },
  checklist_delete: { icon: Trash2, op: 'checklist_delete' },
  expense_add: { icon: Receipt, op: 'expense_add' },
  trip_update: { icon: Navigation, op: 'trip_update' },
  run_optimize: { icon: Route, op: 'optimize' },
}

function meta(action: AssistantAction) {
  const base = KIND_META[action.kind]
  return action.kind === 'place_lock' && !action.locked ? { ...base, icon: LockOpen } : base
}

/** 一行补充信息。文案仍以服务端 `label` 为主，这里只补它没说的数。 */
function detail(action: AssistantAction): string {
  switch (action.kind) {
    case 'place_add':
      return `停留 ${formatDuration(action.duration_min)}${
        action.after_place_id ? ' · 排在指定地点之后' : ' · 排在末尾'
      }`
    case 'place_update':
      return Object.entries(action.patch)
        .map(([k, v]) => `${k} → ${typeof v === 'number' ? formatDuration(v) : String(v)}`)
        .join('，')
    case 'expense_add':
      return `${formatMoney(action.amount_cents)} · ${categoryLabel(action.category)}`
    case 'checklist_add':
      return action.texts.join('、')
    case 'day_add':
      return action.date ? `日期 ${action.date}` : '接在最后，日期由服务端续排'
    case 'trip_update':
      return Object.keys(action.patch).join('、')
    case 'place_lock':
      return action.clears_time ? '解锁会同时清掉手填时刻' : ''
    default:
      return ''
  }
}

const destructive = (action: AssistantAction): boolean =>
  action.kind === 'place_delete' || action.kind === 'checklist_delete'

const stateText: Record<ActionCard['state'], string> = {
  pending: '',
  done: '已执行',
  skipped: '已忽略',
  failed: '执行失败',
}

const canSend = computed(() => !!input.value.trim() && !assistant.busy && !!store.trip)

function submit(): void {
  const text = input.value
  if (!canSend.value) return
  input.value = ''
  void assistant.say(text)
}

async function scrollToEnd(): Promise<void> {
  await nextTick()
  logEl.value?.scrollTo({ top: logEl.value.scrollHeight, behavior: 'smooth' })
}

watch(() => assistant.lines.length, () => void scrollToEnd())
watch(
  () => assistant.open,
  (isOpen) => {
    if (!isOpen) return
    void scrollToEnd()
    window.setTimeout(() => inputEl.value?.focus(), 60)
  },
)

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && assistant.open) assistant.toggle(false)
}

window.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <section v-show="assistant.open" class="apanel" role="dialog" aria-label="行程助手">
      <header class="apanel__hd">
        <div class="apanel__title">
          <h3>游游</h3>
          <p class="tiny">说一句话，行程按你说的改</p>
        </div>
        <span class="apanel__engine tiny mono" :title="engineHint">{{ engineLabel }}</span>
        <button class="apanel__x" type="button" aria-label="关闭" @click="assistant.toggle(false)">
          <X :size="14" />
        </button>
      </header>

      <div ref="logEl" class="apanel__log">
        <p v-if="!assistant.lines.length" class="msg msg--pet">
          <span class="who">游</span>
          <span class="txt">{{ assistant.greeting() }}</span>
        </p>

        <template v-for="line in assistant.lines" :key="line.id">
          <p class="msg" :class="line.from === 'me' ? 'msg--me' : 'msg--pet'">
            <span class="who">{{ line.from === 'me' ? '我' : '游' }}</span>
            <span class="txt">{{ line.text }}</span>
          </p>

          <ul v-if="line.notes?.length" class="notes">
            <li v-for="(note, i) in line.notes" :key="i">{{ note }}</li>
          </ul>

          <div
            v-for="(card, i) in line.cards ?? []"
            :key="`${line.id}-${i}`"
            class="act"
            :class="[`act--${card.state}`, destructive(card.action) && 'act--danger']"
          >
            <div class="act__hd">
              <component :is="meta(card.action).icon" class="ic" :size="14" />
              <strong>{{ card.action.label }}</strong>
              <em class="mono">{{ meta(card.action).op }}</em>
            </div>
            <p v-if="detail(card.action)" class="act__detail tiny">{{ detail(card.action) }}</p>
            <p v-if="destructive(card.action)" class="act__warn tiny">
              删除类不会自动执行，点「执行」才生效
            </p>
            <p v-if="card.error" class="act__warn tiny">{{ card.error }}</p>
            <div class="act__ft">
              <span v-if="card.state !== 'pending'" class="tiny muted">{{ stateText[card.state] }}</span>
              <button
                v-if="card.state === 'pending' || card.state === 'failed'"
                class="btn btn--sm"
                :class="destructive(card.action) ? '' : 'btn--primary'"
                type="button"
                @click="void assistant.confirm(line.id, i)"
              >
                {{ card.state === 'failed' ? '重试' : '执行' }}
              </button>
              <button
                v-if="card.state === 'pending'"
                class="btn btn--sm btn--ghost"
                type="button"
                @click="assistant.dismiss(line.id, i)"
              >
                忽略
              </button>
            </div>
          </div>
        </template>

        <p v-if="assistant.busy" class="msg msg--pet">
          <span class="who">游</span>
          <span class="txt"><CircleDashed class="ic spin" :size="13" /> 正在解析</span>
        </p>
      </div>

      <div v-if="!assistant.lines.length" class="hint">
        <button v-for="s in SUGGESTIONS" :key="s" type="button" @click="input = s">{{ s }}</button>
      </div>

      <footer class="apanel__in">
        <input
          ref="inputEl"
          v-model="input"
          type="text"
          :maxlength="600"
          placeholder="例如：第2天加个玄武湖玩两小时"
          @keydown.enter.prevent="submit"
        />
        <button class="btn btn--sm btn--primary" type="button" :disabled="!canSend" @click="submit">
          <ArrowRight :size="14" />
        </button>
      </footer>
    </section>
  </Teleport>
</template>

<style scoped>
.apanel {
  position: fixed;
  right: var(--s5);
  bottom: 132px;
  width: min(420px, calc(100vw - 32px));
  z-index: calc(var(--z-sprite) + 1);
  background: var(--surface);
  border: 2px solid var(--ink);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-pop);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.apanel__hd {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s3) var(--s4);
  background: var(--accent);
  color: var(--accent-ink);
}

.apanel__title h3 {
  margin: 0;
  font: 800 var(--t-h2)/1.2 var(--font-display);
  letter-spacing: var(--ls-tight);
}

.apanel__title p {
  margin: 3px 0 0;
  opacity: 0.85;
}

.apanel__engine {
  margin-left: auto;
  padding: 3px 8px;
  border-radius: var(--radius-pill);
  border: 1px solid currentColor;
  opacity: 0.85;
}

.apanel__x {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: inherit;
  background: transparent;
  border: 1.5px solid currentColor;
  cursor: pointer;
}

.apanel__log {
  max-height: 46vh;
  overflow-y: auto;
  padding: var(--s3) var(--s4);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.msg {
  display: flex;
  gap: var(--s2);
  margin: 0;
  font-size: var(--t-meta);
  line-height: var(--lh-body);
  animation: apanel-slide var(--dur-entrance) var(--ease-out) backwards;
}

.msg--me {
  flex-direction: row-reverse;
}

.msg .who {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font: 800 var(--t-micro)/1 var(--font);
  background: var(--ramp-4-soft);
  color: var(--ramp-4-deep);
  border: 1.5px solid var(--ink);
}

.msg--me .who {
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.msg .txt {
  padding: 9px var(--s3);
  border-radius: var(--radius) var(--radius) var(--radius) 4px;
  background: var(--surface-2);
  border: 1px solid var(--border);
}

.msg--me .txt {
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.notes {
  margin: 0;
  padding: 8px 10px;
  list-style: none;
  border-radius: var(--radius-sm);
  background: var(--warn-soft);
  border: 1px dashed var(--warn-border);
  color: var(--text-2);
  font-size: var(--t-micro);
  line-height: var(--lh-body);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.notes li {
  padding-left: 12px;
  position: relative;
}

.notes li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--warn);
}

/* 行动确认卡：一句话被翻译成哪几条 op，说清楚了才动手。 */
.act {
  border: 1.5px solid var(--ink);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  animation: apanel-slide var(--dur-entrance) var(--ease-out) backwards;
}

.act__hd {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: 9px var(--s3);
  background: var(--surface-2);
  font-size: var(--t-micro);
}

.act__hd strong {
  font-weight: 800;
  font-size: var(--t-meta);
}

.act__hd em {
  font-style: normal;
  margin-left: auto;
  flex: none;
  font-size: var(--t-micro);
  color: var(--text-faint);
}

.act__detail {
  margin: 0;
  padding: 0 var(--s3) var(--s2);
  color: var(--text-2);
}

.act__warn {
  margin: 0 var(--s3) var(--s2);
  padding: 7px 9px;
  border-radius: var(--radius-sm);
  background: var(--warn-soft);
  color: var(--text-2);
  border: 1px dashed var(--warn-border);
}

.act__ft {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border-top: 1px solid var(--border);
}

.act__ft .tiny {
  margin-right: auto;
}

.act--danger .act__hd {
  background: var(--danger-soft);
}

.act--danger .act__warn {
  background: var(--danger-soft);
  border-color: var(--danger-border);
}

/* 删除的执行按钮不上实心 danger：深色态 --danger 是中亮度的红，白字压不住。
   描边 + 红字在两种主题下都过线，而且「红字白底」本来就比「红色块」更像要出事的动作。 */
.act--danger .act__ft .btn {
  color: var(--danger);
  border-color: var(--danger);
}

.act--done,
.act--skipped {
  opacity: 0.66;
}

.act--done .act__ft {
  background: var(--ok-soft);
}

.act--failed {
  border-color: var(--danger);
}

.spin {
  animation: apanel-spin 1.1s linear infinite;
  transform-origin: center;
}

@keyframes apanel-spin {
  to {
    transform: rotate(360deg);
  }
}

.hint {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 var(--s4) var(--s3);
}

.hint button {
  padding: 5px 11px;
  border-radius: var(--radius-pill);
  font-size: var(--t-micro);
  color: var(--text-2);
  border: 1px dashed var(--hairline);
  background: var(--surface);
  cursor: pointer;
  transition: color var(--dur-fast), border-color var(--dur-fast), transform var(--dur-fast);
}

.hint button:hover {
  border-color: var(--accent);
  border-style: solid;
  color: var(--accent);
  transform: translateY(-1px);
}

.apanel__in {
  display: flex;
  gap: var(--s2);
  padding: var(--s3) var(--s4);
  border-top: 2px solid var(--ink);
  background: var(--surface-2);
}

.apanel__in input {
  flex: 1;
  min-width: 0;
  padding: 10px var(--s3);
  border-radius: var(--radius-pill);
  border: 1.5px solid var(--ink);
  background: var(--surface);
  font: inherit;
  font-size: var(--t-meta);
  color: var(--text);
}

.apanel__in input:focus {
  outline: none;
  box-shadow: 0 0 0 3px var(--accent-soft);
}

@keyframes apanel-slide {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}

@media (max-width: 640px) {
  .apanel {
    right: var(--s3);
    left: var(--s3);
    bottom: 122px;
    width: auto;
  }
}
</style>
