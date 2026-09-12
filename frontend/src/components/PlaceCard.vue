<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import TimeRail, { type RailMark } from '@/components/TimeRail.vue'
import {
  ArrowRight,
  Clock,
  Ellipsis,
  GripVertical,
  Lock,
  LockOpen,
  MapPin,
  Minus,
  Navigation,
  Plus,
  Sparkles,
  Timer,
  X,
} from '@/components/icons'
import type { Place } from '@/types/domain'
import { formatDuration, formatMin } from '@/utils/time'

const props = defineProps<{
  place: Place
  index: number
  active?: boolean
  /** Another participant currently has this card focused -- show their colour ring. */
  editingBy?: { name: string; color: string } | null
  /** 高德 URI API navigation link into this stop (from the previous one). */
  navHref?: string | null
  /** Colour of whoever added this place; empty = the theme colour. */
  creatorColor?: string
  /** 同一天其它站的已定时刻，画到时刻轨道上当参照点。 */
  marks?: RailMark[]
}>()

const emit = defineEmits<{
  select: [place: Place]
  remove: [place: Place]
  lock: [place: Place, locked: boolean]
  patch: [place: Place, patch: { duration_min?: number; note?: string; start_min?: number | null }]
  menu: [place: Place, pos: { x: number; y: number }]
}>()

// 长按（触屏）打开卡片菜单：500ms 按住不动即视为呼出，移动/抬起取消。
let pressTimer: ReturnType<typeof setTimeout> | null = null
function pressStart(e: PointerEvent) {
  if (e.pointerType === 'mouse') return
  const place = props.place
  const x = e.clientX
  const y = e.clientY
  pressTimer = setTimeout(() => emit('menu', place, { x, y }), 500)
}
function pressCancel() {
  if (pressTimer) {
    clearTimeout(pressTimer)
    pressTimer = null
  }
}

/** 本地草稿：编辑面板只在卡片选中时挂载，行数据可能在两次选中之间被别人改过，
 *  所以每次展开都从 props 重新取种，之后一律以草稿为准（ ack 回来再对齐 props ）。 */
const duration = ref(props.place.duration_min)
const note = ref(props.place.note)
/** 手填时刻。null = 从没定过，交给排程——轨道会把滑块画成空心、停在自动位置上。 */
const start = ref<number | null>(props.place.user_start_min)
/** 轨道拖动期间的瞬时值：只喂读数，不落库（松手才 emit 一次）。 */
const preview = ref<number | null>(null)

watch(
  () => props.active,
  (active) => {
    if (active) {
      duration.value = props.place.duration_min
      note.value = props.place.note
      start.value = props.place.user_start_min
      preview.value = null
    }
  },
)

const ringStyle = computed(() =>
  props.editingBy
    ? { borderColor: props.editingBy.color, boxShadow: `0 0 0 3px ${props.editingBy.color}33` }
    : undefined,
)

/** 右侧时刻列。start_min 是服务端排程结果：没排过时只能给一个占位破折号。 */
const clock = computed(() =>
  props.place.start_min === null ? '—' : formatMin(props.place.start_min),
)
const stayText = computed(() => `停留 ${formatDuration(props.place.duration_min)}`)

// -- 到 / 停 / 走 读数条 ---------------------------------------------------------------

/** 拖动中用轨道的瞬时值，平时用服务端排好的 start_min；两者同源，读数不会自相矛盾。 */
const shownStart = computed(() => preview.value ?? props.place.start_min)
const shownEnd = computed(() => (shownStart.value === null ? null : shownStart.value + duration.value))
const arriveText = computed(() =>
  props.place.arrive_min === null ? '待排' : formatMin(props.place.arrive_min),
)
const startText = computed(() => (shownStart.value === null ? '—' : formatMin(shownStart.value)))
const endText = computed(() => (shownEnd.value === null ? '—' : formatMin(shownEnd.value)))
const autoBadge = computed(() => (start.value === null ? '自动' : '已定'))

/** 定了时刻之后，它和路上算出的到达之间差多久——这一句才是拖轨道的意义。 */
const timeHint = computed(() => {
  const picked = preview.value ?? start.value
  if (picked === null) return '跟着排程走。拖到某个时刻上，这一刻就归你定。'
  const arrive = props.place.arrive_min
  if (arrive !== null) {
    const diff = picked - arrive
    if (diff >= 5) return `比预计到达晚 ${formatDuration(diff)}——这段是白等。`
    if (diff <= -5) return `比预计到达早 ${formatDuration(-diff)}——路上的时间得自己挤出来。`
  }
  return '已定时刻，优化排程时会绕开它。'
})

// -- 提交 -------------------------------------------------------------------------------

function commitDuration(value: number) {
  const next = Math.max(0, Math.min(24 * 60, Math.round(value)))
  duration.value = next
  if (next !== props.place.duration_min) {
    emit('patch', props.place, { duration_min: next })
  }
}

const DUR_CHIPS = [
  { min: 15, label: '15 分' },
  { min: 30, label: '30 分' },
  { min: 45, label: '45 分' },
  { min: 60, label: '1 小时' },
  { min: 120, label: '2 小时' },
]

/** start_min 是协议里唯一的「手填时刻」字段：服务端看到它就把 user_start_min 与
 *  locked 一起设上（清空则一起放开），所以这里只管发值。 */
function commitStart(min: number | null) {
  start.value = min
  preview.value = null
  if ((min ?? null) !== (props.place.user_start_min ?? null)) {
    emit('patch', props.place, { start_min: min })
  }
}

function commitNote() {
  if (note.value !== props.place.note) {
    emit('patch', props.place, { note: note.value })
  }
}
</script>

<template>
  <li
    class="card place"
    :class="{ 'place--active': active }"
    :style="ringStyle"
    :data-place-id="place.id"
    @click="emit('select', place)"
    @contextmenu.prevent="emit('menu', place, { x: $event.clientX, y: $event.clientY })"
    @pointerdown="pressStart"
    @pointerup="pressCancel"
    @pointermove="pressCancel"
    @pointercancel="pressCancel"
  >
    <span class="place__drag" title="拖动排序" aria-hidden="true">
      <GripVertical :size="14" />
    </span>

    <span class="place__lead">
      <img
        v-if="place.photo_url"
        class="place__photo"
        :src="place.photo_url"
        :alt="`${place.name} 的照片`"
        loading="lazy"
        referrerpolicy="no-referrer"
      />
      <span v-else class="place__photo place__photo--empty" aria-hidden="true">
        <MapPin :size="14" />
      </span>
      <span
        class="place__order"
        :style="creatorColor ? { background: creatorColor } : undefined"
      >{{ index + 1 }}</span>
    </span>

    <div class="place__body">
      <div class="place__title">
        <span class="place__name">{{ place.name }}</span>
        <span v-if="place.locked" class="place__pin tiny" title="已锁定，优化时不参与重排">
          <Lock :size="11" />
        </span>
        <span v-if="place.status === 'confirmed'" class="place__confirmed tiny">已确认</span>
        <span v-if="editingBy" class="place__editor tiny" :style="{ color: editingBy.color }">
          {{ editingBy.name }} 正在编辑
        </span>
      </div>

      <div v-if="place.address" class="place__address tiny">{{ place.address }}</div>

      <div v-if="place.note && !active" class="place__note tiny">{{ place.note }}</div>
    </div>

    <div class="place__time">
      <span class="place__clock">{{ clock }}</span>
      <span class="place__stay tiny">{{ stayText }}</span>
    </div>

    <div class="place__side">
      <button
        class="btn btn--sm btn--ghost place__menu"
        title="更多操作"
        @click.stop="emit('menu', place, { x: $event.clientX, y: $event.clientY })"
      >
        <Ellipsis :size="14" />
      </button>
      <button
        class="btn btn--sm btn--ghost place__delete"
        title="删除这个地点"
        @click.stop="emit('remove', place)"
      >
        <X :size="14" />
      </button>
    </div>

    <!-- 编辑器：只在选中的卡片上展开，并且横跨整张卡片——侧栏只有 400px，
         挤在正文列里轨道和读数条都会放不下。LWW 意味着在这里改一个字段永远不会
         盖掉别人对同一张卡另一个字段的修改。 -->
    <div v-if="active" class="place__edit" @click.stop>
      <div class="edit__flow" :style="{ '--i': 0 }">
        <span class="flow__node">
          <b class="flow__v">{{ arriveText }}</b>
          <span class="flow__k tiny">到达</span>
        </span>
        <ArrowRight class="flow__arrow" :size="13" />
        <span class="flow__node flow__node--on">
          <b class="flow__v">{{ startText }}</b>
          <span class="flow__k tiny">开始 · {{ autoBadge }}</span>
        </span>
        <ArrowRight class="flow__arrow" :size="13" />
        <span class="flow__node">
          <b class="flow__v">{{ endText }}</b>
          <span class="flow__k tiny">玩到</span>
        </span>
      </div>

      <section class="edit__block" :style="{ '--i': 1 }">
        <div class="edit__head">
          <span class="edit__label"><Clock class="ic" :size="12" /> 开始时间</span>
          <button
            v-if="start !== null"
            class="chip chip--action"
            type="button"
            title="放开这一刻，交回给排程"
            @click="commitStart(null)"
          >
            <Sparkles class="ic" :size="11" /> 自动排
          </button>
        </div>
        <TimeRail
          :model-value="start"
          :auto-min="place.start_min"
          :arrive-min="place.arrive_min"
          :marks="marks"
          :label="`${place.name} 的开始时间`"
          @update:model-value="commitStart"
          @preview="preview = $event"
        />
        <p class="edit__hint tiny muted">{{ timeHint }}</p>
      </section>

      <section class="edit__block" :style="{ '--i': 2 }">
        <div class="edit__head">
          <span class="edit__label"><Timer class="ic" :size="12" /> 停留时长</span>
          <span class="stepper">
            <button
              class="stepper__btn"
              type="button"
              title="少 15 分钟"
              :disabled="duration <= 0"
              @click="commitDuration(duration - 15)"
            >
              <Minus :size="13" />
            </button>
            <output class="stepper__value">{{ formatDuration(duration) }}</output>
            <button
              class="stepper__btn"
              type="button"
              title="多 15 分钟"
              :disabled="duration >= 1440"
              @click="commitDuration(duration + 15)"
            >
              <Plus :size="13" />
            </button>
          </span>
        </div>
        <div class="edit__chips">
          <button
            v-for="c in DUR_CHIPS"
            :key="c.min"
            class="chip"
            :class="{ 'chip--on': duration === c.min }"
            type="button"
            @click="commitDuration(c.min)"
          >
            {{ c.label }}
          </button>
        </div>
      </section>

      <label class="edit__block edit__block--note" :style="{ '--i': 3 }">
        <span class="edit__label">备注</span>
        <textarea
          v-model="note"
          class="input edit__note"
          rows="2"
          placeholder="例如：19:00 已订座、周末限流"
          @blur="commitNote"
        />
      </label>

      <div class="edit__foot" :style="{ '--i': 4 }">
        <button
          class="switch"
          type="button"
          role="switch"
          :aria-checked="place.locked"
          :title="place.locked ? '解锁后重新参与优化' : '锁定位置：优化时不重排、不移动'"
          @click="emit('lock', place, !place.locked)"
        >
          <span class="switch__track">
            <span class="switch__dot">
              <Lock v-if="place.locked" :size="9" />
              <LockOpen v-else :size="9" />
            </span>
          </span>
          <span class="switch__text">{{ place.locked ? '已锁定' : '锁定位置' }}</span>
        </button>
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
      </div>
    </div>
  </li>
</template>

<style scoped>
.place {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-start;
  padding: 8px 10px;
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out),
    box-shadow var(--dur) var(--ease-out),
    transform var(--dur) var(--ease-out);
}

/* 浮起 1px + 阴影升一级：行卡是列表里唯一可点的对象，靠这点位移认领 hover。
   拖拽中的幽灵不跟浮，否则它会跟旁边的行错开半像素。 */
.place:hover:not(.place--ghost) {
  background: var(--surface-hover);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.place--ghost {
  opacity: 0.4;
}

.place--active {
  border-color: var(--accent);
  box-shadow: var(--shadow-sm);
}

.place__drag {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  align-self: stretch;
  padding: 0 1px;
  color: var(--text-faint);
  cursor: grab;
  user-select: none;
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}

.place:hover .place__drag,
.place--active .place__drag {
  opacity: 1;
}

.place__lead {
  position: relative;
  flex: 0 0 auto;
}

.place__photo {
  display: block;
  width: 34px;
  height: 34px;
  background: var(--surface-2);
  object-fit: cover;
  border-radius: 50%;
}

.place__photo--empty {
  display: grid;
  place-items: center;
  color: var(--text-faint);
}

/* 序号＝地图标记上的同一个数字与同一个创建者颜色，读图与读列表因此对得上同一站。 */
.place__order {
  position: absolute;
  right: -5px;
  bottom: -2px;
  display: grid;
  place-items: center;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #fff;
  background: var(--accent);
  border: 1.5px solid var(--surface);
  border-radius: 999px;
}

.place__body {
  flex: 1 1 auto;
  min-width: 0;
}

.place__title {
  display: flex;
  gap: 5px;
  align-items: baseline;
}

.place__name {
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.place__pin {
  display: inline-flex;
  color: var(--warn);
}

.place__editor {
  white-space: nowrap;
}

.place__address {
  margin-top: 1px;
  overflow: hidden;
  color: var(--text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.place__confirmed {
  color: var(--ok);
}

.place__note {
  padding: 4px 6px;
  margin-top: 5px;
  white-space: pre-wrap;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

.place__time {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 1px;
  align-items: flex-end;
  padding-top: 1px;
}

.place__clock {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
}

.place__stay {
  color: var(--text-2);
  white-space: nowrap;
}

.place__side {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
  align-items: flex-start;
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}

.place:hover .place__side,
.place--active .place__side {
  opacity: 1;
}

.place__menu,
.place__delete {
  padding: 2px 6px;
  font-size: 13px;
}

/* ---------- 编辑面板 ---------- */

.place__edit {
  display: flex;
  flex: 1 1 100%;
  flex-direction: column;
  order: 5;
  gap: 10px;
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}

/* 逐行错峰进场：展开不是一堵墙突然长出来，而是一行行落位。
   填充模式 backwards（复用全局 rise-in 的既有约定），延迟只吃 --stagger 一个真源。 */
.place__edit > * {
  animation:
    rise-in var(--dur-slow) var(--ease-out)
    calc(var(--i, 0) * var(--stagger)) backwards;
}

.edit__flow {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto 1fr;
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
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
}

.flow__k {
  color: var(--text-2);
}

/* 中间这一格就是轨道在改的值：读数与控件因此永远指同一件事。 */
.flow__node--on .flow__v {
  color: var(--accent-strong);
}

.flow__node--on .flow__k {
  color: var(--accent);
}

.flow__arrow {
  color: var(--text-faint);
}

.edit__block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.edit__block--note {
  gap: 3px;
}

.edit__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 30px;
}

.edit__label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}

.edit__hint {
  margin: 2px 0 0;
  min-height: 16px;
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
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
}

.chip {
  flex: 0 0 auto;
  padding: 4px 9px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  color: var(--text-2);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  transition:
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur) var(--ease-pop);
}

.chip:hover {
  color: var(--text);
  border-color: var(--accent);
}

.chip:active {
  transform: scale(0.94);
}

.chip--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 600;
}

.chip--action {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--accent-strong);
  border-color: var(--accent);
}

/* 时长步进器：跟创建弹窗那套同形，这里只换更紧的一档高度。 */
.stepper {
  display: grid;
  grid-template-columns: 28px auto 28px;
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
}

.stepper__btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  color: var(--text-2);
  background: transparent;
  border: 0;
  border-radius: 50%;
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur) var(--ease-pop);
}

.stepper__btn:hover:not(:disabled) {
  color: var(--accent-strong);
  background: var(--accent-soft);
}

.stepper__btn:active:not(:disabled) {
  transform: scale(0.9);
}

.stepper__btn:disabled {
  color: var(--text-faint);
  cursor: not-allowed;
}

.stepper__value {
  padding: 0 4px;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  text-align: center;
}

.edit__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 2px;
}

.edit__foot a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.switch {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  padding: 2px 2px;
  color: var(--text-3);
  background: none;
  border: 0;
}

.switch__track {
  position: relative;
  display: block;
  width: 34px;
  height: 20px;
  background: var(--surface-3);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  transition:
    background var(--dur) var(--ease-out),
    border-color var(--dur) var(--ease-out);
}

.switch__dot {
  position: absolute;
  top: 50%;
  left: 2px;
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  color: var(--text-2);
  background: var(--surface);
  border-radius: 50%;
  box-shadow: var(--shadow-sm);
  transform: translate(0, -50%);
  transition:
    transform var(--dur) var(--ease-pop),
    color var(--dur-fast) var(--ease-out);
}

.switch:hover .switch__track {
  border-color: var(--accent);
}

.switch[aria-checked='true'] .switch__track {
  background: var(--accent);
  border-color: var(--accent);
}

.switch[aria-checked='true'] .switch__dot {
  color: var(--accent-strong);
  transform: translate(14px, -50%);
}

.switch[aria-checked='true'] .switch__text {
  color: var(--accent-strong);
  font-weight: 600;
}

.switch__text {
  font-size: 12px;
  color: var(--text-2);
}

/* 触屏没有 hover：把手与行内按钮常驻，否则无法拖动或删除。 */
@media (hover: none) {
  .place__drag,
  .place__side {
    opacity: 1;
  }
}
</style>
