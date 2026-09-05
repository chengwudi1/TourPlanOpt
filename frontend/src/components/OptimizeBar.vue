<script setup lang="ts">
import { ref } from 'vue'

import { useTripStore } from '@/stores/trip'

const store = useTripStore()
const precise = ref(false)

function run() {
  void store.optimize(precise.value ? 'amap' : 'haversine')
}
</script>

<template>
  <div class="optbar">
    <button class="btn btn--primary btn--sm" type="button" :disabled="store.optimizing || store.currentPlaces.length < 3" @click="run">
      {{ store.optimizing ? '优化中…' : '⚡ 一键优化顺序' }}
    </button>
    <label class="optbar__precise tiny muted" title="用高德真实路况建距离矩阵（消耗配额）；默认用直线距离，瞬间完成且不耗配额">
      <input v-model="precise" type="checkbox" :disabled="store.optimizing" />
      精确优化（真实路况）
    </label>
    <span v-if="store.currentPlaces.length < 3" class="tiny muted">至少 3 个地点才可优化</span>
  </div>
</template>

<style scoped>
.optbar {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.optbar__precise {
  display: inline-flex;
  gap: 5px;
  align-items: center;
}
</style>
