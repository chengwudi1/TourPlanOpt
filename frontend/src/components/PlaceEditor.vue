<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import TimeRail, { type RailMark } from '@/components/TimeRail.vue'
import { ArrowRight, Clock, Navigation, Route, Sparkles, Timer } from '@/components/icons'
import type { Place } from '@/types/domain'
import { formatDuration, formatMin, parseDuration } from '@/utils/time'

/**
 * 地点编辑器本体。两个宿主共用同一份：宽屏内联在卡片里（PlaceCard），窄屏装进底部抽屉
 * （TripView 的 PlaceSheet）。宿主只负责边框、标题与出口，字段与提交全在这里——
 * 否则同一张卡在两端会各写一遍校验、各错一遍文案。
 *
 * 界面上只有两个时刻相关的状态，各自一个控件、一个名字（见 docs/TERMS.md）：
 * 「顺序：参与优化 / 不参与优化」写 `locked`，「时刻：跟随排程 / 钉在 HH:MM」写 `start_min`。
 * 后端 `is_anchor = locked || has_time`（routing/tsp.py），手填时刻会连带把 locked 置真，
 * 所以钉了时刻的卡上「参与优化」是灰的——那不是矛盾，是同一个动作的两面。
 */
const props = defineProps<{
  place: Place
  /** 同一天其它站的已钉时刻，画到轨道上当参照点。 */
  marks?: RailMark[]
  /** 高德 URI 导航链接（从上一站过来）。 */
  navHref?: string | null
  /** 抽屉宿主没有卡片行，名称就地给一个输入框。 */
  withName?: boolean
}>()

const emit = defineEmits<{
  patch: [
    place: Place,
    patch: { name?: string; duration_min?: number; note?: string; start_min?: number | null },
  ]
  lock: [place: Place, locked: boolean]
  close: []
}>()

type Patch = { name?: string; duration_min?: number; note?: string; start_min?: number | null }

// -- 本地草稿 ---------------------------------------------------------------------------

const name = ref(props.place.name)
const nameTyping = ref(false)
const duration = ref(props.place.duration_min)
const durationText = ref(formatDuration(props.place.duration_min))
const durationTyping = ref(false)
const note = ref(props.place.note)
const noteTyping = ref(false)
/** 轨道拖动期间的瞬时值：只喂读数，不落库（松手才 emit 一次）。 */
const preview = ref<number | null>(null)
/** 手填时刻一律读行数据：轨道、角标、读数条必须看同一个源，否则解锁后轨道会停在一个
 *  服务端已经不存在的时刻上。乐观写由 stores/trip.ts 镜像服务端的「设置即锁定」。 */
const start = computed(() => props.place.user_start_min)

watch(
  () => props.place.name,
  (v) => {
    if (!nameTyping.value) name.value = v
  },
)

/** 名字不许清空：清空、没改、或纯空格都回滚成行数据，改了就立即发（不等防抖）。 */
function commitName() {
  nameTyping.value = false
  const trimmed = name.value.trim()
  if (trimmed && trimmed !== props.place.name) commitNow({ name: trimmed })
  else name.value = props.place.name
}

/** 失焦＝不在写字了：先放开别人回同步的权利，再把这行字发出去。 */
function blurNote() {
  noteTyping.value = false
  commitNow({ note: note.value })
}
watch(
  () => props.place.duration_min,
  (v) => {
    duration.value = v
    if (!durationTyping.value) durationText.value = formatDuration(v)
  },
)
watch(
  () => props.place.note,
  (v) => {
    if (!noteTyping.value) note.value = v
  },
)

// -- 提交：400ms 合并成一笔 ---------------------------------------------------------------

let pending: Patch = {}
let timer: ReturnType<typeof setTimeout> | null = null
/** 改完时刻/时长之后，`start_min` 要等 `timeline_updated` 才动；等期间的轻标记。 */
const awaiting = ref(false)
let awaitingTimer: ReturnType<typeof setTimeout> | null = null

function schedule(patch: Patch) {
  pending = { ...pending, ...patch }
  if (!timer) timer = setTimeout(flush, 400)
}

function flush() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  if (!Object.keys(pending).length) return
  const patch = pending
  pending = {}
  emit('patch', props.place, patch)
  if ('start_min' in patch || 'duration_min' in patch) {
    awaiting.value = true
    if (awaitingTimer) clearTimeout(awaitingTimer)
    // 回包丢了（离线队列、被拒）也不能把「重算中」永远挂着。
    awaitingTimer = setTimeout(() => (awaiting.value = false), 4000)
  }
}

function commitNow(patch: Patch) {
  schedule(patch)
  flush()
}

/* rev 只有服务端会动（乐观写故意不碰它，见 stores/trip.ts 的 updatePlace），所以它是
   「这一行的真值到了」的唯一凭据。 */
watch(
  () => props.place.rev,
  () => {
    awaiting.value = false
    if (awaitingTimer) {
      clearTimeout(awaitingTimer)
      awaitingTimer = null
    }
  },
)

/** 收起与卸载都必须先把手上的改动发出去：面板一销毁，半行备注就再也找不回来了。 */
function done() {
  flush()
  emit('close')
}

onBeforeUnmount(flush)

// -- 停留时长 ---------------------------------------------------------------------------

const DUR_CHIPS = [
  { min: 15, label: '15 分' },
  { min: 30, label: '30 分' },
  { min: 45, label: '45 分' },
  { min: 60, label: '1 小时' },
  { min: 90, label: '1 小时 30 分' },
  { min: 120, label: '2 小时' },
]

function clampMin(v: number) {
  return Math.max(0, Math.min(24 * 60, Math.round(v)))
}

function onDurationInput() {
  const parsed = parseDuration(durationText.value)
  // 认不出＝还在打字（「1小」）。不报错、不清空，等下一个字符。
  if (parsed === null) return
  const next = clampMin(parsed)
  if (next === duration.value) return
  duration.value = next
  schedule({ duration_min: next })
}

function onDurationBlur() {
  durationTyping.value = false
  const parsed = parseDuration(durationText.value)
  if (parsed === null) {
    durationText.value = formatDuration(duration.value)
    flush()
    return
  }
  duration.value = clampMin(parsed)
  durationText.value = formatDuration(duration.value)
  commitNow({ duration_min: duration.value })
}

function pickDuration(min: number) {
  duration.value = min
  durationText.value = formatDuration(min)
  commitNow({ duration_min: min })
}

// -- 时刻与顺序 --------------------------------------------------------------------------

function onStartCommit(min: number | null) {
  preview.value = null
  if ((min ?? null) === (props.place.user_start_min ?? null)) return
  schedule({ start_min: min })
}

function followSchedule() {
  preview.value = null
  commitNow({ start_min: null })
}

/** 顺序那一行只写 `locked`；时刻是否钉住由上一行负责。 */
function setOrder(participate: boolean) {
  emit('lock', props.place, !participate)
}

// -- 读数 -------------------------------------------------------------------------------

const shownStart = computed(() => preview.value ?? start.value ?? props.place.start_min)
const shownEnd = computed(() =>
  shownStart.value === null ? null : shownStart.value + duration.value,
)
const arriveText = computed(() =>
  props.place.arrive_min === null ? '待排' : formatMin(props.place.arrive_min),
)
const leaveText = computed(() => (shownEnd.value === null ? '—' : formatMin(shownEnd.value)))
const pinnedText = computed(() =>
  start.value === null ? '跟随排程' : `钉在 ${formatMin(start.value)}`,
)
const stayText = computed(() => formatDuration(duration.value))

/** 钉住的时刻和路上算出的到达差多久——这一句才是拖滑块的意义。 */
const timeHint = computed(() => {
  const picked = preview.value ?? start.value
  if (picked === null) {
    return '到达与离开由上一站的结束时间和路途时长推算。想让这一站钉在某个时刻，拖滑块给一个时刻。'
  }
  const who = preview.value !== null ? '所选时刻' : '已填的时刻'
  const arrive = props.place.arrive_min
  if (arrive === null) return `${who} ${formatMin(picked)}，这一站尚未推算到达，优化排程会按该时刻安排。`
  const diff = picked - arrive
  if (diff >= 5) return `${who} ${formatMin(picked)}，比预计到达晚 ${formatDuration(diff)}，中间是空等。`
  if (diff <= -5) return `${who} ${formatMin(picked)}，比预计到达早 ${formatDuration(-diff)}，路途时间不够。`
  return `${who}与预计到达一致。`
})

const orderHint = computed(() => {
  if (start.value !== null)
    return `时刻钉在 ${formatMin(start.value)} 的站不参与优化：它的到达时间会牵动后面每一站。`
  return props.place.locked
    ? '一键优化会把它留在原位，只重排它前后的站。'
    : '一键优化会把它和其它站一起重排。'
})
</script>

<template>
  <div class="editor">
    <label v-if="withName" class="edit__block edit__block--name" :style="{ '--i': 0 }">
      <span class="edit__label">名称</span>
      <input
        v-model="name"
        class="input edit__input"
        type="text"
        maxlength="60"
        placeholder="这一站叫什么"
        @focus="nameTyping = true"
        @blur="commitName"
        @keydown.enter.prevent="commitName"
      />
    </label>

    <div class="edit__flow" :class="{ 'edit__flow--awaiting': awaiting }" :style="{ '--i': 1 }">
      <span
        class="flow__node"
        title="到达时间 = 上一站的结束时间 + 路途时长，由系统推算，不能直接填。"
      >
        <b class="flow__v">{{ arriveText }}</b>
        <span class="flow__k tiny">到达</span>
      </span>
      <ArrowRight class="flow__arrow" :size="13" />
      <span class="flow__node flow__node--on" title="离开时间 = 这一站的时刻 + 停留时长，下一站的到达由此推算。">
        <b class="flow__v">{{ leaveText }}</b>
        <span class="flow__k tiny">离开 · {{ stayText }}</span>
      </span>
      <span v-if="start !== null" class="flow__pin tiny">
        <Clock class="ic" :size="10" /> {{ pinnedText }}
      </span>
      <span v-else-if="awaiting" class="flow__wait tiny">重算中</span>
    </div>

    <section class="edit__block edit__block--dur" :style="{ '--i': 2 }">
      <div class="edit__head">
        <span class="edit__label"><Timer class="ic" :size="12" /> 停留时长</span>
      </div>
      <input
        v-model="durationText"
        class="input edit__input"
        type="text"
        inputmode="numeric"
        aria-label="停留时长，可填 45 分、1 小时 30 分或 100"
        @focus="durationTyping = true"
        @input="onDurationInput"
        @blur="onDurationBlur"
        @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
      />
      <div class="edit__chips">
        <button
          v-for="c in DUR_CHIPS"
          :key="c.min"
          class="chip"
          :class="{ 'chip--on': duration === c.min }"
          type="button"
          @click="pickDuration(c.min)"
        >
          {{ c.label }}
        </button>
      </div>
    </section>

    <section class="edit__block" :style="{ '--i': 3 }">
      <div class="edit__head">
        <span class="edit__label"><Clock class="ic" :size="12" /> 时刻</span>
        <span class="edit__state tiny" :class="{ 'edit__state--on': start !== null }">
          {{ pinnedText }}
        </span>
        <button
          v-if="start !== null"
          class="chip chip--action"
          type="button"
          title="取消手填的时刻，让这一站重新跟随排程"
          @click="followSchedule"
        >
          <Sparkles class="ic" :size="11" /> 跟随排程
        </button>
      </div>
      <TimeRail
        :model-value="start"
        :auto-min="place.start_min"
        :arrive-min="place.arrive_min"
        :marks="marks"
        :label="`${place.name} 的时刻`"
        @update:model-value="onStartCommit"
        @preview="preview = $event"
      />
      <p class="edit__hint tiny muted">{{ timeHint }}</p>
    </section>

    <section class="edit__block edit__block--order" :style="{ '--i': 4 }">
      <div class="edit__head">
        <span class="edit__label"><Route class="ic" :size="12" /> 顺序</span>
      </div>
      <div class="edit__chips">
        <button
          class="chip"
          :class="{ 'chip--on': !place.locked }"
          type="button"
          :disabled="start !== null"
          :title="start !== null ? '时刻已填的站不参与优化' : '一键优化可以重排这一站'"
          @click="setOrder(true)"
        >
          参与优化
        </button>
        <button
          class="chip"
          :class="{ 'chip--on': place.locked }"
          type="button"
          title="优化排程会把它留在原位"
          @click="setOrder(false)"
        >
          不参与优化
        </button>
      </div>
      <p class="edit__hint tiny muted">{{ orderHint }}</p>
    </section>

    <label class="edit__block edit__block--note" :style="{ '--i': 5 }">
      <span class="edit__label">备注</span>
      <textarea
        v-model="note"
        class="input edit__note"
        rows="2"
        placeholder="例如：19:00 已订座、周末限流"
        @focus="noteTyping = true"
        @blur="blurNote"
      />
    </label>

    <div class="edit__foot" :style="{ '--i': 6 }">
      <a
        v-if="navHref"
        class="btn btn--sm btn--ghost"
        :href="navHref"
        target="_blank"
        rel="noopener"
        title="跳转到高德地图导航"
      >
        <Navigation class="ic" :size="13" /> 导航到这里
      </a>
      <button class="btn btn--sm edit__done" type="button" @click="done">完成</button>
    </div>
  </div>
</template>

<style scoped>
.editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 逐行错峰进场：展开不是一堵墙突然长出来，而是一行行落位。填充模式 backwards
   （复用全局 rise-in 的既有约定），减少动效下由全局规则整体关停。 */
.editor > * {
  animation:
    rise-in var(--dur-slow) var(--ease-out)
    calc(var(--i, 0) * var(--stagger)) backwards;
}

.edit__flow {
  position: relative;
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 4px;
  align-items: center;
  padding: 6px 8px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

.flow__node {
  display: flex;
  flex-direction: column;
  gap: 1px;
  align-items: center;
  min-width: 0;
}

.flow__v {
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
}

.flow__k {
  color: var(--text-2);
}

/* 右端那一格就是滑块在改的值：读数与控件因此永远指同一件事。 */
.flow__node--on .flow__v {
  color: var(--accent-strong);
}

.flow__node--on .flow__k {
  color: var(--accent);
}

.flow__arrow {
  color: var(--text-faint);
}

/* 钉住的时刻做成角标，而不是第三格读数：不钉的时候它根本不该占位置。 */
.flow__pin {
  position: absolute;
  top: -7px;
  right: 8px;
  display: inline-flex;
  gap: 3px;
  align-items: center;
  padding: 1px 6px;
  color: var(--accent-strong);
  background: var(--accent-soft);
  border: 1px solid var(--accent);
  border-radius: var(--radius-pill);
}

/* 回包期间的读数：虚线 + 降饱和，读起来是「还没算完」而不是「算错了」。 */
.flow__wait {
  position: absolute;
  top: -7px;
  right: 8px;
  padding: 1px 6px;
  color: var(--text-3);
  background: var(--surface);
  border: 1px dashed var(--border);
  border-radius: var(--radius-pill);
}

.edit__flow--awaiting .flow__node--on .flow__v {
  color: var(--text-3);
}

.edit__block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.edit__block--note,
.edit__block--name {
  gap: 3px;
}

.edit__head {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 30px;
}

.edit__label {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  color: var(--text-2);
}

/* 状态跟着标签走，动作留在右边：一行里「时刻 · 钉在 11:20 · 跟随排程」读起来是一句话。 */
.edit__state {
  flex: 1;
  color: var(--text-3);
}

.edit__state--on {
  color: var(--accent-strong);
  font-weight: 600;
}

.edit__hint {
  margin: 2px 0 0;
  min-height: 16px;
}

.edit__input {
  padding: 6px 9px;
  font-size: calc(13px * var(--fs-scale));
  font-variant-numeric: tabular-nums;
}

.edit__chips {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
}

.edit__chips::-webkit-scrollbar {
  display: none;
}

.edit__note {
  padding: 7px 9px;
  font-size: calc(13px * var(--fs-scale));
  line-height: 1.5;
  resize: vertical;
}

.chip {
  flex: 0 0 auto;
  padding: 4px 9px;
  font-size: calc(12px * var(--fs-scale));
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  color: var(--text-2);
  background: var(--surface);
  border: 1px solid var(--ink);
  border-radius: var(--radius-pill);
  transition:
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur) var(--ease-pop);
}

.chip:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--accent);
}

.chip:active:not(:disabled) {
  transform: scale(0.94);
}

/* 灰掉而不是藏起来：藏起来用户会以为这一站没有这个选项。 */
.chip:disabled {
  color: var(--text-faint);
  border-color: var(--border);
  cursor: not-allowed;
}

.chip--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 600;
}

.chip--action {
  display: inline-flex;
  gap: 3px;
  align-items: center;
  color: var(--accent-strong);
  border-color: var(--accent);
}

/* 吸底：卡比一屏高时，出口必须在眼前——以前顶部那颗 × 会跟着滚走。
   能生效的前提是祖先链上没有滚动容器（`.daysec__foldclip` 用的是 overflow: clip）。 */
.edit__foot {
  position: sticky;
  bottom: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  padding: 8px 0 2px;
  background: var(--surface);
  border-top: 1px solid var(--border-faint);
}

.edit__foot a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.edit__done {
  min-width: 72px;
}

@media (max-width: 860px) {
  .edit__foot {
    /* 抽屉与列表两端都要够按：完成是这一屏唯一的收束动作。 */
    padding-bottom: max(8px, env(safe-area-inset-bottom, 0px));
  }

  .chip {
    min-height: 30px;
  }
}
</style>
