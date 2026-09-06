<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AmapKeyCheck from '@/components/AmapKeyCheck.vue'
import JoinGate from '@/components/JoinGate.vue'
import MapPanel from '@/components/MapPanel.vue'
import OptimizeBar from '@/components/OptimizeBar.vue'
import PlaceCard from '@/components/PlaceCard.vue'
import PlaceSearch from '@/components/PlaceSearch.vue'
import RouteSummary from '@/components/RouteSummary.vue'
import TripHeader from '@/components/TripHeader.vue'
import { getClientId } from '@/composables/useClientIdentity'
import { useDragSort } from '@/composables/useDragSort'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { Place, Poi } from '@/types/domain'
import { formatDuration, formatMin } from '@/utils/time'

const props = defineProps<{ tripId: string }>()

const store = useTripStore()
const socket = useSocketStore()

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
useDragSort(placeListEl, (orderedIds) => {
  if (store.currentDayId) store.reorderDay(store.currentDayId, orderedIds)
})

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
  try {
    await store.load(props.tripId)
  } catch {
    // store.loadError already holds the message/hint pair for the banner below.
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
          <div v-if="store.loading" class="muted tiny">正在加载行程…</div>

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
              </button>
              <button class="daytab daytab--add" type="button" title="加一天" @click="store.addDay()">
                ＋
              </button>
            </nav>

            <p v-if="dayOverview" class="daystrip tiny muted">
              {{ dayOverview.count }} 个地点
              <template v-if="dayOverview.start">
                · {{ dayOverview.start }} 出发 · 预计 {{ dayOverview.end }} 结束
              </template>
            </p>

            <PlaceSearch :city="store.trip.city" @select="onPoiPicked" />

            <OptimizeBar />

            <RouteSummary />

            <div v-if="store.opError" class="banner banner--warn">
              <div class="banner__body">
                <div class="banner__title">{{ store.opError.message }}</div>
                <div v-if="store.opError.hint" class="banner__hint tiny">{{ store.opError.hint }}</div>
              </div>
            </div>

            <ul v-if="store.currentPlaces.length" ref="placeListEl" class="placelist">
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
                />
              </template>
            </ul>

            <p v-else class="muted tiny empty-hint">
              还没有地点。在上面搜索一个（比如「外滩」），点一下就加进今天。
            </p>
          </template>
        </div>
      </div>
      <div class="map-host">
        <MapPanel />
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
  </div>
</template>

<style scoped>
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
  padding: 8px 12px;
  margin: -4px 0 0;
  background: var(--accent-soft);
  border-radius: var(--radius-sm);
}

.placelist {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.empty-hint {
  padding: 18px;
  text-align: center;
  background: var(--surface-2);
  border-radius: var(--radius);
}
</style>
