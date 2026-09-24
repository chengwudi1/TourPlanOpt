<script setup lang="ts">
import { ArrowRight, ShoppingBasket, X } from '@/components/icons'
import { useTripStore } from '@/stores/trip'

const store = useTripStore()
</script>

<template>
  <div v-if="store.stash.length" class="stash">
    <div class="stash__head">
      <strong class="stash__title"><ShoppingBasket class="ic" :size="14" /> 想去</strong>
      <span class="tiny muted">还没排进某一天的地点，随时可以排</span>
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
            <ArrowRight class="ic" :size="13" /> 排进这一天
          </button>
          <button
            class="btn btn--sm btn--ghost stash__drop"
            type="button"
            title="从想去移除"
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
  font-size: calc(14px * var(--fs-scale));
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

/* 手机上「放回行程 / 彻底删除」是这一屏仅有的两个动作，落点要按手指来。 */
@media (max-width: 860px) {
  .stash__item {
    min-height: 56px;
  }
  .stash__drop {
    min-height: 34px;
    padding: 4px 10px;
  }
}
</style>
