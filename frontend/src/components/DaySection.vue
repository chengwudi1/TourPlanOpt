<script setup lang="ts">
import { computed, ref } from 'vue'

import PlaceCard from '@/components/PlaceCard.vue'
import type { RailMark } from '@/components/TimeRail.vue'
import { BedDouble, Car, ChevronDown, Flag, Footprints, Navigation, Ruler, X, Zap } from '@/components/icons'
import { useDragSort } from '@/composables/useDragSort'
import { useFlipList } from '@/composables/useFlipList'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { OptimizeResult } from '@/stores/trip'
import type { Day, Place } from '@/types/domain'
import { formatDistance, haversineM } from '@/utils/coords'
import { formatDuration, formatMin } from '@/utils/time'

/**
 * TREK 式「一天一个可折叠区块」。选中态（加地点/优化的目标）与展开态分离：
 * 点天头 = 选中（父组件负责顺带展开），点箭头 = 折叠/展开；展开态由父组件持久化。
 * 区块内部自成一体：bookend、地点行 + 路线连接行、控件排挂，全部读同一个 store。
 */
const props = defineProps<{
  day: Day
  expanded: boolean
}>()

const emit = defineEmits<{
  select: []
  toggle: []
  rename: []
  remove: []
  menu: [place: Place, pos: { x: number; y: number }]
}>()

const store = useTripStore()
const socket = useSocketStore()

const places = computed(() =>
  store.places
    .filter((p) => p.day_id === props.day.id)
    .slice()
    .sort((a, b) => a.sort_index - b.sort_index),
)

const timeline = computed(() => store.timelines[props.day.id] ?? null)
const warnings = computed(() => timeline.value?.warnings ?? [])
const result = computed(() =>
  store.optimizeResult?.day_id === props.day.id ? (store.optimizeResult as OptimizeResult) : null,
)
const dragger = computed(() => store.draggersByDay.get(props.day.id) ?? null)
const isSelected = computed(() => store.currentDayId === props.day.id)
const startPlace = computed(() => places.value.find((p) => p.id === props.day.start_place_id) ?? null)
const endPlace = computed(() => places.value.find((p) => p.id === props.day.end_place_id) ?? null)

/** 天头概要：`N 个地点 · 09:00–21:40`。收束时刻先取服务端排程的 end_min（它把当天每一
 * 站都算进去了），再退回终点锚到达、最后一站推导值。直接用行数据会在「刚加的地点还没
 * 排程」时把 null 当 0，得出 09:00–01:00 这种荒谬区间。 */
const rangeText = computed(() => {
  const list = places.value
  const start = list[0]?.start_min
  if (start === null || start === undefined) return ''
  const scheduled = list.filter((p) => p.start_min !== null)
  const last = scheduled[scheduled.length - 1]
  const end =
    endPlace.value?.arrive_min ??
    timeline.value?.end_min ??
    (last ? (last.start_min ?? 0) + last.duration_min : null)
  return end === null || end === undefined ? '' : `${formatMin(start)}–${formatMin(end)}`
})

// -- 行内路线连接（RouteConnector）：时间来自排程，距离前端 haversine 现算。 ----------

const AMAP_URI_MODE = { driving: 'car', walking: 'walk', straight: 'car' } as const
const MODE_ICONS = { driving: Car, walking: Footprints, straight: Ruler } as const

const modeIcon = computed(() => MODE_ICONS[store.trip?.travel_mode ?? 'driving'])

function legText(place: Place, index: number): string {
  const parts: string[] = []
  if (place.travel_min_before !== null) parts.push(`约 ${formatDuration(place.travel_min_before)}`)
  else parts.push('路程未知')
  if (index > 0) {
    const prev = places.value[index - 1]
    parts.push(formatDistance(haversineM(prev, place)))
  }
  return parts.join(' · ')
}

/** 高德 URI 全天导航：URI API 最多一个途经点，恰好 3 站时才带 via。 */
function dayNavUrl(): string | null {
  const list = places.value
  if (list.length < 2) return null
  const mode = AMAP_URI_MODE[store.trip?.travel_mode ?? 'driving']
  const common = `mode=${mode}&src=tourplanopt&coordinate=gaode&callnative=0`
  const point = (p: { lng: number; lat: number; name: string }) =>
    `${p.lng},${p.lat},${encodeURIComponent(p.name)}`
  const from = point(list[0])
  const to = point(list[list.length - 1])
  const via = list.length === 3 ? `&via=${point(list[1])}` : ''
  return `https://uri.amap.com/navigation?from=${from}&to=${to}${via}&${common}`
}

/** 卡片↔卡片导航链接（URI API：前一站 → 这一站）。 */
function navHref(place: Place, index: number): string | null {
  const mode = AMAP_URI_MODE[store.trip?.travel_mode ?? 'driving']
  const common = `mode=${mode}&src=tourplanopt&coordinate=gaode&callnative=0`
  const to = `${place.lng},${place.lat},${encodeURIComponent(place.name)}`
  if (index === 0) return `https://uri.amap.com/navigation?to=${to}&${common}`
  const prev = places.value[index - 1]
  const from = `${prev.lng},${prev.lat},${encodeURIComponent(prev.name)}`
  return `https://uri.amap.com/navigation?from=${from}&to=${to}&${common}`
}

/** 时刻轨道上的参照点：同一天里已经排定时刻的其它站。挑时刻要能指着别的站说
 * 「在它后面一小时」，所以基准只能是看得见的站，不能是一个算出来的抽象位置。 */
function railMarks(exceptId: string): RailMark[] {
  return places.value
    .filter((p) => p.id !== exceptId && p.start_min !== null)
    .map((p) => ({ name: p.name, min: p.start_min as number }))
}

// -- 拖拽：只在本区块内排序；拖动状态作为 presence 广播给同房间。 --------------------

const listEl = ref<HTMLElement | null>(null)
useDragSort(
  listEl,
  (orderedIds) => store.reorderDay(props.day.id, orderedIds),
  (dragging) =>
    socket.sendPresence(props.day.id, store.selectedPlaceId, dragging ? props.day.id : null),
)
// 一键优化、别人把地点挪走、跨天移动之后，行与行之间要看得见位移而不是瞬间换位。
useFlipList(
  listEl,
  () => places.value.map((p) => p.id),
)

/** 别人正在编辑哪张卡（光环），只关心本天内的。 */
const editorsByPlace = computed(() => {
  const map = new Map<string, { name: string; color: string }>()
  for (const p of store.presence) {
    if (!p.focusing_place_id) continue
    const place = store.places.find((x) => x.id === p.focusing_place_id)
    if (place?.day_id !== props.day.id) continue
    map.set(p.focusing_place_id, { name: p.name, color: p.color })
  }
  return map
})

// -- 控件排挂 -------------------------------------------------------------------------

const precise = ref(false)

function runOptimize() {
  void store.optimize(precise.value ? 'amap' : 'haversine', props.day.id)
}
</script>

<template>
  <section
    class="daysec card"
    :class="{ 'daysec--open': expanded, 'daysec--on': isSelected }"
  >
    <header
      class="daysec__head"
      :title="isSelected ? '双击重命名这一天' : '点击选中这一天'"
      @click="emit('select')"
      @dblclick.prevent="emit('rename')"
    >
      <button
        class="daysec__arrow"
        type="button"
        :aria-label="expanded ? '折叠这一天' : '展开这一天'"
        @click.stop="emit('toggle')"
      >
        <ChevronDown :size="15" />
      </button>
      <span class="daysec__badge">D{{ day.day_index + 1 }}</span>
      <span class="daysec__title">
        {{ day.title || `第 ${day.day_index + 1} 天` }}
        <span v-if="warnings.length && !expanded" class="daysec__warnbit" title="这一天有排程提醒">
          !
        </span>
      </span>
      <span class="daysec__meta tiny">
        {{ places.length }} 个地点<template v-if="rangeText"> · {{ rangeText }}</template>
      </span>

      <span v-if="dragger" class="daysec__drag tiny" :style="{ borderColor: dragger.color }">
        {{ dragger.name }} 正在调整顺序…
      </span>
      <button
        v-else-if="!places.length && store.days.length > 1"
        class="daysec__del"
        type="button"
        title="删除这个空的天"
        @click.stop="emit('remove')"
      >
        <X :size="12" />
      </button>
    </header>

    <div class="daysec__fold" :class="{ 'is-open': expanded }">
      <div class="daysec__foldclip">
        <div class="daysec__body">
          <div v-if="startPlace" class="bookend tiny">
            <Flag class="ic" :size="12" />
            从「{{ startPlace.name }}」出发
          </div>

          <ul
            v-if="places.length"
            ref="listEl"
            class="daysec__list"
            :class="{ 'daysec__list--locked': !!dragger }"
          >
            <template v-for="(place, index) in places" :key="place.id">
              <li v-if="index > 0" class="leg" :data-flip-key="`leg:${place.id}`">
                <span class="leg__line" />
                <component :is="modeIcon" class="leg__icon" :size="12" />
                <span class="leg__text tiny">{{ legText(place, index) }}</span>
                <span class="leg__line" />
              </li>
              <PlaceCard
                :place="place"
                :index="index"
                :active="place.id === store.selectedPlaceId"
                :editing-by="editorsByPlace.get(place.id) ?? null"
                :nav-href="navHref(place, index)"
                :creator-color="store.creatorColorOf(place.added_by)"
                :marks="railMarks(place.id)"
                @select="store.selectPlace(place.id)"
                @remove="store.deletePlace(place.id)"
                @lock="(p, locked) => store.setPlaceLocked(p.id, locked)"
                @patch="(p, patch) => store.updatePlace(p.id, patch)"
                @menu="(p, pos) => emit('menu', p, pos)"
              />
            </template>
          </ul>

          <p v-else class="daysec__empty tiny muted">这一天还没有安排。选中后从上面搜索或「发现」里挑。</p>

          <div v-if="endPlace" class="bookend bookend--end tiny">
            <BedDouble class="ic" :size="12" />
            <template v-if="endPlace.arrive_min !== null">预计 {{ formatMin(endPlace.arrive_min) }} 到「{{ endPlace.name }}」</template>
            <template v-else>到「{{ endPlace.name }}」收尾</template>
          </div>

          <div v-if="warnings.length" class="daysec__warnings tiny">
            <div v-for="(w, i) in warnings" :key="i">{{ w }}</div>
          </div>

          <div v-if="places.length >= 2" class="daysec__actions">
            <template v-if="result">
              <span class="tiny muted daysec__summary">
                优化后全程交通 {{ formatDuration(result.summary.after_min) }}
                <template v-if="result.summary.saved_min > 0">
                  · 节省 {{ formatDuration(result.summary.saved_min) }}
                </template>
                <template v-if="!result.exact">（启发式解）</template>
              </span>
              <button class="btn btn--sm" type="button" @click="store.undoOptimize()">撤销优化</button>
              <button class="btn btn--sm btn--ghost" type="button" @click="store.dismissOptimizeResult()">
                知道了
              </button>
            </template>
            <template v-else>
              <button
                class="btn btn--primary btn--sm"
                type="button"
                :disabled="store.optimizing || places.length < 3"
                @click="runOptimize"
              >
                <Zap class="ic" :size="13" /> {{ store.optimizing ? '优化中…' : '一键优化顺序' }}
              </button>
              <label class="daysec__precise tiny muted" title="用高德真实路况建距离矩阵（消耗配额）；默认用直线距离，瞬间完成且不耗配额">
                <input v-model="precise" type="checkbox" :disabled="store.optimizing" />
                精确优化
              </label>
              <a
                v-if="dayNavUrl()"
                class="btn btn--sm btn--ghost"
                :href="dayNavUrl()!"
                target="_blank"
                rel="noopener"
                title="把这条路线发到高德地图"
              >
                <Navigation class="ic" :size="13" /> 全天导航
              </a>
            </template>
          </div>
          <p v-if="places.length >= 2 && places.length < 3 && !result" class="tiny muted daysec__hint">
            至少 3 个地点才可优化
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.daysec {
  overflow: hidden;
}

.daysec--on {
  border-color: var(--accent);
}

.daysec__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 9px 10px;
  cursor: pointer;
  user-select: none;
  transition: background var(--dur-fast) var(--ease-out);
}

.daysec__head:hover {
  background: var(--surface-hover);
}

.daysec__arrow {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: var(--text-3);
  background: none;
  border: 0;
  border-radius: 6px;
}

.daysec__arrow svg {
  transition: transform var(--dur) var(--ease-inout);
}

.daysec--open .daysec__arrow svg {
  transform: rotate(180deg);
}

.daysec__badge {
  flex: 0 0 auto;
  padding: 1px 7px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-radius: 999px;
}

.daysec__title {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daysec__warnbit {
  display: inline-grid;
  place-items: center;
  width: 14px;
  height: 14px;
  font-size: 10px;
  font-weight: 700;
  color: var(--warn);
  background: var(--warn-soft);
  border: 1px solid var(--warn-border);
  border-radius: 50%;
}

.daysec__meta {
  flex: 1 1 auto;
  overflow: hidden;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daysec__drag {
  flex: 0 0 auto;
  padding: 2px 8px;
  border: 1px dashed var(--border-strong);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  animation: daysec-drag-in var(--dur) var(--ease-out);
}

@keyframes daysec-drag-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
}

.daysec__del {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--text-3);
  background: none;
  border: 0;
  border-radius: 50%;
}

.daysec__del:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

/* 展开/收起＝grid-template-rows 在 0fr↔1fr 之间插值。
   reduced-motion 下全局规则把 transition 关掉，直接开合，不需要 JS 参与。 */
.daysec__fold {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur-slow) var(--ease-inout);
}

.daysec__fold.is-open {
  grid-template-rows: 1fr;
}

/* 裁剪交给这层不带 padding 与边框的壳：0fr 时它的盒高才是真的 0。让带留白和分隔线的 body
   自己缩，它最低也有 13px，收起态就是一条永远消不掉的残影。 */
.daysec__foldclip {
  min-height: 0;
  overflow: hidden;
  visibility: hidden;
  transition: visibility 0s linear var(--dur-slow);
}

/* 收起时内容只是被裁掉、没被移出焦点链：延迟到动画结束再隐，展开则立刻可见。 */
.daysec__fold.is-open .daysec__foldclip {
  visibility: visible;
  transition-delay: 0s;
}

.daysec__body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px 10px 10px;
  border-top: 1px solid var(--border-faint);
}

/* 展开时内容分两拍落位：书挡先到、列表跟上，折叠动画就不再是一整块被"掀开"。
   只动 opacity 不动 transform——列表容器上挂着 sortablejs，祖先有位移会让它在拖拽
   起点量到错的几何；动画用 backwards，结束后不留合成层也不留层叠上下文。 */
.daysec__fold.is-open .daysec__body > * {
  animation: fade-in var(--dur-slow) var(--ease-out) backwards;
}
.daysec__fold.is-open .daysec__body > *:nth-child(2) {
  animation-delay: calc(var(--stagger) * 1);
}
.daysec__fold.is-open .daysec__body > *:nth-child(n + 3) {
  animation-delay: calc(var(--stagger) * 2);
}

.bookend {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 4px 2px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

.bookend--end {
  justify-content: flex-end;
}

.daysec__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.daysec__list--locked {
  opacity: 0.6;
  pointer-events: none;
}

.leg {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 0 0 0 14px;
  list-style: none;
}

.leg__line {
  flex: 1 1 auto;
  height: 1px;
  background: var(--border-strong);
}

.leg__icon {
  flex: 0 0 auto;
  color: var(--text-3);
}

.leg__text {
  flex: 0 0 auto;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.daysec__empty {
  padding: 10px 12px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

.daysec__warnings {
  padding: 7px 10px;
  color: var(--warn);
  background: var(--warn-soft);
  border-radius: var(--radius-sm);
}

.daysec__actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  padding-top: 2px;
}

.daysec__summary {
  flex: 1 1 auto;
  min-width: 0;
  font-variant-numeric: tabular-nums;
}

.daysec__precise {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.daysec__hint {
  margin: -4px 0 0;
}

.daysec__actions a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}
</style>
