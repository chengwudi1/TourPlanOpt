<script setup lang="ts">
import { ArrowRight, ShoppingBasket, X } from '@/components/icons'
import { useTripStore } from '@/stores/trip'

const store = useTripStore()
</script>

<template>
  <div v-if="store.stash.length" class="stash">
    <div class="stash__head">
      <strong class="stash__title"><ShoppingBasket class="ic" :size="14" /> 想去清单</strong>
      <span class="tiny muted">暂存候选地点，确认后加入指定日期</span>
    </div>

    <ul class="stash__list">
      <li v-for="item in store.stash" :key="item.id" class="stash__item">
        <img
          v-if="item.photo_url"
          class="stash__photo"
          :src="item.photo_url"
          :alt="`${item.name} 的照片`"
          loading="lazy"
          referrerpolicy="no-referrer"
        />
        <div class="stash__info">
          <div class="stash__name">{{ item.name }}</div>
          <div v-if="item.address" class="tiny muted stash__addr">{{ item.address }}</div>
        </div>
        <div class="stash__actions">
          <button
            class="btn btn--sm"
            type="button"
            :title="`加入 ${store.currentDay?.title || '第 1 天'}`"
            @click="store.promoteFromStash(item)"
          >
            <ArrowRight class="ic" :size="13" /> 今天
          </button>
          <button
            class="btn btn--sm btn--ghost stash__drop"
            type="button"
            title="从想去清单移除"
            @click="store.stashRemove(item.id)"
          >
            <X :size="14" />
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.stash__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 8px;
}

.stash__title {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--warn);
}

.stash__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.stash__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 7px 10px;
  background: var(--warn-soft);
  border: 2px dashed var(--warn-border);
  border-radius: var(--radius-lg);
}

.stash__info {
  flex: 1;
  min-width: 0;
}

.stash__photo {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 10px;
  background: var(--surface);
}

.stash__name {
  font-size: 14px;
  font-weight: 600;
}

.stash__addr {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stash__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
}

.stash__drop {
  padding: 4px 7px;
}
</style>
