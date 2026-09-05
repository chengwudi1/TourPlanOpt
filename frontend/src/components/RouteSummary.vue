<script setup lang="ts">
import { computed } from 'vue'

import { useTripStore } from '@/stores/trip'
import { formatDuration } from '@/utils/time'

const store = useTripStore()

const saved = computed(() => store.optimizeResult?.summary.saved_min ?? 0)

/** Whole-day handoff to the Amap app. The URI API allows at most ONE via waypoint,
 * so the full stop-by-stop link only exists for exactly 3 stops; otherwise it is a
 * first-to-last navigation. */
const amapDayUrl = computed(() => {
  const stops = store.currentPlaces
  if (stops.length < 2) return null
  const mode = { driving: 'car', walking: 'walk', straight: 'car' }[store.trip?.travel_mode ?? 'driving']
  const common = `mode=${mode}&src=tourplanopt&coordinate=gaode&callnative=0`
  const point = (p: { lng: number; lat: number; name: string }) =>
    `${p.lng},${p.lat},${encodeURIComponent(p.name)}`
  const from = point(stops[0])
  const to = point(stops[stops.length - 1])
  const via = stops.length === 3 ? `&via=${point(stops[1])}` : ''
  return `https://uri.amap.com/navigation?from=${from}&to=${to}${via}&${common}`
})
</script>

<template>
  <div v-if="store.optimizeResult" class="card summary">
    <div class="summary__main">
      <strong>
        优化后全程交通 {{ formatDuration(store.optimizeResult.summary.after_min) }}
        <span v-if="saved > 0" class="summary__saved">· 节省 {{ formatDuration(saved) }}</span>
      </strong>
      <span v-if="!store.optimizeResult.exact" class="tiny muted">（启发式解）</span>
    </div>

    <ul v-if="store.optimizeResult.warnings.length" class="summary__warnings tiny">
      <li v-for="(w, i) in store.optimizeResult.warnings" :key="i">{{ w }}</li>
    </ul>

    <div class="summary__actions">
      <a
        v-if="amapDayUrl"
        class="btn btn--sm"
        :href="amapDayUrl"
        target="_blank"
        rel="noopener"
        title="把这条路线发到高德地图"
      >
        🧭 在高德中打开
      </a>
      <button class="btn btn--sm" type="button" @click="store.undoOptimize()">撤销优化</button>
      <button class="btn btn--sm btn--ghost" type="button" @click="store.dismissOptimizeResult()">
        知道了
      </button>
    </div>
  </div>
</template>

<style scoped>
.summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}

.summary__saved {
  color: var(--ok);
}

.summary__warnings {
  padding: 8px 10px;
  margin: 0;
  color: var(--warn);
  background: var(--warn-soft);
  border-radius: var(--radius-sm);
}

.summary__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.summary__actions a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}
</style>
