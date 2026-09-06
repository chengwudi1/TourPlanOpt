<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AmapKeyCheck from '@/components/AmapKeyCheck.vue'
import JoinGate from '@/components/JoinGate.vue'
import MapPanel from '@/components/MapPanel.vue'
import OptimizeBar from '@/components/OptimizeBar.vue'
import PlaceCard from '@/components/PlaceCard.vue'
import PlaceSearch from '@/components/PlaceSearch.vue'
import StashPanel from '@/components/StashPanel.vue'
import RecommendPanel from '@/components/RecommendPanel.vue'
import RouteSummary from '@/components/RouteSummary.vue'
import TripHeader from '@/components/TripHeader.vue'
import { getClientId, setClientName } from '@/composables/useClientIdentity'
import { useDragSort } from '@/composables/useDragSort'
import { useAuthStore } from '@/stores/auth'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { Place, Poi } from '@/types/domain'
import { apiFetch } from '@/utils/api'
import { formatDuration, formatMin } from '@/utils/time'

const props = defineProps<{ tripId: string }>()

const store = useTripStore()
const socket = useSocketStore()
const auth = useAuthStore()

/** Per-tab: sessionStorage identity means a reload keeps your name, a new tab asks
 * again -- exactly the granularity the collaboration semantics need. */
const joined = ref(Boolean(sessionStorage.getItem('tourplanopt.joined')))

const selfId = getClientId()

/** Mobile: the two panes become full-screen contexts; this picks which one shows. */
const mobileView = ref<'list' | 'map'>('list')

/** place id -> who (other than me) is focusing it, for the editing ring. */
const editorsByPlace = computed(() => {
  const map = new Map<string, { name: string; color: string }>()
  for (const p of store.presence) {
    if (p.client_id === selfId || !p.focusing_place_id) continue
    map.set(p.focusing_place_id, { name: p.name, color: p.color })
  }
  return map
})

/** added_by (a display name) -> participant colour, for creator-tinted markers. */
const creatorColor = computed(() => {
  const map = new Map<string, string>()
  for (const p of store.participants) map.set(p.name, p.color)
  return map
})

/** One-line day overview: fight the "no clear overview" complaint the category gets. */
const dayOverview = computed(() => {
  const list = store.currentPlaces
  if (!list.length) return null
  const first = list[0]
  const last = list[list.length - 1]
  const scheduled = list.some((p) => p.start_min !== null)
  const end = (last.start_min ?? 0) + last.duration_min
  return {
    count: list.length,
    start: scheduled ? formatMin(first.start_min ?? 540) : null,
    end: scheduled ? formatMin(end) : null,
  }
})

const placeListEl = ref<HTMLElement | null>(null)
useDragSort(
  placeListEl,
  (orderedIds) => {
    if (store.currentDayId) store.reorderDay(store.currentDayId, orderedIds)
  },
  (dragging) => {
    if (store.currentDayId) socket.sendPresence(store.currentDayId, store.selectedPlaceId, dragging ? store.currentDayId : null)
  },
)

const AMAP_URI_MODE = { driving: 'car', walking: 'walk', straight: 'car' } as const

const modeIcon = computed(() => {
  switch (store.trip?.travel_mode) {
    case 'walking':
      return '🚶'
    case 'straight':
      return '📏'
    default:
      return '🚗'
  }
})

/** 高德 URI API navigation link: from the previous place to this one. The URI API
 * allows at most ONE via waypoint, so a whole-day handoff is only built for exactly
 * 3 stops (see RouteSummary); per-leg links are always valid. */
function navHref(place: Place, index: number): string {
  const list = store.currentPlaces
  const mode = AMAP_URI_MODE[store.trip?.travel_mode ?? 'driving']
  const common = `mode=${mode}&src=tourplanopt&coordinate=gaode&callnative=0`
  const to = `${place.lng},${place.lat},${encodeURIComponent(place.name)}`
  if (index === 0) {
    return `https://uri.amap.com/navigation?to=${to}&${common}`
  }
  const prev = list[index - 1]
  const from = `${prev.lng},${prev.lat},${encodeURIComponent(prev.name)}`
  return `https://uri.amap.com/navigation?from=${from}&to=${to}&${common}`
}

function onJoin() {
  joined.value = true
  sessionStorage.setItem('tourplanopt.joined', '1')
  socket.connect(props.tripId)
}

onMounted(async () => {
  await auth.load()
  try {
    await store.load(props.tripId)
  } catch {
    // store.loadError already holds the message/hint pair for the banner below.
  }
  // Logged-in users skip the name gate: their account IS the identity, and asking
  // them to re-type it in every new tab reads as broken. Guests still see the gate
  // because per-tab identity is what makes two-window collaboration testable.
  if (auth.user && !joined.value) {
    joined.value = true
    // The composable's identity feeds added_by / participant rows; align it with the
    // account name so creator colours and the roster show the person, not a hex id.
    setClientName(auth.user.name)
    sessionStorage.setItem('tourplanopt.joined', '1')
  }
  if (joined.value) socket.connect(props.tripId)
})

onBeforeUnmount(() => {
  socket.disconnect()
})

// Switching days is a presence change too, not just selection changes.
watch(
  () => store.currentDayId,
  (dayId) => socket.sendPresence(dayId, store.selectedPlaceId),
)

async function onPoiPicked(poi: Poi) {
  store.opError = null
  store.addPlace({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
  })
}

function onRemove(placeId: string) {
  store.opError = null
  store.deletePlace(placeId)
}

function onStash(poi: Poi) {
  store.opError = null
  store.stashAdd({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
  })
}

/** 地图选点：右键/长按地图某处 → regeo 预填名称 → 排进今天或存入想去清单。 */
const mapPick = ref<{
  lng: number
  lat: number
  name: string
  address: string
  loading: boolean
} | null>(null)

function onMapPick(point: { lng: number; lat: number }) {
  const round = (v: number) => Math.round(v * 1e6) / 1e6
  mapPick.value = {
    lng: round(point.lng),
    lat: round(point.lat),
    name: '',
    address: '',
    loading: true,
  }
  apiFetch<{ name: string; address: string }>(
    `/api/poi/regeo?lng=${mapPick.value.lng}&lat=${mapPick.value.lat}`,
  )
    .then((info) => {
      if (!mapPick.value) return
      mapPick.value.address = info.address || ''
      mapPick.value.name = info.name || info.address || '地图选点'
    })
    .catch(() => {
      // 没配 Web 服务 Key 时 regeo 不可用：仍可手动命名添加。
      if (mapPick.value) mapPick.value.name = '地图选点'
    })
    .finally(() => {
      if (mapPick.value) mapPick.value.loading = false
    })
}

function confirmMapPick() {
  const p = mapPick.value
  if (!p?.name.trim()) return
  store.opError = null
  store.addPlace({ name: p.name.trim(), lng: p.lng, lat: p.lat, address: p.address })
  mapPick.value = null
}

function stashMapPick() {
  const p = mapPick.value
  if (!p?.name.trim()) return
  store.opError = null
  store.stashAdd({ name: p.name.trim(), lng: p.lng, lat: p.lat, address: p.address })
  mapPick.value = null
}

/** 删除空的天：有内容的天服务端会拒绝，这里只对空天显示 ×。 */
function dayIsEmpty(dayId: string): boolean {
  return !store.places.some((p) => p.day_id === dayId)
}

function removeDay(dayId: string, event: MouseEvent) {
  event.stopPropagation()
  if (window.confirm('删除这个（空的）天？')) store.deleteDay(dayId)
}

/** 文字版行程：贴群聊用。 */
async function copyTextItinerary() {
  const lines: string[] = []
  const title = store.trip?.title || '未命名行程'
  lines.push(`📍 ${title}${store.trip?.city ? `（${store.trip.city}）` : ''}`)
  for (const day of store.days) {
    const list = store.places
      .filter((p) => p.day_id === day.id)
      .sort((a, b) => a.sort_index - b.sort_index)
    lines.push('')
    lines.push(`DAY ${day.day_index + 1}${day.title ? ` · ${day.title}` : ''}`)
    for (const place of list) {
      const time =
        place.start_min !== null
          ? `${String(Math.floor((place.start_min % 1440) / 60)).padStart(2, '0')}:${String(place.start_min % 60).padStart(2, '0')} `
          : ''
      lines.push(`${time}${place.name}${place.address ? `（${place.address}）` : ''}`)
    }
  }
  const text = lines.join('\n')
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    window.prompt('复制文字版行程：', text)
  }
}

function onPatch(place: Place, patch: { duration_min?: number; note?: string; start_min?: number | null }) {
  store.updatePlace(place.id, patch)
}

function renameDay(dayId: string, current: string) {
  const name = window.prompt('这一天的名称（留空恢复默认）：', current)
  if (name === null) return
  store.updateDay(dayId, { title: name.trim() })
}

function renameTrip(current: string) {
  const name = window.prompt('行程名称：', current)
  if (name === null) return
  store.updateTripFields({ title: name.trim() })
}

async function copyShareLink() {
  const url = `${window.location.origin}/trip/${props.tripId}`
  try {
    await navigator.clipboard.writeText(url)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    window.prompt('复制下面的链接分享给朋友：', url)
  }
}

const copied = ref(false)

/** 卡片操作菜单（右键/长按/⋯呼出）。 */
const cardMenu = ref<{ place: Place; x: number; y: number } | null>(null)

function closeCardMenu() {
  cardMenu.value = null
}

/** 菜单贴边夹紧：不让它被视口裁掉。 */
const cardMenuPos = computed(() => {
  if (!cardMenu.value) return {}
  return {
    left: `${Math.min(cardMenu.value.x, window.innerWidth - 190)}px`,
    top: `${Math.min(cardMenu.value.y, window.innerHeight - 150)}px`,
  }
})

/** 卡片菜单里的「移到其他天」候选（地点当前所在的天除外）。 */
const cardMenuOtherDays = computed(() => {
  const menu = cardMenu.value
  if (!menu) return []
  return store.days.filter((d) => d.id !== menu.place.day_id)
})

async function copyAddress(place: Place) {
  closeCardMenu()
  try {
    await navigator.clipboard.writeText(place.address || `${place.lng}, ${place.lat}`)
    store.opError = null
  } catch {
    window.prompt('复制地址：', place.address || `${place.lng}, ${place.lat}`)
  }
}

function closeCardMenuOnClick(e: MouseEvent) {
  if (!(e.target as HTMLElement).closest('.cardmenu')) closeCardMenu()
}

if (typeof window !== 'undefined') {
  window.addEventListener('click', closeCardMenuOnClick)
  window.addEventListener('resize', closeCardMenu)
}

// Dev-only handle for console assertions in the two-window verification drills
// (deep-compare both windows' day order after a concurrent-drag storm).
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__trip = store
  ;(window as unknown as Record<string, unknown>).__client_id = selfId
}
</script>

<template>
  <div class="shell">
    <TripHeader
      :title="store.trip?.title ?? ''"
      :city="store.trip?.city ?? ''"
      :presence="store.presence"
      :self-id="selfId"
      :status="socket.status"
      @share="copyShareLink"
      @rename="renameTrip(store.trip?.title ?? '')"
    />

    <AmapKeyCheck />

    <div v-if="store.loadError" class="shell__loadfail">
      <div class="banner banner--danger">
        <div class="banner__body">
          <div class="banner__title">{{ store.loadError.message }}</div>
          <div v-if="store.loadError.hint" class="banner__hint tiny">{{ store.loadError.hint }}</div>
          <div class="banner__hint tiny">
            检查链接是否完整，或回<a href="/">首页</a>重新打开。
          </div>
        </div>
      </div>
    </div>

    <div v-else class="shell__body" :class="{ 'map-open': mobileView === 'map' }">
      <div class="panel">
        <div class="panel__scroll panel__content">
          <div v-if="store.loading" class="skeletongroup" aria-label="正在加载行程">
            <div class="skeleton" style="height: 30px" />
            <div class="skeleton" style="height: 44px" />
            <div class="skeleton" style="height: 74px" />
            <div class="skeleton" style="height: 74px; width: 85%" />
            <div class="skeleton" style="height: 74px; width: 70%" />
          </div>

          <template v-else-if="store.trip">
            <nav class="daytabs">
              <button
                v-for="day in store.days"
                :key="day.id"
                class="daytab"
                :class="{ 'daytab--on': day.id === store.currentDayId }"
                type="button"
                :title="day.id === store.currentDayId ? '双击重命名这一天' : ''"
                @click="store.currentDayId = day.id"
                @dblclick="renameDay(day.id, day.title)"
              >
                D{{ day.day_index + 1 }}
                <span v-if="day.title" class="daytab__title">{{ day.title }}</span>
                <span
                  v-if="dayIsEmpty(day.id) && store.days.length > 1"
                  class="daytab__del"
                  title="删除这个空的天"
                  @click.stop="removeDay(day.id, $event)"
                  @dblclick.stop
                >
                  ×
                </span>
              </button>
              <button class="daytab daytab--add" type="button" title="加一天" @click="store.addDay()">
                ＋
              </button>
            </nav>

            <p v-if="dayOverview" class="daystrip tiny muted">
              <span>
                {{ dayOverview.count }} 个地点
                <template v-if="dayOverview.start">
                  · {{ dayOverview.start }} 出发 · 预计 {{ dayOverview.end }} 结束
                </template>
              </span>
              <button class="daystrip__copy" type="button" @click="copyTextItinerary">
                {{ copied ? '已复制 ✓' : '复制文字版' }}
              </button>
            </p>

            <PlaceSearch
              :city="store.trip.city"
              @select="onPoiPicked"
              @stash="onStash"
            />

            <RecommendPanel :city="store.trip.city" />

            <StashPanel />

            <OptimizeBar />

            <RouteSummary />

            <div v-if="store.opError" class="banner banner--warn">
              <div class="banner__body">
                <div class="banner__title">{{ store.opError.message }}</div>
                <div v-if="store.opError.hint" class="banner__hint tiny">{{ store.opError.hint }}</div>
              </div>
            </div>

            <div v-if="store.remoteDragger" class="draghint tiny" :style="{ borderColor: store.remoteDragger.color }">
              {{ store.remoteDragger.name }} 正在调整顺序…
            </div>

            <ul v-if="store.currentPlaces.length" ref="placeListEl" class="placelist" :class="{ 'placelist--locked': !!store.remoteDragger }">
              <template v-for="(place, index) in store.currentPlaces" :key="place.id">
                <li v-if="index > 0" class="leg">
                  <span class="leg__icon">{{ modeIcon }}</span>
                  <span v-if="place.travel_min_before !== null" class="tiny muted">
                    约 {{ formatDuration(place.travel_min_before) }}
                  </span>
                  <span v-else class="tiny muted">路程未知</span>
                </li>
                <PlaceCard
                  :place="place"
                  :index="index"
                  :active="place.id === store.selectedPlaceId"
                  :editing-by="editorsByPlace.get(place.id) ?? null"
                  :nav-href="navHref(place, index)"
                  :creator-color="creatorColor.get(place.added_by) ?? ''"
                  @select="store.selectPlace(place.id)"
                  @remove="onRemove(place.id)"
                  @lock="(p, locked) => store.setPlaceLocked(p.id, locked)"
                  @patch="onPatch"
                  @menu="(p, pos) => (cardMenu = { place: p, ...pos })"
                />
              </template>
            </ul>

            <div v-else class="empty">
              <svg class="empty__art" viewBox="0 0 200 96" aria-hidden="true">
                <path
                  d="M18 74 C 52 74, 58 30, 96 30 S 148 66, 182 66"
                  fill="none"
                  stroke="var(--accent)"
                  stroke-width="2.5"
                  stroke-dasharray="5 6"
                  stroke-linecap="round"
                />
                <g fill="var(--accent)">
                  <circle cx="18" cy="74" r="6" />
                  <circle cx="96" cy="30" r="7" />
                  <circle cx="182" cy="66" r="6" />
                </g>
                <g fill="var(--surface)">
                  <circle cx="96" cy="30" r="2.5" />
                </g>
              </svg>
              <p class="muted tiny empty-hint">
                还没有地点。在上面搜索一个（比如「外滩」），或打开「发现」挑一个推荐。
              </p>
            </div>
          </template>
        </div>
      </div>
      <div class="map-host">
        <MapPanel @pick="onMapPick" />
      </div>
    </div>

    <nav class="mobile-switch" aria-label="切换视图">
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': mobileView === 'list' }"
        type="button"
        @click="mobileView = 'list'"
      >
        🧾 行程
      </button>
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': mobileView === 'map' }"
        type="button"
        @click="mobileView = 'map'"
      >
        🗺️ 地图
      </button>
    </nav>

    <JoinGate v-if="!joined" @join="onJoin" />

    <div
      v-if="cardMenu"
      class="cardmenu card"
      :style="cardMenuPos"
    >
      <button
        v-if="cardMenu.place.address"
        class="cardmenu__item"
        type="button"
        @click="copyAddress(cardMenu.place)"
      >
        复制地址
      </button>
      <a
        class="cardmenu__item"
        target="_blank"
        rel="noopener"
        :href="`https://uri.amap.com/marker?position=${cardMenu.place.lng},${cardMenu.place.lat}&name=${encodeURIComponent(cardMenu.place.name)}`"
        @click="closeCardMenu"
      >
        在高德中查看 ↗
      </a>
      <button
        class="cardmenu__item"
        type="button"
        @click="store.setPlaceLocked(cardMenu.place.id, !cardMenu.place.locked); closeCardMenu()"
      >
        {{ cardMenu.place.locked ? '📍 取消锁定' : '📍 锁定位置' }}
      </button>
      <button
        v-if="store.currentDayId && store.currentDay?.start_place_id !== cardMenu.place.id"
        class="cardmenu__item"
        type="button"
        @click="store.setStartPlace(store.currentDayId, cardMenu.place.id); closeCardMenu()"
      >
        🏁 设为起点
      </button>
      <button
        v-for="d in cardMenuOtherDays"
        :key="d.id"
        class="cardmenu__item"
        type="button"
        @click="store.movePlaceToDay(cardMenu.place.id, d.id); closeCardMenu()"
      >
        ➜ 移到 D{{ d.day_index + 1 }}{{ d.title ? ` · ${d.title}` : '' }}
      </button>
    </div>

    <div v-if="mapPick" class="mappick card" role="dialog" aria-label="把地图上选的位置加进行程">
      <strong>{{ mapPick.loading ? '正在识别这个位置…' : '把这个位置加进行程？' }}</strong>
      <input
        v-model="mapPick.name"
        class="mappick__name"
        placeholder="地点名称"
        maxlength="120"
        @keyup.enter="confirmMapPick"
      />
      <p v-if="mapPick.address" class="tiny muted mappick__addr">{{ mapPick.address }}</p>
      <div class="mappick__actions">
        <button
          class="btn btn--sm"
          type="button"
          :disabled="!mapPick.name.trim()"
          @click="confirmMapPick"
        >
          ➜ 排进今天
        </button>
        <button
          class="btn btn--sm btn--ghost"
          type="button"
          :disabled="!mapPick.name.trim()"
          @click="stashMapPick"
        >
          🧺 存入想去
        </button>
        <button class="btn btn--sm btn--ghost" type="button" @click="mapPick = null">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cardmenu {
  position: fixed;
  z-index: 60;
  display: flex;
  flex-direction: column;
  min-width: 170px;
  padding: 4px;
}

.cardmenu__item {
  padding: 8px 10px;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  text-decoration: none;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.cardmenu__item:hover {
  background: var(--surface-2);
}

.mappick {
  position: fixed;
  z-index: 70;
  left: 50%;
  bottom: 88px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(320px, calc(100vw - 32px));
  padding: 14px 16px;
  transform: translateX(-50%);
  box-shadow: var(--shadow);
}

.mappick__name {
  padding: 8px 10px;
  font-size: 14px;
  background: var(--surface-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
}

.mappick__addr {
  margin: 0;
}

.mappick__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.panel__content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.leg {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 2px 0 2px 14px;
  list-style: none;
}

.leg__icon {
  font-size: 12px;
}

.daytabs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.daytab {
  padding: 5px 12px;
  font-size: 13px;
  color: var(--text-2);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
}

.daytab--on {
  color: #fff;
  background: var(--accent);
  border-color: var(--accent);
}

.daytab__title {
  margin-left: 4px;
}

.daytab--add {
  padding: 5px 10px;
  color: var(--text-3);
  border-style: dashed;
}

.daystrip {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  margin: -4px 0 0;
  background: var(--accent-soft);
  border-radius: var(--radius-sm);
}

.daystrip__copy {
  padding: 2px 8px;
  font-size: 12px;
  color: var(--accent-strong);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
}

.daytab__del {
  margin-left: 5px;
  padding: 0 4px;
  font-size: 13px;
  color: var(--text-3);
  border-radius: 50%;
}

.daytab__del:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

.placelist {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.placelist--locked {
  opacity: 0.6;
  pointer-events: none;
}

.draghint {
  padding: 6px 10px;
  border: 1px dashed var(--border-strong);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  animation: draghint-in 0.2s ease;
}

@keyframes draghint-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
}

.skeletongroup {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  padding: 18px 12px 14px;
  background: var(--surface-2);
  border-radius: var(--radius);
}

.empty__art {
  width: 180px;
  height: auto;
  opacity: 0.85;
}

.empty-hint {
  margin: 0;
  text-align: center;
}
</style>
