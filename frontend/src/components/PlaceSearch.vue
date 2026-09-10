<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import { ShoppingBasket } from '@/components/icons'
import type { Poi } from '@/types/domain'
import { ApiError, apiFetch } from '@/utils/api'

const props = defineProps<{ city?: string }>()
const emit = defineEmits<{ select: [poi: Poi]; stash: [poi: Poi] }>()

const keyword = ref('')
const results = ref<Poi[]>([])
const open = ref(false)
const busy = ref(false)
const failure = ref<{ message: string; hint: string } | null>(null)
const activeIndex = ref(-1)

let timer: ReturnType<typeof setTimeout> | null = null

function onInput() {
  if (timer) clearTimeout(timer)
  const q = keyword.value.trim()
  if (!q) {
    results.value = []
    open.value = false
    failure.value = null
    return
  }
  // 300 ms: long enough to collapse a burst of keystrokes into one call, short enough
  // that it still feels like autocomplete. /inputtips is the cheap endpoint.
  timer = setTimeout(() => void search(q), 300)
}

async function search(q: string) {
  busy.value = true
  failure.value = null
  try {
    const params = new URLSearchParams({ keyword: q })
    if (props.city) params.set('city', props.city)
    const data = await apiFetch<{ tips: Poi[] }>(`/api/poi/tips?${params}`)
    // A late response for a stale keystroke must not overwrite the current one.
    if (q !== keyword.value.trim()) return
    results.value = data.tips
    activeIndex.value = data.tips.length ? 0 : -1
    open.value = data.tips.length > 0
  } catch (err) {
    if (err instanceof ApiError) failure.value = { message: err.message, hint: err.hint }
    else failure.value = { message: '搜索失败', hint: '' }
    results.value = []
    open.value = false
  } finally {
    busy.value = false
  }
}

function choose(poi: Poi) {
  emit('select', poi)
  keyword.value = ''
  results.value = []
  open.value = false
  failure.value = null
}

function onKeydown(event: KeyboardEvent) {
  if (!open.value) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = (activeIndex.value + 1) % results.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value =
      (activeIndex.value - 1 + results.value.length) % results.value.length
  } else if (event.key === 'Enter' && activeIndex.value >= 0) {
    event.preventDefault()
    choose(results.value[activeIndex.value])
  } else if (event.key === 'Escape') {
    open.value = false
  }
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="search">
    <input
      v-model="keyword"
      class="input"
      type="text"
      placeholder="搜索地点，例如「外滩」"
      autocomplete="off"
      @input="onInput"
      @keydown="onKeydown"
      @focus="open = results.length > 0"
    />

    <div v-if="busy" class="search__status tiny muted">搜索中…</div>

    <div v-if="failure" class="banner banner--danger">
      <div class="banner__body">
        <div class="banner__title">{{ failure.message }}</div>
        <div v-if="failure.hint" class="banner__hint">{{ failure.hint }}</div>
      </div>
    </div>

    <ul v-if="open" class="search__list">
      <li
        v-for="(poi, index) in results"
        :key="poi.id || `${poi.name}-${poi.lng}-${poi.lat}`"
        class="search__item"
        :class="{ 'search__item--active': index === activeIndex }"
        @mousedown.prevent="choose(poi)"
        @mouseenter="activeIndex = index"
      >
        <div class="search__name">{{ poi.name }}</div>
        <div class="search__meta tiny muted">
          {{ [poi.district, poi.address].filter(Boolean).join(' · ') || `${poi.lng}, ${poi.lat}` }}
        </div>
        <button
          class="search__stash"
          type="button"
          title="先存进想去清单，不排进今天"
          @mousedown.stop.prevent="emit('stash', poi)"
        >
          <ShoppingBasket :size="14" />
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.search {
  position: relative;
}

.search__status {
  margin-top: 4px;
}

.search__list {
  position: absolute;
  z-index: var(--z-pop);
  top: calc(100% + 4px);
  right: 0;
  left: 0;
  max-height: 320px;
  padding: 4px;
  margin: 0;
  overflow-y: auto;
  list-style: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-md);
}

.search__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  cursor: pointer;
  border-radius: calc(var(--radius) - 4px);
}

.search__name {
  flex: 1;
}

.search__stash {
  flex: 0 0 auto;
  padding: 2px 6px;
  font-size: 14px;
  background: none;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.search__stash:hover {
  background: var(--surface-2);
}

.search__item--active {
  background: var(--accent-soft);
}

.search__name {
  font-size: 14px;
  color: var(--text);
}

.search__meta {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
