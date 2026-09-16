<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

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
  Trash2,
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
  close: [place: Place]
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
const noteTyping = ref(false)
/** 手填时刻**不**留本地草稿：一律读行数据。轨道、开关、读数条必须看同一个源，
 *  否则解锁之后轨道会停在一个服务端已经不存在的时刻上（连「白等几分钟」都会跟着算错）。
 *  乐观写由 stores/trip.ts 镜像服务端的「设置即锁定」，所以一落地这里就跟着动。 */
const start = computed(() => props.place.user_start_min)
/** 轨道拖动期间的瞬时值：只喂读数，不落库（松手才 emit 一次）。 */
const preview = ref<number | null>(null)

watch(
  () => props.active,
  (active) => {
    if (active) {
      duration.value = props.place.duration_min
      note.value = props.place.note
      preview.value = null
    }
  },
)

// 展开期间别人改了停留时长：步进器是拿本地值做加减的，不回同步就会用旧底数盖掉别人。
watch(
  () => props.place.duration_min,
  (v) => {
    duration.value = v
  },
)
// 备注不能在半行字中间被拽走，所以只在焦点不在输入框里时回同步。
watch(
  () => props.place.note,
  (v) => {
    if (!noteTyping.value) note.value = v
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
const autoBadge = computed(() => (start.value === null ? '自动' : '已固定'))

/** 定了时刻之后，它和路上算出的到达之间差多久——这一句才是拖轨道的意义。 */
const timeHint = computed(() => {
  const picked = preview.value ?? start.value
  if (picked === null) {
    return '到达时间由上一站的结束时间与路途时长推算，无需设置；开始时间默认跟随到达。拖动滑块可固定本站开始时间。'
  }
  const who =
    preview.value !== null ? '所选时刻' : start.value === null ? '排程计算的时刻' : '已固定的时刻'
  const arrive = props.place.arrive_min
  if (arrive === null) return `${who}已固定，本站尚未计算到达时间，优化排程将按该时刻安排。`
  const diff = picked - arrive
  if (diff >= 5) return `${who}晚于预计到达 ${formatDuration(diff)}，中间为空等时间。`
  if (diff <= -5) return `${who}早于预计到达 ${formatDuration(-diff)}，路途时间不足。`
  return `${who}与预计到达一致。`
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
 *  locked 一起设上（清空则一起放开），前端这份镜像在 stores/trip.ts 的 updatePlace 里。 */
function commitStart(min: number | null) {
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

/** 失焦＝不在写字了：先放开别人回同步的权利，再把这行字发出去。 */
function blurNote() {
  noteTyping.value = false
  commitNote()
}

/** 收起详情。备注必须先提交：Esc 与点「收起」都不走 textarea 的 blur，而面板一卸载，
 *  没发出去的那半行字就再也找不回来了。 */
function closePanel() {
  blurNote()
  emit('close', props.place)
}

// Esc 是展开态的退出键——监听只在这一张卡展开期间挂着，同时最多一张，不会互相抢。
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closePanel()
}

let escOn = false
function esc(on: boolean) {
  if (on === escOn) return
  escOn = on
  if (on) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
}

watch(() => props.active, esc, { immediate: true })
onBeforeUnmount(() => esc(false))
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
    <!-- 整行都是把手：以前只有那一枚 grip 能拖，鼠标要瞄 14px 宽的窄条。触屏反过来——
         行上没有 touch-action:none，手指一划仍然是滚动，能拖的仍然只有那一枚 grip。 -->
    <div class="place__row">
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
        <span class="place__order" :style="creatorColor ? { background: creatorColor } : undefined">{{
          index + 1
        }}</span>
      </span>

      <div class="place__body">
        <div class="place__title">
          <span class="place__name">{{ place.name }}</span>
          <span
            v-if="place.locked"
            class="place__pin tiny"
            :title="
              place.user_start_min === null
                ? '本站已固定，优化排程不会调整'
                : '开始时间已固定，本站随之固定'
            "
          >
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
        <!-- 退出键长在删除旁边，且必须比删除更显眼：× 这个字形人人读作「关闭」，
             而它以前恰好是删除——展开详情后找不到出口、只能把地点删掉，就是这么来的。 -->
        <button
          v-if="active"
          class="btn btn--sm btn--ghost place__close"
          type="button"
          title="收起详情"
          aria-label="收起详情"
          @click.stop="closePanel"
        >
          <X :size="14" />
        </button>
        <button
          class="btn btn--sm btn--ghost place__menu"
          type="button"
          title="更多操作"
          @click.stop="emit('menu', place, { x: $event.clientX, y: $event.clientY })"
        >
          <Ellipsis :size="14" />
        </button>
        <button
          class="btn btn--sm btn--ghost place__delete"
          type="button"
          title="删除这个地点"
          aria-label="删除这个地点"
          @click.stop="emit('remove', place)"
        >
          <Trash2 :size="14" />
        </button>
      </div>
    </div>

    <!-- 编辑器：只在选中的卡片上展开，并且横跨整张卡片——侧栏只有 400px，
         挤在正文列里轨道和读数条都会放不下。LWW 意味着在这里改一个字段永远不会
         盖掉别人对同一张卡另一个字段的修改。 -->
    <div v-if="active" class="place__edit" @click.stop>
      <div class="edit__flow" :style="{ '--i': 0 }">
        <span class="flow__node" title="到达时间 = 上一站结束时间 + 路途时长，由系统推算，不可直接设置。如需调整，请修改停留时长、变更顺序，或固定本站开始时间。">
          <b class="flow__v">{{ arriveText }}</b>
          <span class="flow__k tiny">到达</span>
        </span>
        <ArrowRight class="flow__arrow" :size="13" />
        <span
          class="flow__node flow__node--on"
          :title="
            start === null
              ? '开始时间由优化排程计算，拖动下方滑块可固定'
              : '开始时间已固定，优化排程会保留该时刻；点「交回排程」可取消'
          "
        >
          <b class="flow__v">{{ startText }}</b>
          <span class="flow__k tiny">开始 · {{ autoBadge }}</span>
        </span>
        <ArrowRight class="flow__arrow" :size="13" />
        <span
          class="flow__node"
          title="结束时间 = 开始时间 + 停留时长，下一站的到达时间由此推算"
        >
          <b class="flow__v">{{ endText }}</b>
          <span class="flow__k tiny">结束</span>
        </span>
      </div>

      <section class="edit__block" :style="{ '--i': 1 }">
        <div class="edit__head">
          <span class="edit__label"><Clock class="ic" :size="12" /> 开始时间</span>
          <button
            v-if="start !== null"
            class="chip chip--action"
            type="button"
            title="取消固定，交回优化排程"
            @click="commitStart(null)"
          >
            <Sparkles class="ic" :size="11" /> 交回排程
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
              title="减少 15 分钟"
              :disabled="duration <= 0"
              @click="commitDuration(duration - 15)"
            >
              <Minus :size="13" />
            </button>
            <output class="stepper__value">{{ formatDuration(duration) }}</output>
            <button
              class="stepper__btn"
              type="button"
              title="增加 15 分钟"
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
          @focus="noteTyping = true"
          @blur="blurNote"
        />
      </label>

      <div class="edit__foot" :style="{ '--i': 4 }">
        <button
          class="switch"
          type="button"
          role="switch"
          :aria-checked="place.locked"
          :title="
            place.locked
              ? '本站已固定，优化排程不会调整；点击可取消固定'
              : '固定后，优化排程不会调整本站的顺序与开始时间；设置开始时间会自动固定'
          "
          @click="emit('lock', place, !place.locked)"
        >
          <span class="switch__track">
            <span class="switch__dot">
              <Lock v-if="place.locked" :size="9" />
              <LockOpen v-else :size="9" />
            </span>
          </span>
          <span class="switch__text">固定本站</span>
        </button>
        <span class="edit__acts">
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
          <!-- 面板比一屏高时，顶部那颗 × 已经滚出视野了：出口在末尾再给一次。 -->
          <button class="btn btn--sm" type="button" @click="closePanel">收起详情</button>
        </span>
      </div>
    </div>
  </li>
</template>

<style scoped>
.place {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
  padding: 8px 10px;
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out),
    box-shadow var(--dur) var(--ease-out),
    transform var(--dur) var(--ease-out);
}

/* 正文行＝拖拽把手（`useDragSort` 的 handle 就指这里）。编辑面板故意不在这行里：
   否则按住备注框选两个字就把整张卡拖走了，时刻轨道也拖不动。 */
.place__row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-start;
}

/* 站点：一枚落在轨道中线上的实心圆（--dc 由这一天继承下来，所以颜色本身就是「第几天」）。
   白环负责把它从虚线上摘出来，外圈 --ink 保证深色态下仍认得出边界。
   挂在卡片上而不是列表上：重排与 hover 位移时节点要跟着这一站走。 */
.place::before {
  position: absolute;
  top: 15px;
  left: -17px;
  width: 10px;
  height: 10px;
  content: "";
  background: var(--dc, var(--accent));
  border: 2px solid var(--surface);
  border-radius: 50%;
  box-shadow: 0 0 0 1px var(--ink);
  transition: transform var(--dur) var(--ease-pop);
}

.place--active::before {
  transform: scale(1.25);
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

/* 鼠标端整行都能拖了，这一枚仍是触屏上唯一的把手：touch-action:none 只写在把手上，
   手指划过卡片正文照常滚动列表，按住 grip 不动才算拖动。常亮是因为它的职责按设备而变，
   不亮就没人知道触屏该按哪里。 */
.place__drag {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  align-self: stretch;
  padding: 0 2px;
  color: var(--text-2);
  cursor: grab;
  user-select: none;
  touch-action: none;
}

.place__drag:active {
  cursor: grabbing;
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
  border-radius: var(--radius-pill);
}

.place__body {
  /* basis 必须是 0：换行是在「收缩之前」按假想主轴尺寸定的，而标题那一列的自动 basis
     就把 357px 的卡片撑满了 —— 于是右侧的 ⋯/× 被整块挤到第二行，孤零零挂在正文下面。
     给 0 之后第一行永远装得下，这一列照样靠 grow 吃掉剩余宽度。 */
  flex: 1 1 0;
  min-width: 0;
}

.place__title {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 5px;
  align-items: baseline;
}

.place__name {
  /* 必须给个下限：overflow:hidden 把这枚 flex 项的自动最小尺寸压成 0，于是窄屏上
     旁边的「某某 正在编辑」会把地名挤成一两个省略号，而不是把自己换到下一行去。 */
  min-width: 5em;
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
  /* 竖着叠：这一行在拖把手、照片、时间列之后只剩 ~280px，横着排两颗按钮要吃掉 58px，
     地点名就会被挤到「成都大熊猫繁育研究…」。叠成竖列只占 26px，名字回到十一个字。 */
  flex-direction: column;
  flex: 0 0 auto;
  gap: 0;
  align-items: center;
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}

.place:hover .place__side,
.place--active .place__side {
  opacity: 1;
}

.place__close,
.place__menu,
.place__delete {
  padding: 2px 6px;
  font-size: 13px;
}

/* 出口与删除挨着长，颜色必须分开：× 走主色，垃圾桶才走危险色。 */
.place__close:hover {
  color: var(--accent-strong);
  background: var(--accent-soft);
}

/* 这三颗挨着，用 .tap-pad 向外撑会互相盖住落点——直接加高更诚实。 */
@media (max-width: 860px) {
  .place__close,
  .place__menu,
  .place__delete {
    min-height: 30px;
  }

  /* 竖叠在桌面上省的是宽度，手机上正相反：宽度富余（正文列 218px 起），高度才贵——
     两颗 30px 摞起来会把每一行都撑高 12px。所以手机上回到横排。 */
  .place__side {
    flex-direction: row;
    gap: 2px;
    align-items: flex-start;
  }

  /* 把手在卡片最左边，左右没有别的落点，可以安心撑宽到可指按的尺寸。 */
  .place__drag {
    min-width: 28px;
    justify-content: center;
    padding: 0;
  }
}

/* ---------- 编辑面板 ---------- */

.place__edit {
  display: flex;
  flex-direction: column;
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
  border: 1px solid var(--ink);
  border-radius: var(--radius-pill);
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
  border: 1px solid var(--ink);
  border-radius: var(--radius-pill);
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

/* 右端那一组：导航是外链、收起是这块面板的出口，并排同权重，窄了就换行。 */
.edit__acts {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
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
  border: 1px solid var(--ink);
  border-radius: var(--radius-pill);
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
