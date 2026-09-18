<script setup lang="ts">
import { h, onBeforeUnmount, onMounted, ref, render, shallowRef, watch } from 'vue'

import { ensureAmap, useAmap } from '@/composables/useAmap'
import type { AMapNS } from '@/composables/useAmap'
import { BedDouble, Flag, LocateFixed } from '@/components/icons'
import { useTripStore } from '@/stores/trip'
import type { Place } from '@/types/domain'
import { wgs84ToGcj02 } from '@/utils/coords'

// Default view: Shanghai. Replaced by the trip's own bounds once places exist.
const DEFAULT_CENTER: [number, number] = [121.4737, 31.2304]
const DEFAULT_ZOOM = 12

/** 这一天里这个地点的身份：出发锚、收锚，或者只是普通一站。 */
type AnchorKind = '' | 'start' | 'end'

interface MarkerEntry {
  marker: AMapNS
  el: HTMLElement
  img: HTMLImageElement
  num: HTMLElement
  pill: HTMLElement
  /** 已挂进 pill 的锚点图标。图标只在锚点种类变化时重挂，不做无谓的 mount 抖动。 */
  pillKind: AnchorKind
  /** 照片热链加载失败过 → 这个标记永久回落成实心圆。 */
  photoBroken: boolean
}

/** 双描边路线 + 选中站那一段的高亮层。 */
interface RouteOverlays {
  casing: AMapNS
  core: AMapNS
  hl: AMapNS
}

const host = ref<HTMLDivElement | null>(null)
const map = shallowRef<AMapNS>(null)
/** The AMap namespace itself -- Marker/Polyline are constructors on the NAMESPACE,
 * not on the Map instance. `map.value.Marker` is the classic way this panel breaks. */
const amapNs = shallowRef<AMapNS>(null)
const initError = ref('')
const locating = ref(false)
const locateNote = ref('')

/** 长按选点：把地图上按住的坐标抛给上层（TripView 弹「加地点」确认框）。 */
const emit = defineEmits<{ pick: [point: { lng: number; lat: number }] }>()

const { status } = useAmap()
const store = useTripStore()

/** place id -> marker. Rebuilt against `currentPlaces` on every sync. */
const markers = new Map<string, MarkerEntry>()
const route = shallowRef<RouteOverlays | null>(null)
/** Sorted id list the fit-view was last computed for. */
let fittedSignature = ''

/** geolocation returns WGS-84 -- the project's ONLY coordinate conversion boundary. */
function locateMe() {
  if (locating.value || !map.value || !navigator.geolocation) return
  locating.value = true
  locateNote.value = ''
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      locating.value = false
      const [lng, lat] = wgs84ToGcj02(pos.coords.longitude, pos.coords.latitude)
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
        locateNote.value = '定位结果无效'
        setTimeout(() => (locateNote.value = ''), 4000)
        return
      }
      map.value?.setZoomAndCenter(14, [lng, lat], false, 200)
    },
    (err) => {
      locating.value = false
      locateNote.value =
        err.code === err.PERMISSION_DENIED
          ? '定位被拒绝：请在浏览器地址栏允许定位权限'
          : '定位失败，请稍后再试'
      setTimeout(() => (locateNote.value = ''), 4000)
    },
    { enableHighAccuracy: true, timeout: 8000 },
  )
}

onMounted(async () => {
  let AMap: AMapNS
  try {
    AMap = await ensureAmap()
  } catch {
    // ensureAmap already recorded a diagnostic; AmapKeyCheck.vue explains it.
    initError.value = '高德JS API 未就绪'
    return
  }
  if (!host.value) return

  amapNs.value = AMap
  map.value = new AMap.Map(host.value, {
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    resizeEnable: true,
    viewMode: '2D',
    mapStyle: basemapStyle(),
  })
  map.value.addControl(new AMap.Scale())
  bindPickHandlers()
  syncMarkers()
  // 芯色读的是 CSS 变量，而变量由 @media (prefers-color-scheme) 换值 -- 主题一翻就得
  // 重画一次，否则路线会停在旧色上直到下一次编辑。
  darkScheme.addEventListener('change', onSchemeChange)
})

// -- 地图选点（右键 = 桌面，长按 = 触屏）------------------------------------------------

/** detach fn returned by bindPickHandlers; called on unmount. */
let detachPickHandlers: (() => void) | null = null

function bindPickHandlers() {
  const m = map.value
  const hostEl = host.value
  if (!m || !hostEl) return

  m.on('rightclick', (e: { lnglat?: { getLng: () => number; getLat: () => number } }) => {
    const ll = e?.lnglat
    if (ll) emit('pick', { lng: ll.getLng(), lat: ll.getLat() })
  })

  // 触屏长按 550ms 且几乎没挪动 = 选点；挪动是拖地图，照常取消。
  let timer: ReturnType<typeof setTimeout> | null = null
  let start: { x: number; y: number } | null = null
  const clear = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
    start = null
  }
  const onTouchStart = (ev: TouchEvent) => {
    if (ev.touches.length !== 1) return clear()
    const t = ev.touches[0]
    start = { x: t.clientX, y: t.clientY }
    timer = setTimeout(() => {
      timer = null
      if (!start || !map.value || !amapNs.value) return
      const rect = hostEl.getBoundingClientRect()
      const lnglat = map.value.containerToLngLat?.(
        new amapNs.value.Pixel(start.x - rect.left, start.y - rect.top),
      )
      if (lnglat?.getLng) emit('pick', { lng: lnglat.getLng(), lat: lnglat.getLat() })
    }, 550)
  }
  const onTouchMove = (ev: TouchEvent) => {
    if (!start) return
    const t = ev.touches[0]
    if (Math.hypot(t.clientX - start.x, t.clientY - start.y) > 12) clear()
  }
  hostEl.addEventListener('touchstart', onTouchStart, { passive: true })
  hostEl.addEventListener('touchmove', onTouchMove, { passive: true })
  hostEl.addEventListener('touchend', clear)
  hostEl.addEventListener('touchcancel', clear)
  detachPickHandlers = () => {
    hostEl.removeEventListener('touchstart', onTouchStart)
    hostEl.removeEventListener('touchmove', onTouchMove)
    hostEl.removeEventListener('touchend', clear)
    hostEl.removeEventListener('touchcancel', clear)
  }
}

const darkScheme = window.matchMedia('(prefers-color-scheme: dark)')
const onSchemeChange = () => {
  // 底图必须跟着翻：暗色界面配一张亮瓦片，等于在屏幕右侧糊了一块白光。
  map.value?.setMapStyle?.(basemapStyle())
  syncPolyline()
}

onBeforeUnmount(() => {
  detachPickHandlers?.()
  detachPickHandlers = null
  darkScheme.removeEventListener('change', onSchemeChange)
  // 标记 DOM 由地图销毁，但挂进 pill 的 lucide 子树得显式卸载，不然它的 effect 作用域
  // 就永久留在一个已经不在文档里的容器上。
  for (const entry of markers.values()) render(null, entry.pill)
  markers.clear()
  route.value = null
  map.value?.destroy?.()
  map.value = null
})

/** 描边跟底图走、不跟界面令牌走：亮底图用墨色压边把亮芯抬起来，暗底图反过来要一条浅色边，
 * 否则同一条深色描边压在夜航图上就是「没有描边」。芯色始终读 --accent。 */
const CASING_LIGHT = '#2e3132'
const CASING_DARK = '#dbe6ef'
const FALLBACK_ACCENT = '#1670c2'

function basemapStyle(): string {
  return darkScheme.matches ? 'amap://styles/dark' : 'amap://styles/normal'
}

function cssColor(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

/**
 * 标记＝列表行在地图上的分身：34px 圆形照片 + 白描边 + 右下角序号角标，角标底色是创建者
 * 色，与 PlaceCard 的 .place__order 同一套语义，看图和看列表因此对得上同一站。
 */
function createMarker(AMap: AMapNS, m: AMapNS, place: Place, index: number): MarkerEntry {
  const el = document.createElement('div')
  el.className = 'tp-marker'
  const img = document.createElement('img')
  img.className = 'tp-marker__img'
  img.alt = ''
  img.referrerPolicy = 'no-referrer'
  const num = document.createElement('span')
  num.className = 'tp-marker__num'
  const pill = document.createElement('span')
  pill.className = 'tp-marker__pill'
  el.append(img, num, pill)

  // 圆标没有尖，指的就是坐标本身 → anchor 从 teardrop 的 bottom-center 回到 center。
  const marker = new AMap.Marker({
    position: [place.lng, place.lat],
    content: el,
    anchor: 'center',
    zIndex: 100 + index,
  })
  const entry: MarkerEntry = { marker, el, img, num, pill, pillKind: '', photoBroken: false }
  el.addEventListener('click', () => store.selectPlace(place.id))
  // 热链挂过一次就把这个标记永久降级成实心圆：反复重试只会一直亮破图。
  img.addEventListener('error', () => {
    entry.photoBroken = true
    el.classList.remove('tp-marker--photo')
  })
  m.add(marker)
  return entry
}

/** 这个地点在当前天里的锚点身份。起点/终点都是列表里的某一站，只是多带一个 pill。 */
function anchorKindOf(placeId: string): AnchorKind {
  const day = store.currentDay
  if (!day) return ''
  if (day.start_place_id === placeId) return 'start'
  return day.end_place_id === placeId ? 'end' : ''
}

/** 把 store 里这一行投影到已存在的标记节点上：顺序、创建者色、照片、锚点、选中态。 */
function paintMarker(entry: MarkerEntry, place: Place, index: number) {
  const { el } = entry
  el.style.setProperty('--tp-fill', store.creatorColorOf(place.added_by) || FALLBACK_ACCENT)
  el.title = place.name

  const photo = entry.photoBroken ? '' : place.photo_url
  if (photo && entry.img.dataset.src !== photo) {
    entry.img.dataset.src = photo
    entry.img.src = photo
  }
  el.classList.toggle('tp-marker--photo', !!photo)

  const selected = place.id === store.selectedPlaceId
  el.classList.toggle('tp-marker--sel', selected)
  entry.marker.setzIndex(selected ? 300 : 100 + index)

  entry.num.textContent = String(index + 1)
  entry.marker.setPosition([place.lng, place.lat])

  const kind = anchorKindOf(place.id)
  if (kind !== entry.pillKind) {
    entry.pillKind = kind
    el.classList.toggle('tp-marker--anchored', kind !== '')
    const icon = kind === 'start' ? Flag : kind === 'end' ? BedDouble : null
    // 图标要出现在命令式建的 DOM 里，只能走 Vue 的低层 render；单一出口规则照旧不破。
    render(icon ? h(icon, { size: 11, strokeWidth: 2.6 }) : null, entry.pill)
  }
}

/** Make markers / current-day / selection agree with the store. Idempotent. */
function syncMarkers() {
  const m = map.value
  if (!m) return

  const wanted = store.currentPlaces
  const wantedIds = new Set(wanted.map((p) => p.id))

  for (const [id, entry] of markers) {
    if (!wantedIds.has(id)) {
      m.remove(entry.marker)
      render(null, entry.pill)
      markers.delete(id)
    }
  }

  wanted.forEach((place, index) => {
    const AMap = amapNs.value
    if (!AMap) return
    let entry = markers.get(place.id)
    if (!entry) {
      entry = createMarker(AMap, m, place, index)
      markers.set(place.id, entry)
    }
    paintMarker(entry, place, index)
  })

  syncPolyline()

  const signature = wanted.map((p) => p.id).join(',')
  if (wanted.length > 0 && signature !== fittedSignature) {
    // Refit only when the *set* changes -- not while panning between selections.
    fittedSignature = signature
    m.setFitView(m.getAllOverlays('marker'), false, [70, 70, 70, 70])
  }
}

/** 路线一拆就整组丢掉（站点不足两站时）。 */
function dropRoute() {
  const r = route.value
  if (r && map.value) map.value.remove([r.casing, r.core, r.hl])
  route.value = null
}

/**
 * 双描边：深色 casing 把亮芯从瓦片上「抬」起来，上面再走一条 accent 亮芯 —— 单条亮色
 * 线压在高德底图上是没有分量的。第三条 hl 只画选中站那一段。
 */
function syncPolyline() {
  const m = map.value
  const AMap = amapNs.value
  if (!m || !AMap) return

  const path = store.currentPlaces.map((p) => [p.lng, p.lat])
  if (path.length < 2) {
    dropRoute()
    return
  }

  const accent = cssColor('--accent', FALLBACK_ACCENT)
  const casingColor = darkScheme.matches ? CASING_DARK : CASING_LIGHT
  if (!route.value) {
    const base = { path, lineJoin: 'round', lineCap: 'round', bubble: true }
    route.value = {
      casing: new AMap.Polyline({
        ...base,
        strokeColor: casingColor,
        strokeWeight: 8,
        strokeOpacity: 0.25,
        zIndex: 40,
      }),
      core: new AMap.Polyline({
        ...base,
        strokeColor: accent,
        strokeWeight: 4,
        strokeOpacity: 0.95,
        showDir: true,
        zIndex: 50,
      }),
      hl: new AMap.Polyline({
        ...base,
        strokeColor: accent,
        strokeWeight: 7,
        strokeOpacity: 0.95,
        zIndex: 60,
      }),
    }
    m.add([route.value.casing, route.value.core, route.value.hl])
  } else {
    const { casing, core, hl } = route.value
    casing.setPath(path)
    core.setPath(path)
    // 芯色与描边色都要跟着主题重读一次，否则翻主题后路线只剩一半换了颜色。
    casing.setOptions({ strokeColor: casingColor })
    core.setOptions({ strokeColor: accent })
    hl.setOptions({ strokeColor: accent })
  }
  syncLegHighlight()
}

/**
 * 选中某一站 → 点亮「到它」那一段。第一站没有来路，就点亮它的出发段，这样选中任意一站
 * 地图上都有反馈，不会出现点了却没反应的第一站。
 */
function syncLegHighlight() {
  const r = route.value
  if (!r) return
  const places = store.currentPlaces
  const i = places.findIndex((p) => p.id === store.selectedPlaceId)
  const from = i > 0 ? places[i - 1] : places[0]
  const to = i > 0 ? places[i] : places[1]
  const hasLeg = i >= 0 && !!to
  if (!hasLeg) {
    r.hl.hide()
    return
  }
  r.hl.setPath([[from.lng, from.lat], [to.lng, to.lat]])
  r.hl.show()
}

/** Clicking a CARD pans to its marker (marker->card is selectPlace on the marker's
 * own click handler). */
watch(
  () => store.selectedPlaceId,
  (id) => {
    if (!id || !map.value) return
    const entry = markers.get(id)
    if (entry) map.value.panTo(entry.marker.getPosition())
  },
)

watch(
  () => [store.currentPlaces, store.selectedPlaceId] as const,
  () => syncMarkers(),
)

defineExpose({
  getMap: () => map.value,
})
</script>

<template>
  <div class="map-panel">
    <div ref="host" class="map-panel__host" />

    <div v-if="status === 'ready'" class="map-panel__tools">
      <button
        class="map-panel__locate"
        type="button"
        :disabled="locating"
        :title="locating ? '定位中…' : '将地图移动至当前位置'"
        @click="locateMe"
      >
        <LocateFixed class="ic" :size="13" />
        {{ locating ? '定位中…' : '我的位置' }}
      </button>
      <span v-if="locateNote" class="map-panel__note tiny">{{ locateNote }}</span>
    </div>

    <div v-if="status !== 'ready'" class="map-panel__overlay">
      <div class="card map-panel__box">
        <strong>地图未加载</strong>
        <p class="muted tiny">
          {{ initError || '正在初始化高德地图…' }}<br />
          若长时间未加载，请查看上方的 Key 自检提示。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.map-panel {
  position: absolute;
  inset: 0;
}
.map-panel__host {
  width: 100%;
  height: 100%;
}
.map-panel__tools {
  position: absolute;
  z-index: 10;
  right: 12px;
  bottom: 24px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-end;
}

/* 桌面端右下角是精灵的（88px 宽、离边 24px），工具条让到它左边去。
   手机端不用让：那边精灵已经抬到 dock 之上（Sprite.vue 的 --dock-h 那条）。 */
@media (min-width: 861px) {
  .map-panel__tools {
    right: 124px;
  }
}
.map-panel__locate {
  padding: 7px 12px;
  font-size: 13px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--ink);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
}
.map-panel__locate:hover:not(:disabled) {
  background: var(--surface-2);
}
.map-panel__locate:disabled {
  color: var(--text-3);
}
.map-panel__note {
  padding: 6px 10px;
  color: var(--warn);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.map-panel__overlay {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: var(--surface-2);
}
.map-panel__box {
  max-width: 340px;
  padding: 18px 20px;
  text-align: center;
}
.map-panel__box p {
  margin: 8px 0 0;
}
</style>

<style>
/* Global on purpose: marker nodes live inside the AMap container, outside this
   component's scoped tree, so scoped attributes would never match them. */
.tp-marker {
  --tp-fill: var(--accent);
  position: relative;
  display: grid;
  place-items: center;
  box-sizing: border-box;
  width: 34px;
  height: 34px;
  background: var(--tp-fill);
  /* 白描边写死不读 --surface：亮底图和夜航底图上白圈都干净，界面底色只在其中一边成立。 */
  border: 2.5px solid #fff;
  border-radius: 50%;
  box-shadow: 0 0 0 1.5px var(--tp-fill), var(--shadow-sm);
  cursor: pointer;
  transition: transform var(--dur) var(--ease-pop), box-shadow var(--dur) var(--ease-out);
}

.tp-marker__img {
  display: none;
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}

.tp-marker__num {
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #fff;
}

/* 有照片：照片铺满圆，序号退到右下角的小角标——和列表行的 .place__order 同一个位置。 */
.tp-marker--photo {
  background: var(--surface-3);
}

.tp-marker--photo .tp-marker__img {
  display: block;
}

.tp-marker--photo .tp-marker__num {
  position: absolute;
  right: -5px;
  bottom: -3px;
  display: grid;
  place-items: center;
  width: 15px;
  height: 15px;
  font-size: 9.5px;
  color: var(--warp-ink);
  background: var(--tp-fill);
  border: 1.5px solid #fff;
  border-radius: 50%;
}

/* 起点/终点锚：右上角一枚白 chip 装 Flag / BedDouble，锚点也是普通一站，只是多这个角。 */
.tp-marker__pill {
  position: absolute;
  top: -8px;
  right: -7px;
  display: none;
  place-items: center;
  width: 17px;
  height: 17px;
  color: var(--tp-fill);
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 3px rgba(24, 34, 30, 0.3);
}

.tp-marker--anchored .tp-marker__pill {
  display: grid;
}

.tp-marker--sel {
  animation: tp-pop var(--dur-slow) var(--ease-out);
  transform: scale(1.25);
  box-shadow:
    0 0 0 1.5px var(--tp-fill),
    0 0 0 5px color-mix(in srgb, var(--accent) 30%, transparent),
    var(--shadow-md);
}

@keyframes tp-pop {
  0% {
    transform: scale(0.6);
  }
  70% {
    transform: scale(1.35);
  }
  100% {
    transform: scale(1.25);
  }
}

/* 夜航底图上的高德角标与审图号是深色字，压在深色图上等于没有——这两条是必须留着的
   归属信息，所以把它反成浅灰，而不是藏起来。 */
@media (prefers-color-scheme: dark) {
  .amap-logo img,
  .amap-copyright {
    filter: invert(0.9) hue-rotate(180deg);
    opacity: 0.82;
  }
}
</style>
