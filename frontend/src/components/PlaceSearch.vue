<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import { LoaderCircle, Search, ShoppingBasket } from '@/components/icons'
import type { Poi } from '@/types/domain'
import { ApiError, apiFetch } from '@/utils/api'

const props = defineProps<{ city?: string; bare?: boolean }>()
const emit = defineEmits<{ select: [poi: Poi]; stash: [poi: Poi] }>()

// 示例地名跟着行程城市走：成都的行程里提示「外滩」会被当成串城的 bug。
// 只留城市、不留示例：侧栏 440px，带引号的示例一定被截成半句。
const placeholder = computed(() => {
  const city = props.city?.trim()
  return city ? `搜索${city}的地点` : '搜索地点'
})

const keyword = ref('')
const results = ref<Poi[]>([])
const open = ref(false)
const busy = ref(false)
const failure = ref<{ message: string; hint: string } | null>(null)
const activeIndex = ref(-1)
const inputEl = ref<HTMLInputElement | null>(null)

defineExpose({ focus: () => inputEl.value?.focus() })

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
  <div class="search" :class="{ 'search--bare': bare }">
    <div class="search__bar">
      <Search class="ic search__icon" :size="15" />
      <input
        ref="inputEl"
        v-model="keyword"
        class="search__input"
        type="text"
        :placeholder="placeholder"
        autocomplete="off"
        @input="onInput"
        @keydown="onKeydown"
        @focus="open = results.length > 0"
      />
      <LoaderCircle v-if="busy" class="ic search__spin" :size="15" />
    </div>

    <div v-if="failure" class="banner banner--danger search__fail">
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
          title="加入想去，不排进这一天"
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

.search__bar {
  display: flex;
  gap: 8px;
  align-items: center;
  height: 38px;
  padding: 0 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition:
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.search__bar:focus-within {
  background: var(--surface);
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

/* 并条模式：外层那条「添加地点」栏自带描边与焦点环，这里再描一次就是两层框、亮两次。
   一个受焦面只留一层框，视线才知道焦点落在整条栏上而不是那个输入框上。 */
.search--bare .search__bar {
  height: 100%;
  padding: 0;
  background: none;
  border: 0;
  border-radius: 0;
}

.search--bare .search__bar:focus-within {
  border-color: transparent;
  box-shadow: none;
}

.search__icon {
  color: var(--text-3);
}

.search__input {
  flex: 1;
  min-width: 0;
  /* 文字框的落点本来就该容得下一根手指（O6 实测只有 23px）。 */
  min-height: 28px;
  padding: 0;
  background: none;
  border: 0;
  outline: none;
}

.search__input::placeholder {
  color: var(--text-2);
}

.search__spin {
  color: var(--text-2);
  animation: search-spin 0.9s linear infinite;
}

@keyframes search-spin {
  to {
    transform: rotate(360deg);
  }
}

.search__fail {
  margin-top: 6px;
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
  font-size: calc(14px * var(--fs-scale));
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
  font-size: calc(14px * var(--fs-scale));
  color: var(--text);
}

/* ---------- 手机 ---------- */
@media (max-width: 860px) {
  /* 320px 的裸限高假设了桌面视口：键盘弹起后下拉的下半段整个沉进键盘背后，
     看得见的位置只剩前几条。按视口收一档。 */
  .search__list {
    max-height: min(320px, 42dvh);
  }
  .search__item {
    min-height: 44px;
  }
  .search__stash {
    min-height: 32px;
    padding: 4px 10px;
  }
}

.search__meta {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
