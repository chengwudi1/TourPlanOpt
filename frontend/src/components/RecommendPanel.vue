<script setup lang="ts">
import { ref, watch } from 'vue'

import { ChevronDown, ExternalLink, Landmark, MoonStar, Plus, ShoppingBasket, UtensilsCrossed } from '@/components/icons'
import { useTripStore } from '@/stores/trip'
import type { Poi } from '@/types/domain'
import { apiFetch } from '@/utils/api'

const props = defineProps<{ city: string }>()

const store = useTripStore()

const open = ref(false)
const category = ref<'scenic' | 'food' | 'night'>('scenic')
const pois = ref<Poi[]>([])
const amapUrl = ref('')
const loading = ref(false)
const error = ref<{ message: string } | null>(null)
const loadedCategories = ref(new Set<string>())

const CATEGORIES: {
  key: 'scenic' | 'food' | 'night'
  label: string
  icon: typeof Landmark
}[] = [
  { key: 'scenic', label: '景点', icon: Landmark },
  { key: 'food', label: '小吃美食', icon: UtensilsCrossed },
  { key: 'night', label: '夜市', icon: MoonStar },
]

watch(
  () => [props.city, open.value, category.value] as const,
  async ([city, isOpen]) => {
    if (!city || !isOpen) return
    if (loadedCategories.value.has(`${city}:${category.value}`)) return
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams({ city, category: category.value })
      const data = await apiFetch<{ pois: Poi[]; amap_url: string }>(
        `/api/city/recommendations?${params}`,
      )
      pois.value = data.pois
      amapUrl.value = data.amap_url
      loadedCategories.value.add(`${city}:${category.value}`)
    } catch (err) {
      error.value = { message: (err as Error).message || '推荐加载失败' }
    } finally {
      loading.value = false
    }
  },
)

function add(poi: Poi) {
  store.addPlace({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function stash(poi: Poi) {
  store.stashAdd({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function added(poi: Poi): boolean {
  return store.currentPlaces.some((p) => p.amap_poi_id && p.amap_poi_id === poi.id)
}
</script>

<template>
  <div class="reco card">
    <button class="reco__head" type="button" @click="open = !open">
      <strong>发现</strong>
      <span class="tiny muted">
        {{ city ? `${city}的景点、小吃和夜市，点一下就加入行程` : '行程里填了目的地城市后，这里会推荐景点和美食' }}
      </span>
      <span class="reco__chevron" :class="{ 'reco__chevron--open': open }">
        <ChevronDown :size="15" />
      </span>
    </button>

    <div v-if="open" class="reco__body">
      <div class="reco__tabs">
        <button
          v-for="c in CATEGORIES"
          :key="c.key"
          class="reco__tab"
          :class="{ 'reco__tab--on': category === c.key }"
          type="button"
          @click="category = c.key"
        >
          <component :is="c.icon" class="ic" :size="13" /> {{ c.label }}
        </button>
      </div>

      <p v-if="!city" class="reco__empty tiny muted">
        双击天标签旁的行程名或在首页创建时填写目的地城市，就能收到推荐。
      </p>
      <p v-else-if="loading" class="reco__empty tiny muted">正在找 {{ city }} 的好去处…</p>
      <p v-else-if="error" class="reco__empty tiny muted">{{ error.message }}（稍后再试）</p>

      <ul v-else-if="pois.length" class="reco__list">
        <li v-for="poi in pois" :key="poi.id || poi.name" class="reco__item">
          <img
            v-if="poi.photo"
            class="reco__photo"
            :src="poi.photo"
            :alt="`${poi.name} 的照片`"
            loading="lazy"
            referrerpolicy="no-referrer"
          />
          <div class="reco__info">
            <div class="reco__name">{{ poi.name }}</div>
            <div class="tiny muted reco__addr">{{ poi.address || poi.district }}</div>
          </div>
          <button
            class="btn btn--sm"
            type="button"
            title="先存进想去清单"
            @click="stash(poi)"
          >
            <ShoppingBasket class="ic" :size="13" />
          </button>
          <button
            class="btn btn--sm"
            type="button"
            :disabled="added(poi)"
            @click="add(poi)"
          >
            <template v-if="!added(poi)"><Plus class="ic" :size="13" /> 加入</template>
            <template v-else>已加入</template>
          </button>
        </li>
      </ul>
      <p v-else class="reco__empty tiny muted">这一类暂时没有推荐，换个类目试试。</p>

      <a
        v-if="amapUrl"
        class="reco__amap tiny"
        :href="amapUrl"
        target="_blank"
        rel="noopener"
      >
        <ExternalLink class="ic" :size="12" /> 在高德地图中查看更多
      </a>
    </div>
  </div>
</template>

<style scoped>
.reco {
  overflow: hidden;
}

.reco__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  width: 100%;
  padding: 11px 12px;
  text-align: left;
  background: none;
  border: 0;
}

.reco__head span:not(.reco__chevron) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reco__chevron {
  color: var(--text-3);
  transition: transform var(--dur) var(--ease-inout);
}
.reco__chevron--open {
  transform: rotate(180deg);
}

.reco__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 12px 12px;
}

.reco__tabs {
  display: flex;
  gap: 6px;
}

.reco__tab {
  padding: 5px 11px;
  font-size: 13px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: 999px;
}

.reco__tab--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.reco__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 300px;
  padding: 0;
  margin: 0;
  overflow-y: auto;
  list-style: none;
}

.reco__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
}

.reco__photo {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 10px;
  background: var(--surface-2);
}

.reco__item:hover {
  background: var(--surface-2);
}

.reco__info {
  flex: 1;
  min-width: 0;
}

.reco__name {
  font-size: 14px;
  font-weight: 600;
}

.reco__addr {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reco__empty {
  padding: 10px 12px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

.reco__amap {
  color: var(--accent);
  text-decoration: none;
}
.reco__amap:hover {
  text-decoration: underline;
}
</style>
