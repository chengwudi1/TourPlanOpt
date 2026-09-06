<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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
/** HH:MM for the <input type="time">; null start = empty string (auto-schedule). */
const startTime = ref(startText(props.place.start_min))

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
      startTime.value = startText(props.place.start_min)
    }
  },
)

const ringStyle = computed(() =>
  props.editingBy
    ? { borderColor: props.editingBy.color, boxShadow: `0 0 0 3px ${props.editingBy.color}33` }
    : undefined,
)

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
    startTime.value = startText(props.place.start_min)
    return
  }
  if ((minutes ?? null) !== (props.place.start_min ?? null)) {
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
    <span class="place__drag" title="拖动排序" aria-hidden="true">⋮⋮</span>
    <span
      class="place__order"
      :class="{ 'place__order--locked': place.locked }"
      :style="creatorColor ? { background: creatorColor } : undefined"
    >{{ index + 1 }}</span>

    <div class="place__body">
      <div class="place__title">
        <span class="place__name">{{ place.name }}</span>
        <span v-if="place.locked" class="place__pin tiny" title="已锁定，优化时不参与重排">📌</span>
        <span v-if="editingBy" class="place__editor tiny" :style="{ color: editingBy.color }">
          {{ editingBy.name }} 正在编辑
        </span>
      </div>

      <div v-if="place.address" class="place__address tiny muted">{{ place.address }}</div>

      <div class="place__facts tiny muted">
        <span>停留 {{ formatDuration(place.duration_min) }}</span>
        <span v-if="place.start_min !== null">· {{ formatMin(place.start_min) }} 开始</span>
        <span v-if="place.status === 'confirmed'" class="place__confirmed">· 已确认</span>
      </div>

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
          <span class="tiny muted">开始时间（设置即锁定 📌；留空自动排）</span>
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
            {{ place.locked ? '📍 取消锁定' : '📌 锁定位置' }}
          </button>
          <a
            v-if="navHref"
            class="btn btn--sm btn--ghost"
            :href="navHref"
            target="_blank"
            rel="noopener"
            title="跳转到高德地图导航"
          >
            🧭 导航到这里
          </a>
        </div>
      </div>
    </div>

    <div class="place__side">
      <button
        class="btn btn--sm btn--ghost place__menu"
        title="更多操作"
        @click.stop="emit('menu', place, { x: $event.clientX, y: $event.clientY })"
      >
        ⋯
      </button>
      <button
        class="btn btn--sm btn--ghost place__delete"
        title="删除这个地点"
        @click.stop="emit('remove', place)"
      >
        ✕
      </button>
    </div>
  </li>
</template>

<style scoped>
.place {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 10px;
  cursor: pointer;
  transition:
    border-color 0.12s ease,
    box-shadow 0.15s ease,
    transform 0.15s ease;
}

.place--ghost {
  opacity: 0.4;
}

.place--active {
  border-color: var(--accent);
}

.place__drag {
  flex: 0 0 auto;
  padding: 0 2px;
  color: var(--text-3);
  font-size: 12px;
  letter-spacing: -2px;
  cursor: grab;
  user-select: none;
}

.place__order {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 22px;
  height: 22px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  background: var(--accent);
  border-radius: 50%;
}

.place__order--locked {
  background: var(--warn);
}

.place__body {
  flex: 1 1 auto;
  min-width: 0;
}

.place__title {
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.place__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.place__editor {
  white-space: nowrap;
}

.place__address,
.place__note {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.place__facts {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.place__confirmed {
  color: var(--ok);
}

.place__note {
  padding: 4px 6px;
  margin-top: 6px;
  white-space: pre-wrap;
  background: var(--bg);
  border-radius: 4px;
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

a.place__editrow,
.place__editrow a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.place__side {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 2px;
}

.place__menu,
.place__delete {
  padding: 2px 7px;
  font-size: 13px;
}
</style>
