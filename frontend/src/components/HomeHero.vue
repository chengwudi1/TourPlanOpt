<script setup lang="ts">
import { computed } from 'vue'

import { CalendarDays, CheckCheck, ChevronRight, ListChecks, MapPin, Sparkles, Users, Wallet } from '@/components/icons'
import RouteArt from '@/components/RouteArt.vue'
import { useCountUp } from '@/composables/useCountUp'
import { useNow } from '@/composables/useNow'
import type { TripSummary } from '@/types/domain'
import { summaryCover } from '@/utils/builtinCovers'
import { formatMoney } from '@/utils/money'
import { formatDateRange, countdownOf, needsWrapUp, parseDateOnly, phaseOf, readinessHints } from '@/utils/tripstatus'
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
/** 与网格卡同一条两档链：自己那张 > 按 trip_id 挑的内置默认那张。 */
const cover = computed(() => summaryCover(props.trip))
const hints = computed(() => readinessHints(props.trip))
const wrapUp = computed(() => needsWrapUp(props.trip.status, phaseOf(props.trip.start_date, props.trip.end_date)))
const money = computed(() => {
  const t = props.trip
  if (!t.budget_cents) return t.spent_cents ? `已花 ${formatMoney(t.spent_cents)}` : '未设预算'
  return `${formatMoney(t.spent_cents)} / ${formatMoney(t.budget_cents)}`
})
const ago = computed(() => {
  const built = formatAgo(props.trip.created_at)
  return built ? `建于 ${built}，最近编辑 ${formatAgo(props.trip.updated_at)}` : `最近编辑 ${formatAgo(props.trip.updated_at)}`
})

// -- 活起来（M26）----------------------------------------------------------------------
// 数字滚动只在挂载这一次有意义，所以 useCountUp 的底数是 0；之后别人改了数，
// 它从当前读数接着走，不会每次都从头滚一遍。

const companions = useCountUp(() => props.trip.companion_count)
const dayNum = useCountUp(() => props.trip.day_count)
const placeNum = useCountUp(() => props.trip.place_count)
const checklistDone = useCountUp(() => props.trip.checklist_done)
const checklistTotal = useCountUp(() => props.trip.checklist_total)

const checklistText = computed(() =>
  props.trip.checklist_total ? `${checklistDone.value}/${checklistTotal.value}` : '—',
)

const clock = useNow(1000)
/** 出发胶囊：三天开外「N 天后出发」就够准，秒在那时只是噪声。进了三天就换成分秒递进——
 *  首页唯一会自己动的数字，放在最该被盯着的那一格。 */
const liveCountdown = computed(() => {
  const cd = countdown.value
  if (cd.phase !== 'upcoming' || cd.days === null || cd.days > 3) return ''
  const start = parseDateOnly(props.trip.start_date)
  if (!start) return ''
  const left = Math.floor((start.getTime() - clock.value) / 1000)
  if (left <= 0) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  const d = Math.floor(left / 86400)
  const rest = left - d * 86400
  const hms = `${pad(Math.floor(rest / 3600))}:${pad(Math.floor(rest / 60) % 60)}:${pad(rest % 60)}`
  return d ? `${d} 天 ${hms}` : hms
})
const statusText = computed(() =>
  liveCountdown.value ? `距出发 ${liveCountdown.value}` : countdown.value.label,
)
</script>

<template>
  <div class="hero-wrap">
  <section class="hero" :class="{ 'hero--nophoto': !cover }">
    <div v-if="cover" class="hero__photo">
      <img :src="cover" :alt="`${trip.title || '未命名行程'}的封面`" decoding="async" referrerpolicy="no-referrer" />
    </div>
    <div v-else class="hero__fallback" aria-hidden="true">
      <RouteArt />
    </div>

    <div class="hero__inner">
      <div class="hero__top">
        <span class="hero__badge">{{ trip.city || '未设置城市' }}</span>
        <span
          v-if="statusText"
          class="hero__status tiny"
          :class="`hero__status--${countdown.tone}`"
          >{{ statusText }}</span
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

      <div class="hero__acts">
        <button v-if="wrapUp" class="btn btn--sm hero__wrap" type="button" @click="emit('finish')">
          <CheckCheck class="ic" :size="13" /> 标记完成
        </button>
        <button class="btn btn--primary hero__open" type="button" @click="emit('open')">
          打开行程 <ChevronRight class="ic" :size="14" />
        </button>
      </div>
    </div>
  </section>

  <!-- 数据带搬到英雄卡外面（F 的 .tiles）：五块糖果瓷砖嵌在照片里会跟画面抢色，
       落在纸面上才是「票根撕下来排一排」的读法。 -->
  <div class="tiles">
    <div class="tile tile--a">
      <span class="tile__key tiny"><Users class="ic" :size="12" /> 旅伴</span>
      <strong class="tile__num">{{ companions }}</strong>
      <span class="tile__unit tiny">{{ trip.companion_count === 1 ? '人' : '位参与者' }}</span>
    </div>
    <div class="tile tile--b">
      <span class="tile__key tiny"><CalendarDays class="ic" :size="12" /> 行程</span>
      <strong class="tile__num">{{ dayNum }}</strong>
      <span class="tile__unit tiny">天</span>
    </div>
    <div class="tile tile--c">
      <span class="tile__key tiny"><MapPin class="ic" :size="12" /> 地点</span>
      <strong class="tile__num">{{ placeNum }}</strong>
      <span class="tile__unit tiny">个</span>
    </div>
    <div class="tile tile--d">
      <span class="tile__key tiny"><ListChecks class="ic" :size="12" /> 出行清单</span>
      <strong class="tile__num">{{ checklistText }}</strong>
      <span class="tile__unit tiny">{{ trip.checklist_total ? '已备好' : '未填写' }}</span>
    </div>
    <div class="tile tile--e">
      <span class="tile__key tiny"><Wallet class="ic" :size="12" /> 费用</span>
      <strong class="tile__num tile__num--word" :title="money">{{ money }}</strong>
      <span class="tile__unit tiny">{{ trip.budget_cents ? '已花 / 预算' : '点开行程可设' }}</span>
    </div>
  </div>
  </div>
</template>

<style scoped>
/* 英雄卡＝一枚裱进白框的贴纸（F 的 .hero）：30px 级圆角 + 6px 白边。
   白框是半透的（.55），照片亮部会把它透出来一点，边才不像贴上去的。 */
.hero {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  border: 6px solid rgba(255, 255, 255, 0.55);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-md);
}

/* 英雄卡 + 瓷砖带是一整块：英雄卡晚 60ms 入场（--base 由调用方给），瓷砖跟它一起动。 */
.hero-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
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
  transition: transform var(--dur-slow) var(--ease);
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
  /* 没有封面就不装成有封面：这块是「地图底」，不是「假照片」。所以它跟着主题走——
     亮色态是浅蓝到纸色的一层淡彩，深色态自己落回夜航蓝；压在上面的是墨色大字，
     不是白字。原来那四档深色渐变在奶油色的首页上就是一块黑石板，越干净越像漏了底。
     线本身（含两条入场动画）在 components/RouteArt.vue，这里只决定它压多深。 */
  color: var(--accent);
  opacity: 0.5;
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
    rgba(11, 22, 26, 0.44) 0%,
    rgba(11, 22, 26, 0.52) 34%,
    rgba(11, 22, 26, 0.7) 66%,
    rgba(11, 22, 26, 0.82) 100%
  );
}

/* -- 没有封面的那一路：淡彩底 + 墨色字，整块留在纸面上，不再是一枚深色石板 ---------- */
.hero--nophoto {
  background: linear-gradient(150deg, var(--accent-soft) 0%, var(--bg) 54%, var(--surface-2) 100%);
}

.hero--nophoto::after {
  background: none;
}

/* 照片卡要高，是因为照片要留出被看见的部分；淡彩底留那么多空白就是空。 */
.hero--nophoto .hero__inner {
  min-height: clamp(240px, 34vh, 320px);
}

.hero--nophoto .hero__title {
  color: var(--ink);
  text-shadow: none;
}

.hero--nophoto .hero__sub {
  color: var(--text-2);
  text-shadow: none;
}
.hero--nophoto .hero__sub .ic {
  color: var(--accent);
}

.hero--nophoto .hero__hints {
  color: var(--accent-strong);
  background: color-mix(in srgb, var(--surface) 74%, transparent);
}
.hero--nophoto .hero__hints .ic {
  color: var(--accent);
}

/* 倒计时胶囊：照片那套是「实心深底 + 白字」，淡彩底上要换成软底 + 深色字，
   否则一块棕色贴在浅蓝上，读起来像贴错了卡。 */
.hero--nophoto .hero__status {
  color: var(--text);
  background: var(--surface-3);
}
.hero--nophoto .hero__status--soon {
  color: var(--warn);
  background: var(--warn-soft);
}
.hero--nophoto .hero__status--live {
  color: var(--ok);
  background: var(--ok-soft);
}
.hero--nophoto .hero__status--past {
  color: var(--text-2);
  background: var(--surface-3);
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

/* 城市胶囊＝柠黄一块（F 的 .chip.city）：压在照片上，所以两档主题都不反色。 */
.hero__badge {
  padding: 3px 10px;
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  color: var(--city-chip-ink);
  background: var(--city-chip);
  border-radius: var(--radius-pill);
}

/* 三档实心色走 --photo-*：那组令牌刻意不跟主题反色（照片在深色态也还是那张照片），
   压在照片上的白字才不会掉到 2:1。 */
.hero__status {
  padding: 3px 10px;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: #fff;
  background: var(--photo-scrim);
  border-radius: var(--radius-pill);
}
.hero__status--soon {
  background: var(--photo-soon);
}
.hero__status--live {
  background: var(--photo-live);
}
.hero__status--past {
  background: rgba(12, 20, 19, 0.55);
}

.hero__title {
  /* 大字是这张卡的主角（F 的 .hero-title 66px/800）：中文落到系统黑体的粗档，
     拉丁与数字走打包的 Bricolage，两种字面在同一行里排。字距收一点，大字不收会散。
     遮罩是比例渐变，照片裁切后暗部落在哪没法保证，所以再补一道软投影兜底。 */
  max-width: 22ch;
  font-family: var(--font-display);
  font-size: clamp(30px, 4.8vw, 52px);
  font-weight: 800;
  line-height: 1.08;
  letter-spacing: -0.02em;
  color: #fff;
  text-shadow: 0 2px 16px rgba(11, 22, 26, 0.4);
  text-wrap: balance;
}

.hero__sub {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
  margin: 0;
  color: rgba(255, 255, 255, 0.86);
  text-shadow: 0 1px 2px rgba(11, 22, 26, 0.4);
}
.hero__sub .ic {
  color: #a9d3ee;
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
  background: rgba(10, 63, 97, 0.66);
  border-radius: var(--radius-pill);
}
.hero__hints .ic {
  color: #bcdff5;
}

/* ---------- 数据带：五块糖果瓷砖（F 的 .tiles/.tile） ---------- */
.tiles {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

/* 瓷砖是实色块，不参与玻璃层——画面里的高饱和只留给它们和果冻按钮。
   数字是大字档（800 重、26px），糖果墨只需过 3:1，每块的最差值在
   docs/_fskin-check.mjs 里过过账。 */
.tile {
  padding: 12px 14px 10px;
  border-radius: var(--radius);
  box-shadow: var(--shadow-md);
}
.tile--a {
  background: var(--candy-1);
}
.tile--b {
  background: var(--candy-2);
}
.tile--c {
  background: var(--candy-3);
}
.tile--d {
  background: var(--candy-4);
}
.tile--e {
  background: var(--candy-5);
}

.tile__key {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: var(--text-2);
}

.tile__num {
  display: block;
  margin-top: 4px;
  font-family: var(--font-display);
  font-size: calc(26px * var(--fs-scale));
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  letter-spacing: -0.02em;
  color: var(--candy-1-ink);
}
.tile--b .tile__num {
  color: var(--candy-2-ink);
}
.tile--c .tile__num {
  color: var(--candy-3-ink);
}
.tile--d .tile__num {
  color: var(--candy-4-ink);
}
.tile--e .tile__num {
  color: var(--candy-5-ink);
}

/* 金额是唯一会长的字符串：缩到标签字号，宁可用省略号也不许把这一行撑破。 */
.tile__num--word {
  overflow: hidden;
  font-size: calc(17px * var(--fs-scale));
  font-weight: 700;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tile__unit {
  display: block;
  margin-top: 3px;
  color: var(--text-3);
}

/* CTA 收在英雄卡右下（F 的 .cta）：实心果冻只留「打开行程」一颗。 */
.hero__acts {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  margin-top: 2px;
}
.hero__open {
  padding: 10px 18px;
  font-size: calc(15px * var(--fs-scale));
}

/* 「回来了但没整理」是旅行产品里最常见的沉默流失，所以这一档给一键而不是让人点进再说。
   实心留给「打开行程」：一颗卡的左下角出现两个大色块，就等于没有主次。 */
.hero__wrap .ic {
  color: var(--ember-deep);
}

@media (max-width: 860px) {
  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
