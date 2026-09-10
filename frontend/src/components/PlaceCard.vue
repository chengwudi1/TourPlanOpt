<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { Ellipsis, GripVertical, Lock, LockOpen, MapPin, Navigation, X } from '@/components/icons'
import type { Place } from '@/types/domain'
import { formatDuration, formatMin, parseHHMM } from '@/utils/time'

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

const duration = ref(String(props.place.duration_min))
const note = ref(props.place.note)
/** HH:MM for the <input type="time">; seeds from user_start_min -- the hand-set
 * value. start_min is server-derived and would auto-fill the input (and make
 * re-picking the same time a silent no-op). */
const startTime = ref(startText(props.place.user_start_min))

function startText(startMin: number | null): string {
  if (startMin === null || startMin === undefined) return ''
  // formatMin may prefix 次日 for past-midnight times; the time input only shows HH:MM.
  return formatMin(startMin).slice(-5)
}

// Re-seed when the card opens: the row may have changed under us since last time.
watch(
  () => props.active,
  (active) => {
    if (active) {
      duration.value = String(props.place.duration_min)
      note.value = props.place.note
      startTime.value = startText(props.place.user_start_min)
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

function commitDuration() {
  const value = Math.max(0, Math.min(24 * 60, Math.round(Number(duration.value) || 0)))
  duration.value = String(value)
  if (value !== props.place.duration_min) {
    emit('patch', props.place, { duration_min: value })
  }
}

function commitNote() {
  if (note.value !== props.place.note) {
    emit('patch', props.place, { note: note.value })
  }
}

/** '' clears the hand-set time (and thereby the pin, server-side). */
function commitStart(value: string) {
  const minutes = value.trim() === '' ? null : parseHHMM(value)
  if (value.trim() !== '' && minutes === null) {
    startTime.value = startText(props.place.user_start_min)
    return
  }
  if ((minutes ?? null) !== (props.place.user_start_min ?? null)) {
    emit('patch', props.place, { start_min: minutes })
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

      <div v-if="place.note" class="place__note tiny">{{ place.note }}</div>

      <!-- Editor: only on the selected card. LWW means typing here can never clobber
           someone else's edit to the OTHER field of the same card. -->
      <div v-if="active" class="place__edit" @click.stop>
        <label class="place__field">
          <span class="tiny muted">停留（分钟）</span>
          <input
            v-model="duration"
            class="input"
            type="number"
            min="0"
            max="1440"
            step="5"
            @blur="commitDuration"
            @keydown.enter="($event.target as HTMLInputElement).blur()"
          />
        </label>
        <label class="place__field">
          <span class="tiny muted">开始时间（设置即锁定；留空自动排）</span>
          <input
            :value="startTime"
            class="input"
            type="time"
            @change="commitStart(($event.target as HTMLInputElement).value)"
          />
        </label>
        <label class="place__field">
          <span class="tiny muted">备注</span>
          <textarea
            v-model="note"
            class="input"
            rows="2"
            placeholder="例如：19:00 已订座"
            @blur="commitNote"
          />
        </label>
        <div class="place__editrow">
          <button class="btn btn--sm" type="button" @click="emit('lock', place, !place.locked)">
            <LockOpen v-if="place.locked" class="ic" :size="13" />
            <Lock v-else class="ic" :size="13" />
            {{ place.locked ? '取消锁定' : '锁定位置' }}
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
  </li>
</template>

<style scoped>
.place {
  display: flex;
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

/* 序号＝地图标记上的同一个数字与同一个创建者颜色，读图与读列表因此对得上。 */
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

.place__edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}

.place__field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.place__field .input {
  font-size: 13px;
}

.place__editrow {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.place__editrow a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
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

/* 触屏没有 hover：把手与行内按钮常驻，否则无法拖动或删除。 */
@media (hover: none) {
  .place__drag,
  .place__side {
    opacity: 1;
  }
}

</style>
