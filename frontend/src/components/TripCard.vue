<script setup lang="ts">
import { ChevronRight, Clock, MapPin } from '@/components/icons'
import type { TripSummary } from '@/types/domain'
import { formatAgo } from '@/utils/time'

/**
 * 首页行程卡。`trip` 为 null 表示摘要还没回来（或这条 ID 已经不存在，父组件会把它摘掉），
 * 渲染成骨架而不是空白，避免首屏一屏跳。
 *
 * 整卡可点用 RouterLink 而不是 button：h3/p 塞进 button 是无效 HTML，而链接白送一个真实
 * href——中键新开、右键复制链接，对一个以分享链接为核心的应用正好。「最近打开」的记账不在
 * 这里做，由 TripView 挂载时负责：点一条打不开的分享 ID 不该留下永远点不进去的历史。
 */
defineProps<{ trip: TripSummary | null; tripId: string }>()

function meta(trip: TripSummary): string {
  const bits = [trip.city]
  if (trip.day_count) bits.push(`${trip.day_count} 天`)
  bits.push(trip.place_count ? `${trip.place_count} 个地点` : '还没有地点')
  return bits.filter(Boolean).join(' · ')
}
</script>

<template>
  <article class="tripcard card">
    <RouterLink class="tripcard__hit" :to="{ name: 'trip', params: { tripId } }">
      <div class="tripcard__cover" :class="{ 'tripcard__cover--photo': trip?.cover_photo }">
        <img
          v-if="trip?.cover_photo"
          :src="trip.cover_photo"
          :alt="`${trip.title || '未命名行程'}的封面`"
          loading="lazy"
          decoding="async"
          referrerpolicy="no-referrer"
        />
        <svg v-else class="tripcard__art" viewBox="0 0 120 56" aria-hidden="true">
          <path
            d="M8 42C22 42 24 16 40 16s18 24 32 24 14-18 28-18"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-dasharray="1 6"
          />
          <circle cx="8" cy="42" r="3" fill="currentColor" />
          <circle cx="100" cy="26" r="3" fill="currentColor" />
        </svg>
        <span v-if="!trip" class="skeleton tripcard__skeleton" />
      </div>

      <div class="tripcard__body">
        <h3 class="tripcard__title">{{ trip ? trip.title || '未命名行程' : '载入中…' }}</h3>
        <p class="tiny muted tripcard__meta">
          <MapPin class="ic" :size="12" />
          {{ trip ? meta(trip) : '　' }}
        </p>
        <p class="tiny tripcard__foot">
          <span class="tripcard__ago"><Clock class="ic" :size="12" /> {{ trip ? formatAgo(trip.updated_at) : '' }}</span>
          <span class="tripcard__go">打开<ChevronRight class="ic" :size="13" /></span>
        </p>
      </div>
    </RouterLink>
  </article>
</template>

<style scoped>
.tripcard {
  overflow: hidden;
  transition:
    border-color var(--dur) var(--ease-out),
    box-shadow var(--dur) var(--ease-out),
    transform var(--dur) var(--ease-out);
}
.tripcard:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}

/* 整张卡就是一个链接：TREK 的卡片没有「点标题才进得去」这种半吊子热区。 */
.tripcard__hit {
  display: flex;
  flex-direction: column;
  width: 100%;
  color: inherit;
  text-decoration: none;
  text-align: left;
}

.tripcard__cover {
  position: relative;
  display: grid;
  place-items: center;
  height: 92px;
  overflow: hidden;
  color: var(--ember);
  background: linear-gradient(135deg, var(--ember-soft), var(--surface-2));
  border-bottom: 1px solid var(--border-faint);
}

.tripcard__cover--photo {
  color: transparent;
  background: var(--surface-3);
}

.tripcard__cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform var(--dur-slow) var(--ease-out);
}
/* 封面推近：整卡只抬 2px 太客气，照片往里走一步才有「点开看看」的邀请感。
   圆角靠 .tripcard 的 overflow 兜，但封面带自己的 overflow 才挡得住放大后的下沿。 */
.tripcard:hover .tripcard__cover img {
  transform: scale(1.07);
}

.tripcard__art {
  width: 70%;
  opacity: 0.5;
}

/* 扫光交给全局 .skeleton，这里只负责铺满封面带——两处各写一套骨架动画会走形。 */
.tripcard__skeleton {
  position: absolute;
  inset: 0;
  border-radius: 0;
}

.tripcard__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 11px 12px 12px;
}

.tripcard__title {
  overflow: hidden;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tripcard__meta {
  display: flex;
  gap: 4px;
  align-items: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tripcard__foot {
  display: flex;
  align-items: center;
  margin-top: 4px;
  color: var(--text-3);
}

.tripcard__go {
  display: inline-flex;
  gap: 1px;
  align-items: center;
  margin-left: auto;
  color: var(--accent);
  opacity: 0;
  /* 带方向地进来：它指着「往右走」，所以从左边 4px 滑回原位，比凭空显形更读得懂。 */
  transform: translateX(-4px);
  transition:
    opacity var(--dur) var(--ease-out),
    transform var(--dur) var(--ease-out);
}

.tripcard__hit:hover .tripcard__go,
.tripcard__hit:focus-visible .tripcard__go {
  opacity: 1;
  transform: none;
}
</style>
