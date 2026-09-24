<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { Moon } from '@/components/icons'
import { useNow } from '@/composables/useNow'
import { formatMin, MIN_PER_DAY } from '@/utils/time'

/**
 * 自绘「时刻轨道」：把一天的时间画成一条可拖的横轨，滑块所在位置的横坐标就是这一站的开始时刻。
 *
 * 为什么不是滚轮或原生 time input：对行程里的一站来说，时间本质上是**一天中的位置**，
 * 滚轮只是给输入框换皮，而 `<input type="time">` 既表达不了「次日」（parseHHMM 硬拒 >23 点），
 * 又在 Windows 上带一套跟 token 体系打架的系统皮肤。轨道还顺手换来两样东西：
 * 排程算出的到达时刻能画成参照点，同一天别的站点能画成可指认的刻度。
 *
 * 提交时机是这里唯一的坑：拖一次要经过几十个位置，每个都 emit 就是几十个 WS op。
 * 所以拖动期间只更新本地 draft（预览走 preview 事件给父组件读数），松手才 emit 一次。
 * 键盘是同一个量级的问题（按住方向键一秒三十次按下），改成预览即时、落库合并。
 */
export interface RailMark {
  name: string
  min: number
}

const props = withDefaults(
  defineProps<{
    /** 手填时刻（零点起算的分钟，>1440 即次日）。null = 交给排程自动，滑块画成空心。 */
    modelValue: number | null
    /** 服务端排程给出的开始时刻：自动态下滑块就停在这儿。 */
    autoMin?: number | null
    /** 路上算出的到达时刻——手填值跟它比才是「白等 / 早到」，不能跟 start_min 比：
     *  定了时刻之后 start_min 就等于那个值本身，两者永远重合。 */
    arriveMin?: number | null
    /** 同一天其它站已定的时刻，只当参照点，不参与计算。 */
    marks?: RailMark[]
    step?: number
    label?: string
  }>(),
  { autoMin: null, arriveMin: null, marks: () => [], step: 5, label: '开始时间' },
)

const emit = defineEmits<{
  'update:modelValue': [number | null]
  preview: [number | null]
}>()

/** 轨道右端默认画到次日 04:00：夜场、跨年、红眼航班都要有个落点。 */
const RAIL_TAIL = 4 * 60
const MIN_SPAN = 8 * 60
const MAX_SPAN = 20 * 60

const trackEl = ref<HTMLElement | null>(null)
const draft = ref<number | null>(null)
const dragging = ref(false)
const hovered = ref(false)
const focused = ref(false)
/** 拖动的起止瞬间锁定窗口：窗口由 modelValue 推导，而拖动正改着它——不锁住就会越拖越跑。 */
const frozenWin = ref<{ lo: number; hi: number } | null>(null)

/** modelValue 的本地镜像。props 要等下一次渲染才回来，连按方向键时读到的全是旧值——
 *  三次按下会变成一次 +5。滑块读数一律走这里，外部改动由 watch 同步。 */
const local = ref<number | null>(props.modelValue)
/** 键盘落库的合并窗口：预览立刻发，op 攒到停手之后一笔。 */
const COALESCE_MS = 250
let timer: ReturnType<typeof setTimeout> | undefined
let queued: number | null = null

const settled = computed(() => local.value ?? props.autoMin ?? null)
const shown = computed(() => draft.value ?? settled.value)
/** null = 从没手填过。空心滑块 + 「自动」读数，拖一下即接管。 */
const isAuto = computed(() => draft.value === null && local.value === null)

const ceil = computed(() => Math.max(MIN_PER_DAY + RAIL_TAIL, local.value ?? 0))

function computeWindow(): { lo: number; hi: number } {
  const top = ceil.value
  const anchors = [settled.value, ...props.marks.map((m) => m.min)].filter(
    (x): x is number => x !== null,
  )
  let lo: number
  let hi: number
  if (!anchors.length) {
    lo = 7 * 60
    hi = 21 * 60
  } else {
    lo = Math.min(...anchors) - 90
    hi = Math.max(...anchors) + 150
  }
  if (hi - lo < MIN_SPAN) {
    const pad = (MIN_SPAN - (hi - lo)) / 2
    lo -= pad
    hi += pad
  }
  if (hi - lo > MAX_SPAN) hi = lo + MAX_SPAN
  lo = Math.max(0, lo)
  hi = Math.min(top, hi)
  if (hi - lo < MIN_SPAN) {
    if (lo === 0) hi = Math.min(top, MIN_SPAN)
    else lo = Math.max(0, hi - MIN_SPAN)
  }
  // 锚点会随别人改时长、挪顺序而移动。滑块掉出轨道外就再也拖不回来了。
  const v = settled.value
  if (v !== null && (v < lo || v > hi)) {
    lo = Math.max(0, v - MIN_SPAN / 2)
    hi = Math.min(top, lo + MIN_SPAN)
    lo = Math.max(0, hi - MIN_SPAN)
  }
  return { lo: Math.floor(lo / 60) * 60, hi: Math.ceil(hi / 60) * 60 }
}

const win = computed(() => frozenWin.value ?? computeWindow())

function pct(min: number): number {
  const { lo, hi } = win.value
  return ((Math.min(hi, Math.max(lo, min)) - lo) / (hi - lo)) * 100
}

function clampMin(min: number): number {
  return Math.min(ceil.value, Math.max(0, min))
}

function snapMin(min: number): number {
  return clampMin(Math.round(min / props.step) * props.step)
}

function minFromClientX(clientX: number): number | null {
  const el = trackEl.value
  if (!el) return null
  const rect = el.getBoundingClientRect()
  if (!rect.width) return null
  const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
  const { lo, hi } = win.value
  return snapMin(lo + ratio * (hi - lo))
}

function startDrag(e: PointerEvent) {
  const el = trackEl.value
  if (!el) return
  if (e.pointerType === 'mouse' && e.button !== 0) return
  cancelQueued()
  el.setPointerCapture(e.pointerId)
  frozenWin.value = computeWindow()
  dragging.value = true
  const min = minFromClientX(e.clientX)
  draft.value = min
  emit('preview', min)
}

function moveDrag(e: PointerEvent) {
  if (!dragging.value) return
  const min = minFromClientX(e.clientX)
  draft.value = min
  emit('preview', min)
}

function endDrag() {
  if (!dragging.value) return
  dragging.value = false
  frozenWin.value = null
  const min = draft.value
  draft.value = null
  emit('preview', null)
  if (min !== null) commit(min)
}

/** 提交：拖动松手、Home/End 立刻落库；键盘走 queue，合并成停手后的一笔。 */
function commit(min: number | null) {
  cancelQueued()
  const v = min === null ? null : snapMin(min)
  local.value = v
  emit('update:modelValue', v)
}

/** 键盘一步：读数与预览立刻跟上，op 攒到停手之后。 */
function queue(min: number) {
  const v = snapMin(min)
  local.value = v
  emit('preview', v)
  queued = v
  if (timer !== undefined) clearTimeout(timer)
  timer = setTimeout(flushQueued, COALESCE_MS)
}

function flushQueued() {
  const v = queued
  cancelQueued()
  if (v !== null) commit(v)
}

function cancelQueued() {
  if (timer !== undefined) {
    clearTimeout(timer)
    timer = undefined
  }
  queued = null
}

watch(
  () => props.modelValue,
  (v) => {
    // 父组件给的值既不是当前读数也不是排队里那个 = 外部改动抢先了（点「自动排」清时刻
    // 就是这样）。排队那次必须作废，否则 250ms 后会把用户刚清掉的时刻又钉回去。
    if (v !== local.value && v !== queued) cancelQueued()
    local.value = v
  },
)

/** 收卡片、切天、换端都会在这里卸载：合并窗口没到期就丢弃，等于改了个数没保存。 */
onBeforeUnmount(flushQueued)

function onKeydown(e: KeyboardEvent) {
  const base = local.value ?? props.autoMin
  if (base === null && !['Home', 'End'].includes(e.key)) return
  let delta: number | null = null
  switch (e.key) {
    case 'ArrowRight':
    case 'ArrowUp':
      delta = props.step
      break
    case 'ArrowLeft':
    case 'ArrowDown':
      delta = -props.step
      break
    case 'PageUp':
      delta = 60
      break
    case 'PageDown':
      delta = -60
      break
    case 'Home':
      e.preventDefault()
      commit(win.value.lo)
      return
    case 'End':
      e.preventDefault()
      commit(win.value.hi)
      return
    default:
      return
  }
  e.preventDefault()
  if (e.shiftKey) delta = Math.sign(delta) * 60
  if (base !== null) queue(base + delta)
}

const ticks = computed(() => {
  const { lo, hi } = win.value
  const out: { min: number; major: boolean }[] = []
  const labelEvery = hi - lo > 14 * 60 ? 6 : 3
  for (let m = Math.ceil(lo / 60) * 60; m <= hi; m += 60) {
    out.push({ min: m, major: m % (labelEvery * 60) === 0 })
  }
  return out
})

const majorTicks = computed(() => ticks.value.filter((t) => t.major))

function hourLabel(min: number): string {
  const h = Math.round(min / 60)
  if (h === 24) return '24'
  return String(h >= 24 ? h - 24 : h).padStart(2, '0')
}

/** 22:00 之后到轨道末端铺一层深底：深夜这一段本来就该看着不一样。 */
const night = computed(() => {
  const { lo, hi } = win.value
  const from = Math.max(lo, 22 * 60)
  if (hi <= from) return null
  return { left: pct(from), width: pct(hi) - pct(from) }
})

const midnight = computed(() => {
  const { lo, hi } = win.value
  return hi > MIN_PER_DAY && lo < MIN_PER_DAY ? pct(MIN_PER_DAY) : null
})

/** 手填值与到达时刻之间的差额：往右是白等，往左是早到。这一段才是轨道最有用的信息。 */
const gap = computed(() => {
  if (isAuto.value) return null
  const g = props.arriveMin
  const v = shown.value
  if (g === null || v === null || Math.abs(v - g) < props.step) return null
  return {
    left: pct(Math.min(g, v)),
    width: Math.abs(pct(v) - pct(g)),
    waiting: v > g,
  }
})

/** 到达标：手填之后滑块离开了到达点，就把到达点本身留在轨上，差额才有两端可指。 */
const arriveTick = computed(() => {
  const g = props.arriveMin
  if (g === null || isAuto.value || g === shown.value) return null
  return pct(g)
})

/** 「现在」游标：真读墙上时间，每 20s 走一次。轨道窗口只有几个小时，一分钟才值半像素，
 *  秒级刷新是白烧定时器。 */
const now = useNow(20_000)
/** 轨道可以越过次日 00:00：夜里这一趟的「现在」既可能是今天的读数，也要试 +1440 那个。
 *  两个都不在窗口里就不画——一根指错位置的针比没有针更糟。 */
const nowTick = computed(() => {
  const { lo, hi } = win.value
  const d = new Date(now.value)
  const base = d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60
  const hit = [base, base + MIN_PER_DAY].find((m) => m >= lo && m <= hi)
  if (hit === undefined) return null
  return { left: pct(hit), label: formatMin(Math.floor(hit)) }
})

const bubble = computed(() => dragging.value || hovered.value || focused.value)
</script>

<template>
  <div class="rail" :class="{ 'rail--drag': dragging }">
    <div
      ref="trackEl"
      class="rail__track"
      @pointerdown.stop="startDrag"
      @pointermove="moveDrag"
      @pointerup="endDrag"
      @pointercancel="endDrag"
      @lostpointercapture="endDrag"
      @pointerenter="hovered = true"
      @pointerleave="hovered = false"
    >
      <span v-if="night" class="rail__night" :style="{ left: `${night.left}%`, width: `${night.width}%` }" />
      <span
        v-if="midnight !== null"
        class="rail__mid"
        :style="{ left: `${midnight}%` }"
        title="次日 00:00"
      >
        <Moon :size="9" />
      </span>
      <span class="rail__ticks" aria-hidden="true">
        <i
          v-for="t in ticks"
          :key="t.min"
          :class="{ 'rail__tick--major': t.major }"
          :style="{ left: `${pct(t.min)}%` }"
        />
      </span>
      <span
        v-if="gap"
        class="rail__gap"
        :class="{ 'rail__gap--early': !gap.waiting }"
        :style="{ left: `${gap.left}%`, width: `${gap.width}%` }"
      />
      <span
        v-if="arriveTick !== null"
        class="rail__arrive"
        :style="{ left: `${arriveTick}%` }"
        :title="`路途推算的到达时间 ${formatMin(arriveMin ?? 0)}`"
      />
      <span
        v-for="m in marks"
        :key="`${m.name}:${m.min}`"
        class="rail__mark"
        :style="{ left: `${pct(m.min)}%` }"
        :title="`${m.name} · ${formatMin(m.min)}`"
      />
      <span
        v-if="nowTick"
        class="rail__now"
        :style="{ left: `${nowTick.left}%` }"
        :title="`现在 ${nowTick.label}`"
        aria-hidden="true"
      />
      <button
        class="rail__knob"
        :class="{ 'rail__knob--auto': isAuto }"
        type="button"
        role="slider"
        tabindex="0"
        :aria-label="label"
        :aria-valuemin="0"
        :aria-valuemax="ceil"
        :aria-valuenow="shown ?? undefined"
        :aria-valuetext="shown === null ? '跟随排程' : formatMin(shown)"
        :style="{ left: `${shown === null ? 0 : pct(shown)}%` }"
        @keydown="onKeydown"
        @focus="focused = true"
        @blur="focused = false"
      >
        <span v-if="bubble" class="rail__bubble">{{
          shown === null ? '跟随排程' : formatMin(shown)
        }}</span>
      </button>
    </div>

    <div class="rail__hours" aria-hidden="true">
      <button
        v-for="(t, i) in majorTicks"
        :key="`h${t.min}`"
        class="rail__hour"
        :class="{
          'rail__hour--near': gap === null && shown === t.min,
          'rail__hour--first': i === 0,
          'rail__hour--last': i === majorTicks.length - 1,
        }"
        type="button"
        tabindex="-1"
        :style="{ left: `${pct(t.min)}%` }"
        @click="commit(t.min)"
      >
        {{ hourLabel(t.min) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 14px;
}

/* 轨道本体只有 6px，但命中区靠 ::before 撑到 26px：细条配大热区，
   既能看清刻度，拇指也砸得中。touch-action:none 只吃在这一条上，
   面板该滚还是要滚。 */
.rail__track {
  position: relative;
  height: 6px;
  background: var(--surface-2);
  border-radius: var(--radius-pill);
  box-shadow: inset 0 0 0 1px var(--border);
  cursor: grab;
  touch-action: none;
}

.rail__track::before {
  position: absolute;
  top: 50%;
  right: -4px;
  left: -4px;
  height: 26px;
  content: "";
  transform: translateY(-50%);
}

.rail--drag .rail__track {
  cursor: grabbing;
  box-shadow:
    inset 0 0 0 1px var(--accent),
    0 0 0 3px var(--accent-soft);
}

.rail__night {
  position: absolute;
  inset-block: 0;
  background: var(--surface-3);
  border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
}

.rail__mid {
  position: absolute;
  top: -13px;
  display: flex;
  align-items: center;
  color: var(--text-faint);
  transform: translateX(-50%);
}

.rail__mid::after {
  position: absolute;
  top: 12px;
  left: 50%;
  width: 1px;
  height: 16px;
  content: "";
  background: var(--hairline);
}

.rail__ticks {
  position: absolute;
  inset: 0;
}

.rail__ticks i {
  position: absolute;
  bottom: -4px;
  width: 1px;
  height: 3px;
  background: var(--hairline);
  transform: translateX(-50%);
}

.rail__ticks .rail__tick--major {
  height: 6px;
  background: var(--text-faint);
}

.rail__gap {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--accent-soft);
  border-radius: var(--radius-pill);
}

.rail__gap--early {
  background: var(--surface-3);
}

/* 到达标是一条竖线，不是又一个圆点：圆点已经被「同一天别的站」占了，两种参照不能撞形。 */
.rail__arrive {
  position: absolute;
  top: 50%;
  width: 2px;
  height: 16px;
  background: var(--accent);
  border-radius: 2px;
  opacity: 0.45;
  transform: translate(-50%, -50%);
}

.rail__mark {
  position: absolute;
  top: 50%;
  width: 5px;
  height: 5px;
  background: var(--surface);
  border: 1.5px solid var(--text-faint);
  border-radius: 50%;
  transform: translate(-50%, -50%);
}

/* 「现在」是一根 ambient 的针，不是控件：用 --ember 而不是 --accent，accent 只发给可点的东西。
   到达标也是 2px 竖线，所以这里靠顶端那颗呼吸的圆点区分——它在说这一根是活的。 */
.rail__now {
  position: absolute;
  top: -7px;
  bottom: -7px;
  width: 2px;
  background: var(--ember);
  border-radius: 2px;
  opacity: 0.7;
  transform: translateX(-50%);
}

.rail__now::before {
  position: absolute;
  top: -2px;
  left: 50%;
  width: 6px;
  height: 6px;
  content: "";
  background: var(--ember);
  border-radius: 50%;
  transform: translateX(-50%);
  animation: now-breathe 2.6s var(--ease-inout) infinite;
}

@keyframes now-breathe {
  50% {
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ember) 26%, transparent);
  }
}

.rail__knob {
  position: absolute;
  top: 50%;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  padding: 0;
  background: var(--accent);
  border: 2px solid var(--surface);
  border-radius: 50%;
  box-shadow: var(--shadow-sm);
  transform: translate(-50%, -50%);
  transition:
    left var(--dur) var(--ease-pop),
    background var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}

.rail--drag .rail__knob {
  transition: none;
  transform: translate(-50%, -50%) scale(1.12);
}

.rail__knob:hover {
  background: var(--accent-strong);
}

/* 自动态：空心滑块停在排程算出的时刻上，意思是「这儿还没定，拖一下就归你定」。 */
.rail__knob--auto {
  background: var(--surface);
  border: 2px dashed var(--accent);
}

.rail__bubble {
  position: absolute;
  bottom: 22px;
  left: 50%;
  padding: 2px 6px;
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  color: var(--accent-ink);
  background: var(--accent-strong);
  border-radius: var(--radius-sm);
  translate: -50% 0;
  animation: pop-in var(--dur-fast) var(--ease-out) backwards;
}

.rail__hours {
  position: relative;
  height: 15px;
}

.rail__hour {
  position: absolute;
  padding: 1px 3px;
  font-size: calc(11px * var(--fs-scale));
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
  background: none;
  border: 0;
  border-radius: 4px;
  transform: translateX(-50%);
  transition:
    color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.rail__hour:hover {
  color: var(--accent-strong);
  background: var(--accent-soft);
}

.rail__hour--near {
  color: var(--accent-strong);
  font-weight: 600;
}

/* 两端的标签贴着轨道对齐，不再各半只出去：轨道能拖到端点，标签却会把卡片顶出横向滚动。 */
.rail__hour--first {
  transform: none;
}

.rail__hour--last {
  transform: translateX(-100%);
}

/* ---------- 手机：轨道是这一屏的主控件，热区按手指来 ---------- */
@media (max-width: 860px) {
  /* 细条配大热区这条不动，只是 26px 对拇指偏小：撑到 34。 */
  .rail__track::before {
    height: 34px;
  }

  .rail__hours {
    height: 18px;
  }

  /* 11px 的刻度字不随 --t-micro 抬（这里是裸写的），单独跟一档。 */
  .rail__hour {
    padding: 2px 5px;
    font-size: calc(12px * var(--fs-scale));
  }
}
</style>
