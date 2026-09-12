<script setup lang="ts">
import { computed } from 'vue'

import { CalendarDays, CheckCheck, ChevronRight, ListChecks, MapPin, Sparkles, Users, Wallet } from '@/components/icons'
import type { TripSummary } from '@/types/domain'
import { formatMoney } from '@/utils/money'
import { formatDateRange, countdownOf, needsWrapUp, phaseOf, readinessHints } from '@/utils/tripstatus'
import { formatAgo } from '@/utils/time'

/**
 * 最近一段旅行的封面卡。版式学 TREK：大图 + 压在图上的大字 + 一条票券式数据带。
 *
 * 数据带回答的是「这趟还差什么」：几天后出发、清单打勾到几件、预算花掉多少。这些字段
 * M18c 之后才真的有人填（新建抽屉一次建 N 天并写 days.date），所以倒计时不是装饰——
 * 没填日期的行程 countdownOf 直接给空串，宁可不显示也不摆一个「?? 天后」。
 */
const props = defineProps<{ trip: TripSummary }>()

const emit = defineEmits<{ open: []; finish: [] }>()

const countdown = computed(() => countdownOf(props.trip.start_date, props.trip.end_date))
const range = computed(() => formatDateRange(props.trip.start_date, props.trip.end_date))
const hints = computed(() => readinessHints(props.trip))
const wrapUp = computed(() => needsWrapUp(props.trip.status, phaseOf(props.trip.start_date, props.trip.end_date)))
const money = computed(() => {
  const t = props.trip
  if (!t.budget_cents) return t.spent_cents ? `已花 ${formatMoney(t.spent_cents)}` : '未设预算'
  return `${formatMoney(t.spent_cents)} / ${formatMoney(t.budget_cents)}`
})
const checklistText = computed(() => (props.trip.checklist_total ? `${props.trip.checklist_done}/${props.trip.checklist_total}` : '—'))
const ago = computed(() => {
  const built = formatAgo(props.trip.created_at)
  return built ? `建于 ${built}，最近编辑 ${formatAgo(props.trip.updated_at)}` : `最近编辑 ${formatAgo(props.trip.updated_at)}`
})
</script>

<template>
  <section class="hero">
    <div v-if="trip.cover_photo" class="hero__photo">
      <img :src="trip.cover_photo" :alt="`${trip.title || '未命名行程'}的封面`" decoding="async" referrerpolicy="no-referrer" />
    </div>
    <div v-else class="hero__fallback" aria-hidden="true">
      <svg viewBox="0 0 400 200" preserveAspectRatio="none">
        <path
          d="M20 160C70 160 78 60 130 60s60 92 112 92 44-64 96-64 40 40 40 40"
          fill="none"
          stroke="currentColor"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-dasharray="2 9"
        />
        <circle cx="20" cy="160" r="5" fill="currentColor" />
        <circle cx="358" cy="128" r="5" fill="currentColor" />
      </svg>
    </div>

    <div class="hero__inner">
      <div class="hero__top">
        <span class="hero__badge">{{ trip.city || '未设置城市' }}</span>
        <span
          v-if="countdown.label"
          class="hero__status tiny"
          :class="`hero__status--${countdown.tone}`"
          >{{ countdown.label }}</span
        >
        <span v-else class="hero__status tiny">{{ trip.place_count ? '继续编辑' : '先丢一个地点' }}</span>
      </div>

      <h2 class="hero__title">{{ trip.title || '未命名行程' }}</h2>

      <p class="hero__sub tiny">
        <CalendarDays class="ic" :size="12" />
        <span>{{ range || '未设置日期' }}</span>
        <span class="hero__sub-dot">·</span>
        <span>{{ ago }}</span>
      </p>

      <p v-if="hints.length" class="hero__hints tiny"><Sparkles class="ic" :size="12" /> {{ hints.join(' · ') }}</p>

      <div class="hero__stats card">
        <div class="hero__stat">
          <span class="hero__stat-key tiny"><Users class="ic" :size="12" /> 旅伴</span>
          <strong class="hero__stat-num">{{ trip.companion_count }}</strong>
          <span class="hero__stat-unit tiny">{{ trip.companion_count === 1 ? '人' : '位参与者' }}</span>
        </div>
        <div class="hero__stat">
          <span class="hero__stat-key tiny"><CalendarDays class="ic" :size="12" /> 行程</span>
          <strong class="hero__stat-num">{{ trip.day_count }}</strong>
          <span class="hero__stat-unit tiny">天</span>
        </div>
        <div class="hero__stat">
          <span class="hero__stat-key tiny"><MapPin class="ic" :size="12" /> 地点</span>
          <strong class="hero__stat-num">{{ trip.place_count }}</strong>
          <span class="hero__stat-unit tiny">个</span>
        </div>
        <div class="hero__stat">
          <span class="hero__stat-key tiny"><ListChecks class="ic" :size="12" /> 出行清单</span>
          <strong class="hero__stat-num">{{ checklistText }}</strong>
          <span class="hero__stat-unit tiny">{{ trip.checklist_total ? '已备好' : '未填写' }}</span>
        </div>
        <div class="hero__stat">
          <span class="hero__stat-key tiny"><Wallet class="ic" :size="12" /> 费用</span>
          <strong class="hero__stat-label">{{ money }}</strong>
          <span class="hero__stat-unit tiny">{{ trip.budget_cents ? '已花 / 预算' : '点开行程可设' }}</span>
        </div>

        <div class="hero__acts">
          <button v-if="wrapUp" class="btn btn--sm hero__wrap" type="button" @click="emit('finish')">
            <CheckCheck class="ic" :size="13" /> 标记完成
          </button>
          <button class="btn btn--primary hero__open" type="button" @click="emit('open')">
            打开行程 <ChevronRight class="ic" :size="14" />
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
}

.hero__photo,
.hero__fallback {
  position: absolute;
  inset: 0;
  z-index: -2;
}

/* hover 推进挂在这层容器、呼吸挂在 img 上：同一元素的 animation 会永久压过 transition，
   两个都写在一起就只剩一个能动。 */
.hero__photo {
  transition: transform var(--dur-entrance) var(--ease-out);
}
.hero:hover .hero__photo {
  transform: scale(1.045);
}

.hero__photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* 静止的照片最容易读出「贴图」，一个来回 22s 的缩放漂移慢到不会被注意，但画面活了。
     keyframes 用全局那份（动效基建），不在这里重复定义。 */
  animation: ken-burns var(--dur-drift) var(--ease-inout) infinite alternate;
  transform-origin: 60% 42%;
}

.hero__fallback {
  /* 本质是「假封面」：两种主题下都必须保持深色，因为上面压的是白字。所以这里故意不用
     ember 令牌——--ember-deep 在深色态是浅橙 #e8a675（那是给正文反着用的一套），
     拿它当底会让白字掉到 1.7:1。四档从深到亮，白字压在哪一档都在 4:1 以上。 */
  color: #f8e8db;
  background: linear-gradient(140deg, #8a4520, #98481f 42%, #b85c2c 78%, #cf7539);
}
.hero__fallback svg {
  width: 100%;
  height: 100%;
  opacity: 0.4;
}

/* 路线自绘：这条线是虚线点阵，stroke-dashoffset 派不上用场（只会让点往前爬，
   不会「长出来」）。改用一层从左推进的 clip-path，点阵图案原封不动。
   收到 -1% 而不是 0 是给线帽留余量，不然末端会被切掉半个像素。 */
.hero__fallback path {
  animation: route-draw 1.5s var(--ease-inout) calc(var(--stagger) * 2) backwards;
}
@keyframes route-draw {
  from {
    clip-path: inset(0 100% 0 0);
  }
  to {
    clip-path: inset(0 -1% 0 0);
  }
}

/* 端点跟着线到达的时间点冒出来：transform-box 让 transform-origin 落在图形自身
   的包围盒上，否则圆心会被当成用户坐标系原点，缩放时圆点乱飞。 */
.hero__fallback circle {
  transform-box: fill-box;
  transform-origin: center;
  animation: dot-pop var(--dur-slow) var(--ease-pop) backwards;
}
.hero__fallback circle:first-of-type {
  animation-delay: calc(var(--stagger) * 2);
}
.hero__fallback circle:last-of-type {
  animation-delay: 1.62s;
}
@keyframes dot-pop {
  from {
    opacity: 0;
    transform: scale(0.2);
  }
}

/* 文字全在底部那摞内容里，而内容摞的顶端在哪取决于视口和数据带换不换行（实测最矮的
   照片卡上标题能顶到 17%），所以按百分比找一条正好压在字下面的暗带是靠不住的。
   做法是整张压暗、顶部就从 .44 起：白字在最亮的雪景照片上也够 3:1，照片照样认得出。 */
.hero::after {
  position: absolute;
  inset: 0;
  z-index: -1;
  content: '';
  background: linear-gradient(
    180deg,
    rgba(30, 20, 10, 0.44) 0%,
    rgba(30, 20, 10, 0.52) 34%,
    rgba(30, 20, 10, 0.7) 66%,
    rgba(30, 20, 10, 0.82) 100%
  );
}

.hero__inner {
  display: flex;
  flex-direction: column;
  gap: 14px;
  justify-content: flex-end;
  /* 上面留白给照片，文字统一沉到遮罩的暗部；不写最小高度的话 inner 就是内容高度，
     大标题会顶到 0.18 的浅遮罩上，等于直接压在原图里最亮的那块天空上。 */
  min-height: clamp(300px, 46vh, 420px);
  padding: 18px 18px 18px;
}

.hero__top {
  display: flex;
  gap: 8px;
  align-items: center;
}

.hero__badge {
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ember-ink);
  background: var(--ember);
  border-radius: 999px;
}

/* 小字不跟遮罩赌照片明暗：自带深棕底，压在雪景上也是 7:1 以上。 */
.hero__status {
  padding: 3px 10px;
  margin-left: auto;
  color: #fff;
  background: rgba(24, 16, 9, 0.72);
  border-radius: 999px;
}
/* 三档实心色全部写死深色，不用 --ember / --ok：那两个令牌在深色主题是浅橙和亮绿，
   白字压上去只有 2.2–2.6:1。这颗胶囊压在照片上，必须和主题反着走。 */
.hero__status--soon {
  background: #8a4520;
}
.hero__status--live {
  background: #136f3c;
}
.hero__status--past {
  background: rgba(24, 16, 9, 0.52);
}

.hero__title {
  /* 大字是这张卡的主角：clamp 到 42px。遮罩是比例渐变，照片裁切后暗部落在哪没法保证，
     所以再补一道轻投影兜底——只糊边缘，不做描边。 */
  max-width: 22ch;
  font-size: clamp(26px, 4.6vw, 42px);
  font-weight: 700;
  line-height: 1.12;
  color: #fff;
  text-shadow: 0 1px 2px rgba(30, 20, 10, 0.45);
  text-wrap: balance;
}

.hero__sub {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
  margin: 0;
  color: rgba(255, 255, 255, 0.86);
  text-shadow: 0 1px 2px rgba(30, 20, 10, 0.4);
}
.hero__sub .ic {
  color: #f0b98a;
}
.hero__sub-dot {
  opacity: 0.6;
}

/* 「还差 2 项清单 · 没设预算」——首页该说的不是「你做得多好」，是「还差什么」。 */
.hero__hints {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  align-self: flex-start;
  padding: 3px 9px;
  margin: 0;
  color: #fff;
  background: rgba(138, 69, 32, 0.62);
  border-radius: 999px;
}
.hero__hints .ic {
  color: #f6c79c;
}

.hero__stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr)) auto;
  gap: 0;
  align-items: center;
  padding: 12px 6px;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border-color: var(--border-faint);
  box-shadow: var(--shadow-sm);
  /* 卡片先落、数据带晚三拍跟上：一次入场里有先后，才不是一整块闪现。
     父子各自动 transform 是叠加的，所以视觉上像带子被惯性甩了一下。 */
  animation: rise-in var(--dur-entrance) var(--ease-out) calc(var(--stagger) * 3) backwards;
}

.hero__stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 0 10px;
}

/* 票券式虚线分隔：TREK 用它替代硬边框，压在奶油底上不会显脏。 */
.hero__stat + .hero__stat {
  border-left: 1px dashed var(--border-strong);
}

.hero__stat-key {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: var(--text-2);
}

.hero__stat-num {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
}

/* 金额是这一格里唯一会长的字符串，宁可省略号也不许它把票券挤歪。 */
.hero__stat-label {
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.hero__stat-unit {
  overflow: hidden;
  color: var(--text-3);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.hero__acts {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-right: 8px;
  margin-left: 8px;
}

/* 「回来了但没整理」是旅行产品里最常见的沉默流失，所以这一档给一键而不是让人点进再说。
   实心留给「打开行程」：一条数据带里出现两个大色块，就等于没有主次。 */
.hero__wrap .ic {
  color: var(--ember-deep);
}

@media (max-width: 860px) {
  .hero__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 10px;
  }
  .hero__stat:nth-child(2n + 1) {
    border-left: 0;
  }
  .hero__acts {
    grid-column: 1 / -1;
    margin: 4px 8px 0;
  }
}
</style>
