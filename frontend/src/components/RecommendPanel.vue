<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  ChevronDown,
  Compass,
  ExternalLink,
  Landmark,
  MoonStar,
  Plus,
  ShoppingBasket,
  UtensilsCrossed,
} from '@/components/icons'
import SegmentedControl, { type SegOption } from '@/components/SegmentedControl.vue'
import { useTripStore } from '@/stores/trip'
import type { Place, Poi } from '@/types/domain'
import { apiFetch } from '@/utils/api'

const props = defineProps<{ city: string; tripId: string }>()

const store = useTripStore()

type Sort = 'composite' | 'hot' | 'distance'

const open = ref(false)
const category = ref<'scenic' | 'food' | 'night'>('scenic')
const sort = ref<Sort>('composite')
const pois = ref<Poi[]>([])
const amapUrl = ref('')
const loading = ref(false)
const error = ref<{ message: string } | null>(null)
const loaded = ref(new Set<string>())
// 在途去重：loaded 要等响应回来才写，同一 key 在那之前能被 watch 的多次触发钻过去
// （实测挪一次基准点就发出两条一模一样的请求）。配额在服务端有缓存兜着，但这条请求本就不该发。
const inFlight = new Set<string>()

const CATEGORIES: {
  key: 'scenic' | 'food' | 'night'
  label: string
  icon: typeof Landmark
}[] = [
  { key: 'scenic', label: '景点', icon: Landmark },
  { key: 'food', label: '小吃美食', icon: UtensilsCrossed },
  { key: 'night', label: '夜市', icon: MoonStar },
]

const SORT_OPTIONS = computed<SegOption[]>(() => [
  { value: 'composite', label: '综合' },
  { value: 'hot', label: '热度' },
  { value: 'distance', label: '距离', disabled: !origin.value },
])

/* ---------- 距离基准：当天地点中心 → 全行程地点中心 → 没有 ---------- */

function centroid(list: Place[]): [number, number] | null {
  if (!list.length) return null
  let lng = 0
  let lat = 0
  for (const p of list) {
    lng += p.lng
    lat += p.lat
  }
  return [lng / list.length, lat / list.length]
}

const origin = computed<[number, number] | null>(() => {
  const today = store.currentPlaces
  if (today.length) return centroid(today)
  return centroid(store.places)
})
/** 基准点是从哪儿来的，直接写在界面上——「距此」的「此」不能让人猜。 */
const originScope = computed<'day' | 'trip' | null>(() => {
  if (!origin.value) return null
  return store.currentPlaces.length ? 'day' : 'trip'
})
const originNote = computed(() => {
  if (originScope.value === 'day') {
    return `距离按这一天已加入的 ${store.currentPlaces.length} 个地点的中心算（直线距离）`
  }
  if (originScope.value === 'trip') {
    return '这一天还没有地点，距离按整个行程已加入地点的中心算（直线距离）'
  }
  return '先加入一个地点，才能按距离排'
})

/* ---------- 列表高度与排序：按行程记住 ---------- */

const DEFAULT_H = 300
const MIN_H = 140
const listH = ref(DEFAULT_H)
const prefsKey = computed(() => `tourplanopt.reco-${props.tripId}`)

function maxH() {
  return Math.max(MIN_H, Math.min(Math.round(window.innerHeight * 0.7), 760))
}
function clampH(px: number) {
  return Math.min(maxH(), Math.max(MIN_H, Math.round(px)))
}

function loadPrefs() {
  let raw: unknown = null
  try {
    raw = JSON.parse(localStorage.getItem(prefsKey.value) ?? 'null')
  } catch {
    return // 脏数据与无痕模式一样：回到默认，不连带面板一起坏
  }
  if (!raw || typeof raw !== 'object') return
  const { h, sort: saved } = raw as { h?: unknown; sort?: unknown }
  if (typeof h === 'number' && Number.isFinite(h)) listH.value = clampH(h)
  if (saved === 'composite' || saved === 'hot' || saved === 'distance') sort.value = saved
}

function persistPrefs() {
  try {
    localStorage.setItem(prefsKey.value, JSON.stringify({ h: listH.value, sort: sort.value }))
  } catch {
    // 无痕模式：丢掉偏好不影响功能
  }
}

/* ---------- 无把手拖拽改高度 ---------- */

const dragging = ref(false)
let dragPointer = 0
let dragStartY = 0
let dragStartH = 0

function onGrabDown(e: PointerEvent) {
  dragPointer = e.pointerId
  dragStartY = e.clientY
  dragStartH = listH.value
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onGrabMove(e: PointerEvent) {
  if (!dragging.value || e.pointerId !== dragPointer) return
  listH.value = clampH(dragStartH + (e.clientY - dragStartY))
}

function onGrabUp(e: PointerEvent) {
  if (!dragging.value || e.pointerId !== dragPointer) return
  dragging.value = false
  persistPrefs()
}

function nudge(delta: number) {
  listH.value = clampH(listH.value + delta)
  persistPrefs()
}

// 基准点只当缓存键用，取三位小数（≈100m）：加一个地点值得重排，挪动 20 米不值得。
const originKey = computed(() =>
  origin.value ? `${origin.value[0].toFixed(3)},${origin.value[1].toFixed(3)}` : 'none',
)

watch(
  () => [props.city, open.value, category.value, sort.value, originKey.value] as const,
  async ([city, isOpen]) => {
    if (!city || !isOpen) return
    const key = `${city}:${category.value}:${sort.value}:${originKey.value}`
    if (loaded.value.has(key) || inFlight.has(key)) return
    inFlight.add(key)
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams({ city, category: category.value, sort: sort.value })
      if (sort.value === 'distance' && origin.value) {
        const [lng, lat] = origin.value
        params.set('origin', `${lng.toFixed(6)},${lat.toFixed(6)}`)
      }
      const data = await apiFetch<{ pois: Poi[]; amap_url: string }>(
        `/api/city/recommendations?${params}`,
      )
      pois.value = data.pois
      amapUrl.value = data.amap_url
      loaded.value.add(key)
    } catch (err) {
      error.value = { message: (err as Error).message || '推荐加载失败' }
    } finally {
      loading.value = false
      inFlight.delete(key)
    }
  },
)

function fmtDistance(meters: number | null | undefined): string | null {
  if (meters == null) return null
  if (meters < 1000) return `${Math.max(10, Math.round(meters / 10) * 10)} m`
  if (meters < 10000) return `${(meters / 1000).toFixed(1)} km`
  return `${Math.round(meters / 1000)} km`
}

function add(poi: Poi) {
  store.addPlace({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function stash(poi: Poi) {
  store.stashAdd({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function added(poi: Poi): boolean {
  return store.currentPlaces.some((p) => p.amap_poi_id && p.amap_poi_id === poi.id)
}

onMounted(loadPrefs)

defineExpose({ show: () => (open.value = true) })
</script>

<template>
  <div class="reco card" :class="{ 'reco--dragging': dragging }" :style="{ '--reco-h': `${listH}px` }">
    <button class="reco__head" type="button" @click="open = !open">
      <strong class="reco__label"><Compass class="ic" :size="14" /> 发现</strong>
      <span class="tiny muted">
        {{ city ? `${city}的景点、小吃和夜市，点一下就加入行程` : '点天头的城市 chip 先定目的地，这里才有景点和夜市' }}
      </span>
      <span class="reco__chevron" :class="{ 'reco__chevron--open': open }">
        <ChevronDown :size="15" />
      </span>
    </button>

    <div v-if="open" class="reco__body">
      <div class="reco__tabs">
        <button
          v-for="c in CATEGORIES"
          :key="c.key"
          class="reco__tab"
          :class="{ 'reco__tab--on': category === c.key }"
          type="button"
          @click="category = c.key"
        >
          <component :is="c.icon" class="ic" :size="13" /> {{ c.label }}
        </button>
      </div>

      <SegmentedControl v-model="sort" :options="SORT_OPTIONS" label="发现结果的排序方式" />
      <p v-if="sort === 'distance'" class="reco__note tiny muted">{{ originNote }}</p>

      <p v-if="!city" class="reco__empty tiny muted">
        点行程名右侧的城市标签设一个目的地，这里就会推荐景点、小吃和夜市。
      </p>
      <p v-else-if="loading" class="reco__empty tiny muted">正在找 {{ city }} 的好去处…</p>
      <p v-else-if="error" class="reco__empty tiny muted">{{ error.message }}（稍后再试）</p>

      <ul v-else-if="pois.length" class="reco__list">
        <li v-for="poi in pois" :key="poi.id || poi.name" class="reco__item">
          <img
            v-if="poi.photo"
            class="reco__photo"
            :src="poi.photo"
            :alt="`${poi.name} 的照片`"
            loading="lazy"
            referrerpolicy="no-referrer"
          />
          <div class="reco__info">
            <div class="reco__name">{{ poi.name }}</div>
            <div class="tiny muted reco__addr">
              {{ poi.address || poi.district }}<template v-if="poi.distance_m != null">
                · 距此 {{ fmtDistance(poi.distance_m) }}</template
              >
            </div>
          </div>
          <button
            class="btn btn--sm"
            type="button"
            title="先存进想去清单"
            @click="stash(poi)"
          >
            <ShoppingBasket class="ic" :size="13" />
          </button>
          <button
            class="btn btn--sm"
            type="button"
            :disabled="added(poi)"
            @click="add(poi)"
          >
            <template v-if="!added(poi)"><Plus class="ic" :size="13" /> 加入</template>
            <template v-else>已加入</template>
          </button>
        </li>
      </ul>
      <p v-else class="reco__empty tiny muted">这一类暂时没有推荐，换个类目或排序试试。</p>

      <a
        v-if="amapUrl"
        class="reco__amap tiny"
        :href="amapUrl"
        target="_blank"
        rel="noopener"
      >
        <ExternalLink class="ic" :size="12" /> 在高德地图中查看更多
      </a>
    </div>

    <!-- 底部这条既是「还有得拖」的提示，也是把手本身：整条 14px 都是热区。 -->
    <div
      v-if="open"
      class="reco__resize"
      role="separator"
      tabindex="0"
      aria-orientation="horizontal"
      aria-label="调整发现列表的高度"
      :aria-valuenow="listH"
      :aria-valuemin="MIN_H"
      :aria-valuemax="maxH()"
      title="拖动或按上下方向键调整列表高度"
      @pointerdown="onGrabDown"
      @pointermove="onGrabMove"
      @pointerup="onGrabUp"
      @pointercancel="onGrabUp"
      @keydown.arrow-up.prevent="nudge(-24)"
      @keydown.arrow-down.prevent="nudge(24)"
    />
  </div>
</template>

<style scoped>
.reco {
  overflow: hidden;
}

/* 拖高度时列表里的文字会被顺手选成一片蓝，整块关掉。 */
.reco--dragging {
  user-select: none;
}

.reco__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  width: 100%;
  padding: 11px 12px;
  text-align: left;
  background: none;
  border: 0;
}

.reco__label {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  flex: 0 0 auto;
  white-space: nowrap;
}

.reco__head span:not(.reco__chevron) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reco__chevron {
  color: var(--text-3);
  transition: transform var(--dur) var(--ease-inout);
}
.reco__chevron--open {
  transform: rotate(180deg);
}

.reco__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 12px 12px;
}

.reco__tabs {
  display: flex;
  gap: 6px;
}

.reco__tab {
  padding: 5px 11px;
  font-size: 13px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: 999px;
}

.reco__tab--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.reco__note {
  margin: -4px 0 0;
}

.reco__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: var(--reco-h, 300px);
  padding: 0;
  margin: 0;
  overflow-y: auto;
  list-style: none;
}

.reco__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
}

.reco__photo {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 10px;
  background: var(--surface-2);
}

.reco__item:hover {
  background: var(--surface-2);
}

.reco__info {
  flex: 1;
  min-width: 0;
}

.reco__name {
  font-size: 14px;
  font-weight: 600;
}

.reco__addr {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reco__empty {
  padding: 10px 12px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

.reco__amap {
  color: var(--accent);
  text-decoration: none;
}
.reco__amap:hover {
  text-decoration: underline;
}

/* 把手：整条 14px 都是热区，中间那条小药丸只是提示。touch-action 只给这一条，
   不与列表滚动和 sortablejs 的拖拽抢手势。 */
.reco__resize {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 14px;
  cursor: row-resize;
  touch-action: none;
}

.reco__resize::before {
  width: 40px;
  height: 4px;
  content: '';
  background: var(--border);
  border-radius: 999px;
  transition: background var(--dur-fast) var(--ease-out);
}

.reco__resize:hover::before,
.reco__resize:focus-visible::before {
  background: var(--accent);
}
</style>
