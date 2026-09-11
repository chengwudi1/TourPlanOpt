<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import {
  Archive,
  CalendarDays,
  CheckCheck,
  ChevronRight,
  Clock,
  Copy,
  Ellipsis,
  ListChecks,
  MapPin,
  RotateCcw,
  Wallet,
  X,
} from '@/components/icons'
import type { TripStatus, TripSummary } from '@/types/domain'
import { formatMoney } from '@/utils/money'
import { countdownOf, formatDateRange } from '@/utils/tripstatus'
import { formatAgo } from '@/utils/time'

/**
 * 首页行程卡。`trip` 为 null 表示摘要还没回来（或这条 ID 已经不存在，父组件会把它摘掉），
 * 渲染成骨架而不是空白，避免首屏一屏跳。
 *
 * 整卡可点用 RouterLink 而不是 button：h3/p 塞进 button 是无效 HTML，而链接白送一个真实
 * href——中键新开、右键复制链接，对一个以分享链接为核心的应用正好。「最近打开」的记账不在
 * 这里做，由 TripView 挂载时负责：点一条打不开的分享 ID 不该留下永远点不进去的历史。
 *
 * 卡片菜单用 Teleport 挂到 body：卡片为了兜住放大后的封面带 `overflow: hidden`，
 * 一个 4 项的下拉在里面必然被裁掉。定位靠按钮的视口矩形，滚一下就让位。
 */
const props = defineProps<{ trip: TripSummary | null; tripId: string }>()

const emit = defineEmits<{
  status: [TripStatus]
  hide: []
  copy: []
}>()

const countdown = computed(() => countdownOf(props.trip?.start_date, props.trip?.end_date))
const range = computed(() => formatDateRange(props.trip?.start_date, props.trip?.end_date))

const tags = computed(() => {
  const t = props.trip
  if (!t) return []
  const list: { icon: typeof CalendarDays; text: string }[] = []
  if (t.checklist_total) list.push({ icon: ListChecks, text: `清单 ${t.checklist_done}/${t.checklist_total}` })
  if (t.budget_cents) list.push({ icon: Wallet, text: `${formatMoney(t.spent_cents)} / ${formatMoney(t.budget_cents)}` })
  else if (t.spent_cents) list.push({ icon: Wallet, text: `已花 ${formatMoney(t.spent_cents)}` })
  return list
})

function meta(trip: TripSummary): string {
  const bits = [trip.city]
  if (trip.day_count) bits.push(`${trip.day_count} 天`)
  bits.push(trip.place_count ? `${trip.place_count} 个地点` : '还没有地点')
  return bits.filter(Boolean).join(' · ')
}

/* ---------- 菜单 ---------- */

const menuOpen = ref(false)
const menuEl = ref<HTMLElement | null>(null)
const triggerEl = ref<HTMLElement | null>(null)
const menuPos = ref({ top: 0, left: 0 })
const MENU_W = 168
const MENU_ITEM_H = 34
const MENU_PAD = 16

const items = computed(() => {
  const status = props.trip?.status ?? 'planning'
  const list: { key: string; label: string; icon: typeof Archive; onClick: () => void; danger?: boolean }[] = []
  if (status === 'planning') {
    list.push({ key: 'finish', label: '标记为已完成', icon: CheckCheck, onClick: () => emit('status', 'finished') })
  } else {
    list.push({ key: 'reopen', label: '放回规划中', icon: RotateCcw, onClick: () => emit('status', 'planning') })
  }
  // 归档态只有一条出路：回到规划中。再补一个「取消归档」就是把同一个动作念两遍。
  if (status !== 'archived') {
    list.push({ key: 'archive', label: '归档', icon: Archive, onClick: () => emit('status', 'archived') })
  }
  list.push({ key: 'copy', label: '复制分享链接', icon: Copy, onClick: () => emit('copy') })
  list.push({ key: 'hide', label: '从首页移除', icon: X, onClick: () => emit('hide'), danger: true })
  return list
})

function closeMenu() {
  menuOpen.value = false
  document.removeEventListener('pointerdown', onOutside, true)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('scroll', closeMenu, true)
  window.removeEventListener('resize', closeMenu)
}

function onOutside(evt: Event) {
  const el = evt.target as Node | null
  if (el && (menuEl.value?.contains(el) || triggerEl.value?.contains(el))) return
  closeMenu()
}

function onKey(evt: KeyboardEvent) {
  if (evt.key === 'Escape') closeMenu()
}

function openMenu(evt: MouseEvent) {
  const rect = (evt.currentTarget as HTMLElement).getBoundingClientRect()
  const h = MENU_ITEM_H * items.value.length + MENU_PAD
  const below = rect.bottom + 6
  menuPos.value = {
    top: below + h > window.innerHeight ? Math.max(8, rect.top - 6 - h) : below,
    left: Math.max(8, Math.min(rect.right - MENU_W, window.innerWidth - MENU_W - 8)),
  }
  menuOpen.value = true
  document.addEventListener('pointerdown', onOutside, true)
  document.addEventListener('keydown', onKey, true)
  window.addEventListener('scroll', closeMenu, true)
  window.addEventListener('resize', closeMenu)
}

function pick(item: { onClick: () => void }) {
  closeMenu()
  item.onClick()
}

onBeforeUnmount(closeMenu)
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

        <span
          v-if="trip && countdown.label"
          class="tripcard__count"
          :class="`tripcard__count--${countdown.tone}`"
        >{{ countdown.label }}</span>
      </div>

      <div class="tripcard__body">
        <h3 class="tripcard__title">{{ trip ? trip.title || '未命名行程' : '载入中…' }}</h3>
        <p class="tiny muted tripcard__meta">
          <MapPin class="ic" :size="12" />
          {{ trip ? meta(trip) : '　' }}
        </p>
        <div v-if="trip && (range || tags.length)" class="tripcard__tags">
          <span v-if="range" class="tripcard__tag"><CalendarDays class="ic" :size="11" /> {{ range }}</span>
          <span v-for="t in tags" :key="t.text" class="tripcard__tag">
            <component :is="t.icon" class="ic" :size="11" /> {{ t.text }}
          </span>
        </div>
        <p class="tiny tripcard__foot">
          <span class="tripcard__ago"><Clock class="ic" :size="12" /> {{ trip ? formatAgo(trip.updated_at) : '' }}</span>
          <span class="tripcard__go">打开<ChevronRight class="ic" :size="13" /></span>
        </p>
      </div>
    </RouterLink>

    <button
      v-if="trip"
      ref="triggerEl"
      class="tripcard__more"
      type="button"
      aria-label="更多操作"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      @click="menuOpen ? closeMenu() : openMenu($event)"
    >
      <Ellipsis :size="15" />
    </button>

    <Teleport to="body">
      <div
        v-if="menuOpen"
        ref="menuEl"
        class="tripcard__menu card"
        role="menu"
        :style="{ top: `${menuPos.top}px`, left: `${menuPos.left}px` }"
      >
        <button
          v-for="item in items"
          :key="item.key"
          class="tripcard__menu-item"
          :class="{ 'tripcard__menu-item--danger': item.danger }"
          type="button"
          role="menuitem"
          @click="pick(item)"
        >
          <component :is="item.icon" class="ic" :size="13" /> {{ item.label }}
        </button>
      </div>
    </Teleport>
  </article>
</template>

<style scoped>
.tripcard {
  position: relative;
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

/* 倒计时胶囊压在照片上，所以自带深色底，不跟照片明暗赌运气。
   三档底色一律写死深色，不用 --ember / --ok：那两个令牌在深色主题是浅橙 #d98a54 和
   亮绿 #34b96f，白字压上去只有 2.2–2.6:1。这里是「压在图上的实心胶囊」，和 HomeHero
   的假封面同理，必须和主题反着走。 */
.tripcard__count {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.6;
  color: #fff;
  background: rgba(24, 16, 9, 0.68);
  border-radius: 999px;
  backdrop-filter: blur(4px);
}
.tripcard__count--soon {
  background: #8a4520;
}
.tripcard__count--live {
  background: #136f3c;
}
.tripcard__count--past {
  background: rgba(24, 16, 9, 0.5);
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

/* 进度类信息一行带过：日期区间、清单、预算——缺哪项就不占位。 */
.tripcard__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.tripcard__tag {
  display: inline-flex;
  gap: 3px;
  align-items: center;
  padding: 1px 7px;
  font-size: 11px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid var(--border-faint);
  border-radius: 999px;
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

/* 菜单按钮压在封面右上角：常驻而不是 hover 才出现，触屏没有 hover。 */
.tripcard__more {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: var(--z-pop);
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  color: #fff;
  background: rgba(24, 16, 9, 0.55);
  border: 0;
  border-radius: 50%;
  backdrop-filter: blur(4px);
  transition:
    background var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}
.tripcard__more:hover {
  background: rgba(24, 16, 9, 0.8);
}
.tripcard__more:active {
  transform: scale(0.92);
}

/* Teleport 到 body 之后 scoped 样式照样跟着组件走，但位置是视口坐标（fixed）。
   z 要跳到 notice 档：--z-pop 是「卡片内部的下拉」，挂到 body 后它会和 sticky 顶栏、
   右下 FAB 抢层级，菜单被压在下面就没法点了。 */
.tripcard__menu {
  position: fixed;
  z-index: var(--z-notice);
  display: flex;
  flex-direction: column;
  width: 168px;
  padding: 4px;
  box-shadow: var(--shadow-pop);
  animation: rise-in var(--dur) var(--ease-out) backwards;
}

.tripcard__menu-item {
  display: flex;
  gap: 7px;
  align-items: center;
  padding: 7px 9px;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}
.tripcard__menu-item:hover {
  background: var(--surface-2);
}
.tripcard__menu-item .ic {
  color: var(--text-3);
}
.tripcard__menu-item--danger {
  color: var(--danger);
}
.tripcard__menu-item--danger .ic {
  color: var(--danger);
}
</style>
