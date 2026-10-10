<script setup lang="ts">
import { h, onBeforeUnmount, onMounted, ref, render, shallowRef, watch } from 'vue'

import { ensureAmap, useAmap } from '@/composables/useAmap'
import type { AMapNS } from '@/composables/useAmap'
import { BedDouble, Flag, LocateFixed } from '@/components/icons'
import { useReduceMotion } from '@/composables/useReduceMotion'
import { useSettingsStore } from '@/stores/settings'
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
  /** 同伴头像容器：别人停在这一站上时挂在左下角。 */
  who: HTMLElement
  /** 上一次画进 who 的内容签名。在场心跳每 250ms 一趟，没有这一位就会反复重建子树。 */
  whoSig: string
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
const settings = useSettingsStore()
const reduceMotion = useReduceMotion()
const store = useTripStore()

/** place id -> marker. Rebuilt against `currentPlaces` on every sync. */
const markers = new Map<string, MarkerEntry>()
const route = shallowRef<RouteOverlays | null>(null)
/** Sorted id list the fit-view was last computed for. */
let fittedSignature = ''
/** 拟合时容器还没有尺寸（窄屏那一格是 `display: none` 挂着的），欠着这一次。 */
let fitPending = false
let mapRo: ResizeObserver | null = null

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
    initError.value = '地图没能加载出来'
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

  // 容器从 0（display: none）变有尺寸时，把欠着的拟合补上。挑下一帧再动相机：让 SDK 自己
  // 先把画布换到新尺寸（resizeEnable 已开），否则拟合还是按旧投影算的。
  mapRo = new ResizeObserver(() => {
    const el = host.value
    if (!el || el.clientWidth === 0 || el.clientHeight === 0 || !fitPending) return
    fitPending = false
    requestAnimationFrame(() => {
      const m = map.value
      if (!m) return
      // 欠账期间标记可能已被删空（手机上加完又删光）：空数组不许喂给 setFitView。
      const overlays = m.getAllOverlays('marker')
      if (overlays.length > 0) m.setFitView(overlays, false, [70, 70, 70, 70])
    })
  })
  mapRo.observe(host.value)
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

// 主题一翻，地图上有三样东西要跟着动：底图样式、描边色（它跟底图，不跟界面）、芯色
// （它读 --accent，是界面令牌）。所以这里盯的是两个已解析值，而不是浏览器的偏好——
// 用户把底图钉成亮色之后，「界面深色」和「底图深色」就再也不是同一件事了。
watch(
  () => [settings.basemapDark, settings.theme] as const,
  () => {
    // 底图必须跟着翻：暗色界面配一张亮瓦片，等于在屏幕右侧糊了一块白光。
    map.value?.setMapStyle?.(basemapStyle())
    syncPolyline()
  },
)

onBeforeUnmount(() => {
  clearEcho()
  mapRo?.disconnect()
  mapRo = null
  detachPickHandlers?.()
  detachPickHandlers = null
  // 标记 DOM 由地图销毁，但挂进 pill 的 lucide 子树得显式卸载，不然它的 effect 作用域
  // 就永久留在一个已经不在文档里的容器上。
  for (const entry of markers.values()) render(null, entry.pill)
  markers.clear()
  route.value = null
  map.value?.destroy?.()
  map.value = null
})

/* -- 指针回声：定位键上那枚光斑跟手。坐标写在发光的那枚键上，和海报头、聊天区同一套约定
   ——JS 只写 --mx/--my，光斑是纯 CSS 的合成层。
   刻意不碰 .map-panel__host：AMap 的瓦片容器一加 transform/filter，整幅底图会跟着指针抖，
   投影也被改写——那是要命的错，不是不够精致。 */
let echoTarget: HTMLElement | null = null

function clearEcho() {
  echoTarget?.style.removeProperty('--mx')
  echoTarget?.style.removeProperty('--my')
  echoTarget = null
}

function onEchoMove(e: PointerEvent) {
  if (reduceMotion.value) return
  const el = (e.target as HTMLElement | null)?.closest<HTMLElement>('.map-panel__locate')
  if (!el) {
    clearEcho()
    return
  }
  const r = el.getBoundingClientRect()
  if (!r.width || !r.height) return
  if (echoTarget !== el) clearEcho()
  echoTarget = el
  el.style.setProperty('--mx', `${((e.clientX - r.left) / r.width) * 100}%`)
  el.style.setProperty('--my', `${((e.clientY - r.top) / r.height) * 100}%`)
}

/** 偏好中途能改：关掉动态时要把已经写上去的光斑收掉，不然它留在原地像一盏没关的灯。 */
watch(reduceMotion, (off) => {
  if (off) clearEcho()
})

/** 描边跟底图走、不跟界面令牌走：亮底图用墨色压边把亮芯抬起来，暗底图反过来要一条浅色边，
 * 否则同一条深色描边压在夜航图上就是「没有描边」。芯色始终读 --accent。 */
const CASING_LIGHT = '#1b1d1f'
const CASING_DARK = '#dbe6ef'
const FALLBACK_ACCENT = '#2f6a99'
/** 无主标记（创建者不在名单：导入的模板行程、已退出的同伴）用身份盘里那颗中性色，
 *  不再借用 --accent：accent 与 1 号身份色只差 0.034，一颗「谁都不是」的圆标长得像某个
 *  具体的人，比没有颜色更误导。真值在 main.css 的 --warp-none，这里只是读不到令牌时的底。 */
const NO_OWNER = '#4f5b66'

/** 底图退回高德默认档。`whitesmoke` 那张灰白底压住了界面里那点暖中性纸，看上去像蒙了层
 *  雾，观感上不如默认瓦片；换底图这件事到此为止，界面不再跟着它调。 */
function basemapStyle(): string {
  return settings.basemapDark ? 'amap://styles/dark' : 'amap://styles/normal'
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
  const who = document.createElement('span')
  who.className = 'tp-marker__who'
  el.append(img, num, pill, who)

  // 圆标没有尖，指的就是坐标本身 → anchor 从 teardrop 的 bottom-center 回到 center。
  const marker = new AMap.Marker({
    position: [place.lng, place.lat],
    content: el,
    anchor: 'center',
    zIndex: 100 + index,
  })
  const entry: MarkerEntry = {
    marker,
    el,
    img,
    num,
    pill,
    who,
    whoSig: '',
    pillKind: '',
    photoBroken: false,
  }
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

/** 一枚标记上最多画两头像：34px 的圆里第三枚开始就只是噪点，多出来的人收成 +n。 */
const WHO_MAX_CHIPS = 2

/**
 * 同伴此刻停在这一站上 → 标记左下角挂他的头像。协同原先只在顶栏报一个人数，
 * 「谁在改哪一站」在地图上完全看不出来，而地图正是这一群人共同盯着的那一块。
 */
function paintWho(entry: MarkerEntry, placeId: string) {
  const viewers = store.presenceOnPlace.get(placeId) ?? []
  const shown = viewers.slice(0, WHO_MAX_CHIPS)
  const extra = viewers.length - shown.length
  const sig = `${shown.map((v) => `${v.client_id}:${v.name}:${v.color}`).join('|')}#${extra}`
  if (entry.whoSig === sig) return
  entry.whoSig = sig

  entry.who.textContent = ''
  for (const v of shown) {
    const chip = document.createElement('i')
    chip.className = 'tp-marker__whoitem'
    chip.style.setProperty('--tp-who', v.color || cssColor('--warp-none', NO_OWNER))
    chip.textContent = (v.name || '?').slice(0, 1).toUpperCase()
    chip.title = `${v.name || '同伴'} 在这一站`
    entry.who.append(chip)
  }
  if (extra > 0) {
    const more = document.createElement('i')
    more.className = 'tp-marker__whoitem tp-marker__whoitem--more'
    more.textContent = `+${extra}`
    more.title = `还有 ${extra} 人在这一站`
    entry.who.append(more)
  }
  entry.el.classList.toggle('tp-marker--watched', viewers.length > 0)
}

/** 创建者色单独一条：它读的是名单而不是这一行，所以名单一变就得重画（见下面的 watch）。 */
function paintOwner(entry: MarkerEntry, place: Place) {
  const ownerColor = store.creatorColorOf(place.added_by)
  entry.el.style.setProperty('--tp-fill', ownerColor || cssColor('--warp-none', NO_OWNER))
}

/** 把 store 里这一行投影到已存在的标记节点上：顺序、创建者色、照片、锚点、选中态。 */
function paintMarker(entry: MarkerEntry, place: Place, index: number) {
  const { el } = entry
  paintOwner(entry, place)
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
  paintWho(entry, place.id)
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
    const hostEl = host.value
    if (hostEl && hostEl.clientWidth > 0 && hostEl.clientHeight > 0) {
      m.setFitView(m.getAllOverlays('marker'), false, [70, 70, 70, 70])
    } else {
      // 窄屏上这一格是 `display: none` 挂着的：此刻拟合等于拿 0×0 容器算，视野会被夹在
      // 最小缩放上（切到「地图」看到的是全省）。欠着，等容器第一次量到尺寸再补（见 onMounted
      // 的 ResizeObserver）。只欠这一种「此前根本没有尺寸」的账——用户自己拖过的视野不在
      // 补的范围里。
      fitPending = true
    }
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
  const casingColor = settings.basemapDark ? CASING_DARK : CASING_LIGHT
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

// 在场变化只重画头像那一层。presence 每 250ms 就可能动一趟，跟着跑 syncMarkers 会把
// 照片热链与锚点子树反复重建——地图上一站站地闪，而地点数据其实一点没变。
watch(
  () => store.presenceOnPlace,
  () => {
    for (const [id, entry] of markers) paintWho(entry, id)
  },
)

// 名单一变（同伴改名、退出、重拉参与者）就重画创建者色。这一条以前没人管：
// currentPlaces 只依赖 day_id，改 added_by 或改名单都不会让它脏，于是「已退出的同伴」
// 那一站会一直穿着他生前的颜色——而 --warp-none 要接管的正是这一档。
watch(
  () => JSON.stringify(store.participants.map((p) => [p.name, p.color])),
  () => {
    for (const [id, entry] of markers) {
      const place = store.currentPlaces.find((p) => p.id === id)
      if (place) paintOwner(entry, place)
    }
  },
)

defineExpose({
  getMap: () => map.value,
})
</script>

<template>
  <div class="map-panel" :class="{ 'map-panel--dark-basemap': settings.basemapDark }">
    <div ref="host" class="map-panel__host" />

    <div
      v-if="status === 'ready'"
      class="map-panel__tools"
      @pointermove="onEchoMove"
      @pointerleave="clearEcho"
    >
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
          {{ initError || '正在加载地图…' }}<br />
          若长时间未加载，请查看上方的地图服务提示。
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
   手机端精灵停在 dock 之上（bottom = dock 总高 + 16，头像 56），
   工具条同样挤在右下象限——抬到精灵头顶上，S1 的同族问题。 */
@media (min-width: 861px) {
  .map-panel__tools {
    right: 124px;
  }
}
@media (max-width: 860px) {
  /* 右下象限是精灵的地盘（≤640 头像 56、641–860 仍是 88，右侧让不开），
     工具条整体挪到左下；bottom 抬高是避开 AMap 比例尺（它钉在左下 ~10px）。 */
  .map-panel__tools {
    right: auto;
    left: 12px;
    bottom: 64px;
    align-items: flex-start;
  }
  .map-panel__locate {
    min-height: 38px;
  }
}
.map-panel__locate {
  position: relative;
  padding: 7px 12px;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm), var(--edge);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  cursor: pointer;
  transition:
    transform var(--dur) var(--ease),
    background var(--dur) var(--ease);
}
.map-panel__locate:hover:not(:disabled) {
  background: var(--glass-dense);
  /* 浮层可以挪，瓦片容器不许挪：这 1px 只落在键上。 */
  transform: translateY(-1px);
}

/* 指针回声：光斑吃 JS 写在同一枚键上的 --mx/--my。 */
.map-panel__locate::after {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(
    64px 34px at var(--mx, 50%) var(--my, 50%),
    color-mix(in oklab, var(--text) 10%, transparent),
    transparent 70%
  );
  opacity: 0;
  pointer-events: none;
  content: '';
  transition: opacity var(--dur) var(--ease);
}
.map-panel__locate:hover:not(:disabled)::after {
  opacity: 1;
}
/* 减少动态那一档不画光斑：JS 不写坐标，它只会钉在键的正中，看着像渲染坏了。
   这一档的 hover 反馈退回底色和那道 1px 位移。 */
:root[data-motion='off'] .map-panel__locate::after {
  display: none;
}
.map-panel__locate:disabled {
  color: var(--text-3);
}
.map-panel__note {
  padding: 6px 10px;
  color: var(--warn);
  background: var(--glass-dense);
  border: 1px solid var(--glass-border);
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
  --tp-fill: var(--warp-none, #4f5b66);
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
  transition: transform var(--dur) var(--ease-pop), box-shadow var(--dur) var(--ease);
}

.tp-marker__img {
  display: none;
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}

.tp-marker__num {
  font-size: calc(12px * var(--fs-scale));
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
  font-size: calc(9.5px * var(--fs-scale));
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

/* 同伴头像：挂在左下角——右下角在有照片时是序号角标的地盘，两个都挤在那一侧就叠成
   一坨。默认不显示，只有这一站确实有人时父元素才带上 --watched。 */
.tp-marker__who {
  position: absolute;
  bottom: -5px;
  left: -5px;
  display: none;
  gap: 1px;
}

.tp-marker--watched .tp-marker__who {
  display: flex;
}

.tp-marker__whoitem {
  display: grid;
  place-items: center;
  width: 15px;
  height: 15px;
  font-size: calc(9px * var(--fs-scale));
  font-style: normal;
  font-weight: 700;
  color: #fff;
  background: var(--tp-who, var(--warp-none, #4f5b66));
  border: 1.5px solid #fff;
  border-radius: 50%;
  box-shadow: 0 1px 2px rgba(24, 34, 30, 0.35);
}

/* 「+n」不是一个人，是一串人，所以它不穿任何成员的颜色。 */
.tp-marker__whoitem--more {
  --tp-who: var(--text-2);
  font-size: calc(8px * var(--fs-scale));
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
   归属信息，所以把它反成浅灰，而不是藏起来。
   键在「实际画出来的那张底图」上（模板里的 .map-panel--dark-basemap），不是媒体查询：
   用户把底图钉成亮色之后，深色界面配的是亮瓦片，这时候再 invert 一次就把审图号反成了
   深色字压亮图——归属信息必须在每一种组合下都可读，所以它的开关不能自己猜主题。 */
.map-panel--dark-basemap .amap-logo img,
.map-panel--dark-basemap .amap-copyright {
  filter: invert(0.9) hue-rotate(180deg);
  opacity: 0.82;
}
</style>
