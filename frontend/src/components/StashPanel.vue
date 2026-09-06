<script setup lang="ts">
import { useTripStore } from '@/stores/trip'

const store = useTripStore()
</script>

<template>
  <div v-if="store.stash.length" class="stash">
    <div class="stash__head">
      <strong>🧺 想去清单</strong>
      <span class="tiny muted">先存着，想好了再放进某一天</span>
    </div>

    <ul class="stash__list">
      <li v-for="item in store.stash" :key="item.id" class="stash__item">
        <div class="stash__info">
          <div class="stash__name">{{ item.name }}</div>
          <div v-if="item.address" class="tiny muted stash__addr">{{ item.address }}</div>
        </div>
        <div class="stash__actions">
          <button
            class="btn btn--sm"
            type="button"
            :title="`放进 ${store.currentDay?.title || '第 1 天'}`"
            @click="store.promoteFromStash(item)"
          >
            ➜ 今天
          </button>
          <button
            class="btn btn--sm btn--ghost stash__drop"
            type="button"
            title="不去了"
            @click="store.stashRemove(item.id)"
          >
            ✕
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
  background: var(--lemon-soft, #fff8e0);
  border: 2px dashed var(--ink);
  border-radius: 14px;
}

.stash__info {
  flex: 1;
  min-width: 0;
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
