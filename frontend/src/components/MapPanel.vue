<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { ensureAmap, useAmap } from '@/composables/useAmap'
import type { AMapNS } from '@/composables/useAmap'
import { useTripStore } from '@/stores/trip'
import { wgs84ToGcj02 } from '@/utils/coords'

// Default view: Shanghai. Replaced by the trip's own bounds once places exist.
const DEFAULT_CENTER: [number, number] = [121.4737, 31.2304]
const DEFAULT_ZOOM = 12

interface MarkerEntry {
  marker: AMapNS
  /** The current content node; replaced together with setContent. */
  el: HTMLElement
}

const host = ref<HTMLDivElement | null>(null)
const map = shallowRef<AMapNS>(null)
/** The AMap namespace itself -- Marker/Polyline are constructors on the NAMESPACE,
 * not on the Map instance. `map.value.Marker` is the classic way this panel breaks. */
const amapNs = shallowRef<AMapNS>(null)
const initError = ref('')
const locating = ref(false)
const locateNote = ref('')

const { status } = useAmap()
const store = useTripStore()

/** place id -> marker. Rebuilt against `currentPlaces` on every sync. */
const markers = new Map<string, MarkerEntry>()
const polyline = shallowRef<AMapNS>(null)
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
  })
  map.value.addControl(new AMap.Scale())
  syncMarkers()
})

onBeforeUnmount(() => {
  map.value?.destroy?.()
  map.value = null
  markers.clear()
  polyline.value = null
})

function makeNode(index: number, name: string): HTMLElement {
  const node = document.createElement('div')
  node.className = 'tp-marker'
  node.title = name
  node.textContent = String(index + 1)
  return node
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
      markers.delete(id)
    }
  }

  wanted.forEach((place, index) => {
    const AMap = amapNs.value
    if (!AMap) return

    let entry = markers.get(place.id)
    if (!entry) {
      const el = makeNode(index, place.name)
      el.addEventListener('click', () => store.selectPlace(place.id))
      const marker = new AMap.Marker({
        position: [place.lng, place.lat],
        content: el,
        anchor: 'bottom-center',
        zIndex: 100 + index,
      })
      m.add(marker)
      entry = { marker, el }
      markers.set(place.id, entry)
    } else {
      // Position/order can change via later ops; rebuild content so the number follows.
      const el = makeNode(index, place.name)
      el.addEventListener('click', () => store.selectPlace(place.id))
      entry.marker.setPosition([place.lng, place.lat])
      entry.marker.setContent(el)
      entry.marker.setzIndex(100 + index)
      entry.el = el
    }
  })

  applySelection(store.selectedPlaceId)
  syncPolyline()

  const signature = wanted.map((p) => p.id).join(',')
  if (wanted.length > 0 && signature !== fittedSignature) {
    // Refit only when the *set* changes -- not while panning between selections.
    fittedSignature = signature
    m.setFitView(m.getAllOverlays('marker'), false, [70, 70, 70, 70])
  }
}

function applySelection(selectedId: string | null) {
  for (const [id, entry] of markers) {
    const selected = id === selectedId
    entry.el.classList.toggle('tp-marker--sel', selected)
    entry.marker.setzIndex(selected ? 300 : 100)
  }
}

/** The day's route line, drawn through the markers in authoritative order. */
function syncPolyline() {
  const m = map.value
  const AMap = amapNs.value
  if (!m || !AMap) return

  const path = store.currentPlaces.map((p) => [p.lng, p.lat])
  if (path.length < 2) {
    if (polyline.value) {
      m.remove(polyline.value)
      polyline.value = null
    }
    return
  }
  if (!polyline.value) {
    polyline.value = new AMap.Polyline({
      path,
      strokeColor: '#2f6feb',
      strokeWeight: 5,
      strokeOpacity: 0.85,
      showDir: true,
      lineJoin: 'round',
      bubble: true,
    })
    m.add(polyline.value)
  } else {
    polyline.value.setPath(path)
  }
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
        :title="locating ? '定位中…' : '把地图移到我所在的位置'"
        @click="locateMe"
      >
        {{ locating ? '定位中…' : '📍 我的位置' }}
      </button>
      <span v-if="locateNote" class="map-panel__note tiny">{{ locateNote }}</span>
    </div>

    <div v-if="status !== 'ready'" class="map-panel__overlay">
      <div class="card map-panel__box">
        <strong>地图未加载</strong>
        <p class="muted tiny">
          {{ initError || '正在初始化高德地图…' }}<br />
          如果长时间停在这里，请看上方的 Key 自检提示。
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
.map-panel__locate {
  padding: 7px 12px;
  font-size: 13px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border-strong);
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
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--accent);
  border: 2px solid #fff;
  border-radius: 50% 50% 50% 4px;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
}

.tp-marker--sel {
  background: var(--danger);
  box-shadow: 0 0 0 4px var(--danger-soft);
}
</style>
