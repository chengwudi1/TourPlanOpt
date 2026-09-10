<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AmapKeyCheck from '@/components/AmapKeyCheck.vue'
import DaySection from '@/components/DaySection.vue'
import JoinGate from '@/components/JoinGate.vue'
import MapPanel from '@/components/MapPanel.vue'
import PlaceSearch from '@/components/PlaceSearch.vue'
import StashPanel from '@/components/StashPanel.vue'
import RecommendPanel from '@/components/RecommendPanel.vue'
import TripHeader from '@/components/TripHeader.vue'
import {
  ArrowRight,
  BedDouble,
  Check,
  ExternalLink,
  Flag,
  List,
  Lock,
  LockOpen,
  Map as MapIcon,
  Plus,
} from '@/components/icons'
import { getClientId, setClientName } from '@/composables/useClientIdentity'
import { useAuthStore } from '@/stores/auth'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { Place, Poi } from '@/types/domain'
import { apiFetch } from '@/utils/api'
import { formatMin } from '@/utils/time'

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

// -- 展开态（M15b）---------------------------------------------------------------------
// 选中与展开是两件事：点天头选中这一天（并展开它），点箭头只折叠/展开。折叠是本地
// 偏好，不进协议；记忆按行程分键，重新打开还是上次看到的样子。

const expandKey = `tourplanopt.day-expanded-${props.tripId}`
const expandedIds = ref<Set<string>>(new Set())
/** 用户亲手折叠过的天：排程提醒不再强行把它展开。 */
const touchedDays = new Set<string>()
let knownDays = new Set<string>()

function persistExpanded() {
  try {
    localStorage.setItem(expandKey, JSON.stringify([...expandedIds.value]))
  } catch {
    // 无痕模式下 localStorage 会抛；丢掉折叠偏好不影响功能。
  }
}

function setExpanded(dayId: string, on: boolean) {
  if (expandedIds.value.has(dayId) === on) return
  const next = new Set(expandedIds.value)
  if (on) next.add(dayId)
  else next.delete(dayId)
  expandedIds.value = next
  persistExpanded()
}

function toggleDay(dayId: string) {
  touchedDays.add(dayId)
  setExpanded(dayId, !expandedIds.value.has(dayId))
}

function selectDay(dayId: string) {
  store.currentDayId = dayId
  setExpanded(dayId, true)
}

function initExpand() {
  const ids = store.days.map((d) => d.id)
  knownDays = new Set(ids)
  let saved: unknown = null
  try {
    saved = JSON.parse(localStorage.getItem(expandKey) ?? 'null')
  } catch {
    saved = null
  }
  const next = new Set(
    Array.isArray(saved) ? saved.filter((id): id is string => ids.includes(id)) : [],
  )
  if (!Array.isArray(saved) && store.currentDayId) next.add(store.currentDayId)
  expandedIds.value = next
}

function removeDay(dayId: string) {
  if (window.confirm('删除这个（空的）天？')) store.deleteDay(dayId)
}

function renameDay(dayId: string) {
  const day = store.days.find((d) => d.id === dayId)
  if (!day) return
  const name = window.prompt('这一天的名称（留空恢复默认）：', day.title)
  if (name === null) return
  store.updateDay(dayId, { title: name.trim() })
}

function renameTrip(current: string) {
  const name = window.prompt('行程名称：', current)
  if (name === null) return
  store.updateTripFields({ title: name.trim() })
}

/** 文字版行程：贴群聊用。 */
const copied = ref(false)

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
      const time = place.start_min !== null ? `${formatMin(place.start_min)} ` : ''
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
  initExpand()
  if (joined.value) socket.connect(props.tripId)
})

onBeforeUnmount(() => {
  socket.disconnect()
})

// Switching days is a presence change too, not just selection changes.
watch(
  () => store.currentDayId,
  (dayId) => {
    socket.sendPresence(dayId, store.selectedPlaceId)
    if (dayId) setExpanded(dayId, true)
  },
)

/** 提醒藏在折叠的天里等于没有提醒：有 warnings 且用户没亲手折叠过的天自动展开。 */
watch(
  () => store.timelines,
  () => {
    for (const [dayId, t] of Object.entries(store.timelines)) {
      if (t.warnings.length && !touchedDays.has(dayId)) setExpanded(dayId, true)
    }
  },
)

/** 天增减：清理已消失的展开项，新出现的天默认展开（否则像行程凭空少了几天）。 */
watch(
  () => store.days.map((d) => d.id).join(','),
  () => {
    const alive = new Set(store.days.map((d) => d.id))
    const next = new Set([...expandedIds.value].filter((id) => alive.has(id)))
    for (const id of alive) if (!knownDays.has(id)) next.add(id)
    knownDays = alive
    expandedIds.value = next
    persistExpanded()
  },
)

async function onPoiPicked(poi: Poi) {
  store.opError = null
  store.addPlace({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function onStash(poi: Poi) {
  store.opError = null
  store.stashAdd({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

/** 地图选点：右键/长按地图某处 → regeo 预填名称 → 排进选中的天或存入想去清单。 */
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

/** 菜单里地点所在的天：卡片可以在非选中的天上，起点/终点只能落在它自己那一天。 */
const menuDay = computed(() => {
  const menu = cardMenu.value
  if (!menu) return null
  return store.days.find((d) => d.id === menu.place.day_id) ?? null
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
            <div class="skeleton" style="height: 46px" />
            <div class="skeleton" style="height: 74px" />
            <div class="skeleton" style="height: 74px; width: 85%" />
            <div class="skeleton" style="height: 74px; width: 70%" />
          </div>

          <template v-else-if="store.trip">
            <div v-if="store.opError" class="banner banner--warn">
              <div class="banner__body">
                <div class="banner__title">{{ store.opError.message }}</div>
                <div v-if="store.opError.hint" class="banner__hint tiny">{{ store.opError.hint }}</div>
              </div>
            </div>

            <PlaceSearch
              :city="store.trip.city"
              @select="onPoiPicked"
              @stash="onStash"
            />

            <RecommendPanel :city="store.trip.city" />

            <StashPanel />

            <div v-if="!store.places.length" class="empty">
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

            <div v-else class="daylist">
              <DaySection
                v-for="day in store.days"
                :key="day.id"
                :day="day"
                :expanded="expandedIds.has(day.id)"
                @select="selectDay(day.id)"
                @toggle="toggleDay(day.id)"
                @rename="renameDay(day.id)"
                @remove="removeDay(day.id)"
                @menu="(p, pos) => (cardMenu = { place: p, ...pos })"
              />
              <div class="daylist__foot">
                <button class="btn btn--sm btn--ghost" type="button" @click="store.addDay()">
                  <Plus class="ic" :size="13" /> 加一天
                </button>
                <button class="btn btn--sm btn--ghost" type="button" @click="copyTextItinerary">
                  <Check v-if="copied" class="ic" :size="12" />
                  {{ copied ? '已复制' : '复制文字版' }}
                </button>
              </div>
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
        <List class="ic" :size="15" /> 行程
      </button>
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': mobileView === 'map' }"
        type="button"
        @click="mobileView = 'map'"
      >
        <MapIcon class="ic" :size="15" /> 地图
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
        <ExternalLink class="ic" :size="13" /> 在高德中查看
      </a>
      <button
        class="cardmenu__item"
        type="button"
        @click="store.setPlaceLocked(cardMenu.place.id, !cardMenu.place.locked); closeCardMenu()"
      >
        <LockOpen v-if="cardMenu.place.locked" class="ic" :size="13" />
        <Lock v-else class="ic" :size="13" />
        {{ cardMenu.place.locked ? '取消锁定' : '锁定位置' }}
      </button>
      <button
        v-if="menuDay && menuDay.id && menuDay.start_place_id !== cardMenu.place.id"
        class="cardmenu__item"
        type="button"
        @click="store.setStartPlace(menuDay.id, cardMenu.place.id); closeCardMenu()"
      >
        <Flag class="ic" :size="13" /> 设为起点
      </button>
      <button
        v-if="menuDay && menuDay.id && menuDay.end_place_id !== cardMenu.place.id"
        class="cardmenu__item"
        type="button"
        @click="store.setEndPlace(menuDay.id, cardMenu.place.id); closeCardMenu()"
      >
        <BedDouble class="ic" :size="13" /> 设为终点
      </button>
      <button
        v-for="d in cardMenuOtherDays"
        :key="d.id"
        class="cardmenu__item"
        type="button"
        @click="store.movePlaceToDay(cardMenu.place.id, d.id); closeCardMenu()"
      >
        <ArrowRight class="ic" :size="13" /> 移到 D{{ d.day_index + 1 }}{{ d.title ? ` · ${d.title}` : '' }}
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
          <ArrowRight class="ic" :size="13" /> 排进选中的天
        </button>
        <button
          class="btn btn--sm btn--ghost"
          type="button"
          :disabled="!mapPick.name.trim()"
          @click="stashMapPick"
        >
          存入想去
        </button>
        <button class="btn btn--sm btn--ghost" type="button" @click="mapPick = null">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cardmenu {
  position: fixed;
  z-index: var(--z-bar);
  display: flex;
  flex-direction: column;
  min-width: 170px;
  padding: 4px;
  box-shadow: var(--shadow-lg);
  animation: cardmenu-in var(--dur-slow) var(--ease-pop);
}

.cardmenu__item {
  display: flex;
  gap: 6px;
  align-items: center;
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
  z-index: var(--z-overlay);
  left: 50%;
  bottom: 88px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(320px, calc(100vw - 32px));
  padding: 14px 16px;
  transform: translateX(-50%);
  box-shadow: var(--shadow-pop);
  animation: mappick-in var(--dur-slow) var(--ease-pop);
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

@keyframes cardmenu-in {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
}

/* 只有 from 帧：动画结束后回到元素自身的样式，所以 from 里必须重复 translateX(-50%)，
   否则弹层会在弹出的瞬间横跳一次。 */
@keyframes mappick-in {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(10px) scale(0.97);
  }
}

.panel__content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.daylist {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.daylist__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 2px 2px 6px;
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
