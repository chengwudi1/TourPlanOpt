<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import MastCoverGl from '@/components/MastCoverGl.vue'
import RouteArt from '@/components/RouteArt.vue'
import { ImagePlus } from '@/components/icons'
import { useReduceMotion } from '@/composables/useReduceMotion'
import { MOTION, useMotion } from '@/composables/useMotion'
import { useTripStore } from '@/stores/trip'
import type { CoverSource } from '@/types/domain'
import { formatMoney } from '@/utils/money'
import { countdownOf, formatDateRange } from '@/utils/tripstatus'

/**
 * 海报头（M38 A 版式）：照片铺满整张卡，字压在照片下半部。
 *
 * 为什么字又搬回照片上：票券那一版把照片吃掉三分之二（手机 214px 里 139px 是票券），
 * 第一眼读到的是一条白色卡片，照片只剩上面 62px 的边——「像贴进来的一张截图」的病根在这。
 * A 让照片整幅露出来，字直接压在它上面，版面只剩一件事：这趟旅行本身。
 *
 * 白字压任意照片的对比度由 --scrim-hero 保证（与首页英雄卡同一条配方，实测账在
 * docs/_contrast-mast.mjs：六张内置封面逐张量最亮像素，大字 ≥3:1）。kicker 胶囊撤了，
 * 日期与天数变成标题下的元信息行；「当前使用的是默认封面」那句没了，改成右上角一枚
 * 「默认」小胶囊——它说的还是同一件事，只是不再占版面。
 *
 * 「不像截图」靠的是三件动效，不是靠字压在哪：照片 22s Ken Burns 呼吸 + hover 推进、
 * 滚动视差（照片比列表慢 0.34 倍、封顶 40px）、指针在照片上留一道柔光带。入场是三段——
 * 照片从 1.12 落回 1.0、右上角封面胶囊先淡入、标题栈最后升起。
 *
 * 收起由宿主（TripView 的 `.panel__scroll`）决定，这里只吃 `collapsed` 与 `scrollY`：
 * 组件自己监听滚动就变成「两块界面互相偷看」，而滚动条明明在宿主那一层。反方向只递一件
 * 东西——`rootEl`，让宿主自己量这块占掉多少（见下面那条 expose），而不是替它猜。
 */
const props = defineProps<{ collapsed: boolean; scrollY: number }>()

const emit = defineEmits<{ rename: []; pickCover: [] }>()

/** 展开态的封面有多高，只有这一层量得到（`height: var(--mast-h)`，收起时是 0）。
 *  宿主收这块的时候等于把列表的视口抬高同样一截，那一截得原样垫回清单末尾，读数才不会被
 *  浏览器夹回去——所以露出去的是元素本身，不是组件替宿主猜的那个高度。 */
const rootEl = ref<HTMLElement | null>(null)

defineExpose({ rootEl })

const store = useTripStore()

const title = computed(() => store.trip?.title ?? '')
const city = computed(() => store.trip?.city ?? '')

const cover = computed(() => store.coverPhoto)
/** 坏图不是「没有图」这一档的装饰——它是同一个兜底形态，所以走同一条分支，
 *  而不是留下一个 1×1 的裂图标压在字下面。记的是**哪一张**坏过：`<img>` 在 `v-if` 里，
 *  一旦判坏就连根拔起，光靠新图的 `load` 清不掉这个标记（那时候已经没有元素在加载了）。
 *  按 URL 记，换一张就自动重新挂载重试——一次网络抖动不该把海报永久打死。 */
const brokenUrl = ref('')
const hasPhoto = computed(() => !!cover.value && brokenUrl.value !== cover.value)
/** WebGL 档是否真的在画。它只由子组件的两条事件决定——`<img>` 一直在底下画着，
 *  所以这里为假时不需要任何「回落动作」，CSS 那两条动画本来就是它的。 */
const glLive = ref(false)
/** 出的是两档链里的哪一档。屏幕上读它的有两处（右上角那枚「默认」胶囊、复验探针）。 */
const coverSource = computed<CoverSource>(() => store.coverSource)
/** 还站在默认那张上：链头空着，或者内置资产读不出来（同一档的兜底形态，胶囊照样要给）。 */
const usingDefault = computed(() => coverSource.value === 'builtin')
function onImgError() {
  brokenUrl.value = cover.value
}

/** 日期区间与出发倒计时：天的 `date` 可以整段没填，也可以只填了中间几天，所以取已知
 *  日期的最早/最晚两端，而不是 `days[0]` 和 `days.at(-1)`。ISO 串按字典序即时间序。 */
const dateSpan = computed(() => {
  const dates = store.days
    .map((d) => d.date)
    .filter((x): x is string => !!x)
    .sort()
  return { start: dates[0] ?? null, end: dates.at(-1) ?? null }
})
const dateRange = computed(() => formatDateRange(dateSpan.value.start, dateSpan.value.end))
/** 胶囊与首页那颗同源：没日期就没有 label，宁可不显示也不摆一个「?? 天后」。 */
const countdown = computed(() => countdownOf(dateSpan.value.start, dateSpan.value.end))

/** 标题下的第一行：天数与日期区间。城市不在这—它是上面那枚黄胶囊。
 *  三段里任何一段没填，整段连同它前面那个分隔符一起消失：「· 10月1日」这种开头
 *  的空段比没有这一段更难看。 */
const metaLine = computed(() => {
  const parts = [store.days.length ? `${store.days.length} 天` : '', dateRange.value]
  return parts.filter(Boolean).join(' · ')
})

const stats = computed(() => {
  const parts: string[] = []
  if (store.places.length) parts.push(`${store.places.length} 个地点`)
  if (store.participants.length > 1) parts.push(`同行 ${store.participants.length} 人`)
  if (store.spentCents) parts.push(`已记 ${formatMoney(store.spentCents)}`)
  return parts.join(' · ')
})

// -- 指针回声：只写两个 CSS 变量，光带是纯 CSS 的合成层，不在 JS 里改样式。 ----------
// 开关只有 useReduceMotion 这一个出口：全局那条 CSS 媒体查询掐不掉 `--sy` 驱动的位移，
// 而设置面板的「减少动效」也不会写进媒体查询。

const reduceMotion = useReduceMotion()

/** 视差的位移量。减少动效时直接给 0——CSS 那一侧的表达式不用知道自己被关了。 */
const sy = computed(() => (reduceMotion.value ? 0 : props.scrollY))
/** hover 推进的量。:hover 是状态不是动画，全局那条兜底掐不掉它，只能从这儿断源。 */
const zoom = computed(() => (reduceMotion.value ? 1 : 1.03))

const stageEl = ref<HTMLElement | null>(null)

function onPointerMove(e: PointerEvent) {
  if (reduceMotion.value) return
  const el = stageEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  el.style.setProperty('--mx', `${((e.clientX - r.left) / r.width) * 100}%`)
  el.style.setProperty('--my', `${((e.clientY - r.top) / r.height) * 100}%`)
}

/** 指针离开后光带要退回中间，否则它停在最后一次采样点上，看着像一道没关的灯。 */
function onPointerLeave() {
  stageEl.value?.style.removeProperty('--mx')
  stageEl.value?.style.removeProperty('--my')
}

/** 偏好是中途能改的：关掉时光带要跟着关，不能留在最后一次采样的位置上。 */
watch(reduceMotion, (off) => {
  if (off) onPointerLeave()
})

/* -- 行遮罩揭幕（lab D1）：行程名从一条线后面升上来，而不是整块淡入。 ------------- */
// 遮罩由模板里那两层 span 承担（外层裁、内层跑 transform），GSAP 只碰内层那一枚。
// 收尾必须 clearProps：标题栈里还有别的元素在量自己的位置，留一份 transform 在身上，
// 下一次重排就会拿它当基准（M15 那轮 TransitionGroup 留尾巴的同一种错）。
useMotion(rootEl, ({ q, gsap, reduce }) => {
  gsap.fromTo(
    q('.mast__masktext'),
    { yPercent: 105, autoAlpha: 0 },
    {
      yPercent: 0,
      autoAlpha: 1,
      duration: reduce ? 0.001 : MOTION.reveal,
      ease: reduce ? 'none' : MOTION.easeOut,
      delay: reduce ? 0 : 0.1,
      clearProps: 'transform,opacity,visibility',
    },
  )
})

onBeforeUnmount(() => {
  stageEl.value = null
})
</script>

<template>
  <section
    ref="rootEl"
    class="mast"
    :class="{ 'mast--photo': hasPhoto, 'mast--nophoto': !hasPhoto, 'mast--min': collapsed, 'mast--gl': glLive }"
    :data-cover-source="coverSource"
    :style="{ '--sy': sy, '--zoom': zoom }"
    aria-label="行程概要"
  >
    <div
      ref="stageEl"
      class="mast__stage"
      @pointermove="onPointerMove"
      @pointerleave="onPointerLeave"
    >
      <div v-if="hasPhoto" class="mast__ph">
        <img
          class="mast__img"
          :src="cover"
          alt=""
          decoding="async"
          referrerpolicy="no-referrer"
          @error="onImgError"
        />
        <!-- 减少动效时这一档根本不挂载：CSS 那条全局兜底掐不掉 shader 自己的 rAF。 -->
        <MastCoverGl
          v-if="!reduceMotion"
          :src="cover"
          @live="glLive = true"
          @dead="glLive = false"
        />
      </div>
      <div v-else class="mast__art" aria-hidden="true">
        <RouteArt />
      </div>

      <div v-if="hasPhoto && !reduceMotion" class="mast__glow" aria-hidden="true"></div>

      <!-- 标题栈：压在照片下半部。城市与倒计时在上、名字居中、天数与统计在下，
           整摞离照片下沿 12px——首页英雄卡同一个读法。 -->
      <div class="mast__stack">
        <div v-if="city || countdown.label" class="mast__srow">
          <span v-if="city" class="mast__city">{{ city }}</span>
          <span v-if="countdown.label" class="mast__pill" :class="`mast__pill--${countdown.tone}`">
            {{ countdown.label }}
          </span>
        </div>
        <h1 class="mast__title">
          <button
            class="mast__name tap-pad"
            type="button"
            :title="title ? '点击重命名行程' : '点击设置行程名'"
            @click="emit('rename')"
          >
            <!-- 两层：外层裁、内层被 GSAP 抬起来。文本本身仍是一行带省略号。 -->
            <span class="mast__maskline"><span class="mast__masktext">{{ title || '未命名行程' }}</span></span>
          </button>
        </h1>
        <p v-if="metaLine" class="mast__meta">{{ metaLine }}</p>
        <p v-if="stats" class="mast__meta">{{ stats }}</p>
      </div>

      <!-- 封面胶囊（右上角）：默认档说「默认」，换封面这一枚两档都在——键永远就地给，
           它不再依赖票券那一段说明。 -->
      <div class="mast__picks">
        <span v-if="usingDefault" class="mast__cchip">默认</span>
        <button class="mast__cchip mast__cchip--act tap-pad" type="button" @click="emit('pickCover')">
          <ImagePlus class="ic" :size="13" /> 换封面
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 一整个圆角卡，照片铺满、字压在下半部——首页那张封面卡就是这么长的，两页说同一种话。
   照片的下沿由此变成「卡自己的圆角」，而不是横切页面的一条硬边（「像贴进来」的另一半在这）。
   总高是这里唯一随收合变的一格（连 margin 一起收，不然折完还剩 10px 的空带）。
   不用 max-height：那要写成一个大常量，动画全程大部分时间在走空档，看着像迟了一拍
   （同 DaySection 的 fold 那条口径）。clip 而不是 hidden——不许凭空造出滚动容器。 */
.mast {
  --mast-h: clamp(214px, 30vh, 258px);
  position: relative;
  z-index: 1;
  isolation: isolate;
  overflow: clip;
  height: var(--mast-h);
  margin: 10px 12px 0;
  /* F 把封面卡收进同一套「白边勾轮廓」：边框换 --glass-border。首页那张大封面按 mock
     用 6px 半透明白框，这一张（mock 的 .mast 走 --card-bd）是 1px——照片那一圈细白
     就是 F 里「照片被摆在桌上」的那条边。 */
  background: var(--surface);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  transition:
    height var(--dur-slow) var(--ease-inout),
    margin-top var(--dur-slow) var(--ease-inout),
    border-width var(--dur-slow) var(--ease-inout),
    visibility 0s linear var(--dur-slow);
}

/* 收起之后内容只是被裁掉了，还留在焦点链和朗读顺序里；等动画走完再摘掉。 */
.mast--min {
  visibility: hidden;
  height: 0;
  margin-top: 0;
  border-width: 0;
}

.mast:not(.mast--min) {
  visibility: visible;
  transition:
    height var(--dur-slow) var(--ease-inout),
    margin-top var(--dur-slow) var(--ease-inout),
    border-width var(--dur-slow) var(--ease-inout),
    visibility 0s;
}

.mast__stage {
  position: absolute;
  inset: 0;
  z-index: 0;
  isolation: isolate;
  /* clip 不是可选的：照片层为了有可动的范围刻意比这一格高（负 inset），不裁的话它会
     顶出卡外去。用 clip 不用 hidden——不许凭空造滚动容器。 */
  overflow: clip;
  background: var(--stage-bg);
}

/* -- 有照片 / 没照片两档只在这一层切换 ------------------------------------------ */
.mast {
  /* 没有封面时这一格是「地图底」不是「假照片」：淡彩底 + 墨色字。
     取 ramp-1（水系蓝）——封面说的是这趟旅行，不是某一天；深色态这一档自己
     落回夜航蓝，所以不必再写一套。 */
  --stage-bg: linear-gradient(
    150deg,
    color-mix(in oklab, var(--ramp-1-soft) 72%, var(--surface)) 0%,
    var(--surface) 60%,
    color-mix(in oklab, var(--ramp-1-soft) 48%, var(--surface)) 100%
  );
}

.mast--photo {
  --stage-bg: var(--photo-scrim);
}

/* 没有封面就不占海报的高度：一条 258px 高的空带上只画了一根虚线，读出来是「图没刷出来」，
   不是「这是一条装饰带」。首页 hero 的 nophoto 分支同一条口径（宁矮不装）。 */
.mast--nophoto {
  --mast-h: clamp(168px, 22vh, 196px);
}

/* 淡彩底上的插画是水印、不是底衬：.7 那档的路线点会横穿标题与元信息（兜底档实测
   2.34:1，字读不出来），压到 .3 之后连两点交叠处也在 4.5:1 以上。判据在
   docs/_contrast-mast.mjs 的「坏封面兜底」那一趟。 */
.mast--nophoto .mast__art {
  color: var(--ramp-1-deep);
  opacity: 0.3;
}

.mast__ph {
  position: absolute;
  /* 位移只会往下走（列表滚走时照片落得更慢），所以上方余量比下方多。
     min() 封顶是硬要求：门限之上还有多少滚动量是不可知的，不设上限就总有一天露边。 */
  --drift: min(var(--sy, 0) * 0.34px, 40px);
  inset: -26% 0 -14%;
  z-index: 0;
  will-change: transform;
  transform: translate3d(0, var(--drift), 0);
  /* 视差跟的是滚轮，用 --dur 那档：慢到 --dur-slow 就变成「照片迟到了」，
     而 hover 推进也读这一条，两段共用一个时长才不会互相抢。 */
  transition: transform var(--dur) var(--ease);
  animation: mast-settle var(--dur-photo) var(--ease-out) backwards;
}

.mast--photo:hover .mast__ph {
  transform: translate3d(0, var(--drift), 0) scale(var(--zoom, 1));
}

.mast__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* 封面多是竖构图里裁出来的一刀，视线落在照片的下三分之二，所以基准点压下去一些。 */
  object-position: 50% 60%;
  /* 静止的照片最容易读出「贴图」，一个来回 22s 的缩放漂移慢到不会被注意，但画面活了。 */
  animation: ken-burns var(--dur-drift) var(--ease-inout) infinite alternate;
  transform-origin: 58% 44%;
}

/* WebGL 档接管照片时，这条呼吸要停下：同一条缩放两边各跑一次，读起来是照片在抽搐。
   而 `.mast__ph` 那句 `mast-settle` 不许跟着停——它带着画布一起落，照片的入场收拢只有它一份。 */
.mast--gl .mast__img {
  animation: none;
}

/* 压暗层：白字压任意照片的对比度由它保底。配方是 --scrim-hero（与首页英雄卡同一个值，
   整张压暗、底部到 .82），实测账在 docs/_contrast-mast.mjs——六张内置封面张张量过最亮
   像素，白字大字档都过 3:1。没有照片那一路不挂这层：淡彩底上写的是墨字。 */
.mast--photo .mast__stage::after {
  position: absolute;
  inset: 0;
  z-index: 1;
  content: '';
  background: var(--scrim-hero);
}

/* 指针回声：一道跟着光标走的柔光带，压在压暗层之上——它要提亮的是最终上屏的那张合成图，
   不是原图。没有它，鼠标一动这屏就是死的（同登录页那一条口径）。 */
.mast__glow {
  position: absolute;
  inset: 0;
  z-index: 2;
  background: radial-gradient(
    220px circle at var(--mx, 50%) var(--my, 50%),
    rgba(255, 255, 255, 0.22),
    transparent 68%
  );
  mix-blend-mode: soft-light;
  pointer-events: none;
}

.mast__art {
  position: absolute;
  inset: 0;
  z-index: 0;
  color: var(--ramp-1);
  opacity: 0.42;
}

/* -- 标题栈：城市胶囊 + 倒计时一行、名字、两行元信息，压在照片下半部 ------------- */
.mast__stack {
  position: absolute;
  z-index: 3;
  right: 14px;
  bottom: 12px;
  left: 14px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  /* 与票券那一版同一段入场：晚三拍升起，前两拍留给照片沉下与胶囊淡入。 */
  animation: rise-in var(--dur-entrance) var(--ease-out) calc(var(--stagger) * 3) backwards;
}

.mast__srow {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* 城市胶囊＝柠黄一块（F 的 .chip.city，与首页那颗同源）：压在照片上，所以两档主题
   都不反色。 */
.mast__city {
  padding: 3px 10px;
  color: var(--city-chip-ink);
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  line-height: 1.4;
  background: var(--city-chip);
  border-radius: var(--radius-pill);
}

/* 倒计时推到行尾（同首页：城市在左、状态在右）。 */
.mast__srow .mast__pill {
  margin-left: auto;
}

.mast__title {
  min-width: 0;
  margin: 0;
}

.mast__name {
  display: block;
  max-width: 100%;
  padding: 0;
  color: #fff;
  font-family: var(--font-display);
  font-size: calc(clamp(26px, 2.7vw, 34px) * var(--fs-scale));
  font-weight: 800;
  line-height: 1.12;
  letter-spacing: -0.02em;
  text-align: left;
  /* 大字是这一屏的主角，遮罩是比例渐变、照片裁切后暗部落在哪没法保证，
     所以再补一道软投影兜底（同首页大标题）。 */
  text-shadow: 0 2px 16px rgba(11, 22, 26, 0.4);
  background: none;
  border: 0;
  cursor: pointer;
}

/* 行遮罩：外层裁、内层被抬起来（GSAP 只碰内层那枚）。省略号从按钮搬到内层——
   块级子元素不吃父级的 text-overflow。下缘 0.14em 是给拉丁字尾留的呼吸：展示字的
   line-height 是 1.12，直接 clip 会把 g/j 的下半截切掉；外层用等量负 margin 还回去，
   按钮的盒高与加遮罩之前一致，栈的几何不用重算。 */
.mast__maskline {
  display: block;
  min-width: 0;
  margin-bottom: -0.14em;
  overflow: hidden;
}

.mast__masktext {
  display: block;
  padding-bottom: 0.14em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mast__name:hover {
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 4px;
}

/* 元信息两行：天数/日期、地点/同行/花费。小字压照片同样走一道软投影兜底。 */
.mast__meta {
  min-width: 0;
  overflow: hidden;
  margin: 0;
  color: rgba(255, 255, 255, 0.87);
  font-size: calc(12.5px * var(--fs-scale));
  font-variant-numeric: tabular-nums;
  line-height: 1.4;
  text-overflow: ellipsis;
  text-shadow: 0 1px 2px rgba(11, 22, 26, 0.4);
  white-space: nowrap;
}

/* 出发胶囊：深色实心 + 白字，走 --photo-* 那组「不随主题反转」的令牌——
   规矩与首页那颗同源（见 main.css 顶部那条硬规矩）。 */
.mast__pill {
  flex: 0 0 auto;
  padding: 3px 10px;
  color: #fff;
  font-family: system-ui, sans-serif;
  font-size: var(--t-micro);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
  line-height: 1.4;
  background: var(--photo-scrim);
  border-radius: var(--radius-pill);
}
.mast__pill--soon {
  background: var(--photo-soon);
}
.mast__pill--live {
  background: var(--photo-live);
}
.mast__pill--past {
  background: var(--photo-past);
}

/* -- 封面胶囊（右上角）：默认档那枚是「说状态」，换封面那枚是「给键」，两枚都在 ----
   压在照片最亮的那一角上，所以都带自己的实底：默认档用 55% 黑 + 模糊（底下的照片
   还透一点），换封面用 --photo-scrim 那档实心——它是键，要读得最清。 */
.mast__picks {
  position: absolute;
  z-index: 4;
  top: 10px;
  right: 12px;
  display: flex;
  gap: 6px;
  animation: fade-in var(--dur-entrance) var(--ease-out) calc(var(--stagger) * 2) backwards;
}

.mast__cchip {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  color: rgba(255, 255, 255, 0.94);
  font-size: calc(11px * var(--fs-scale));
  font-weight: 600;
  line-height: 1;
  background: rgba(24, 28, 30, 0.55);
  border: 0;
  border-radius: var(--radius-pill);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}

.mast__cchip--act {
  background: var(--photo-scrim);
  cursor: pointer;
}

.mast__cchip--act:hover {
  filter: brightness(1.18);
}

/* -- 没有照片那一路：墨字压淡彩底，胶囊跟着换软底 -------------------------------- */
.mast--nophoto .mast__name {
  color: var(--ink);
  text-shadow: none;
}

.mast--nophoto .mast__meta {
  /* 元信息从 --text-2 提到 --text：这一档的底是「淡彩 + 插画水印」，点迹上那一处
     --text-2 两档主题都压不住。照片档的小字靠 --scrim-hero 保底，这一档靠自己调深。 */
  color: var(--text);
  text-shadow: none;
}

/* 倒计时胶囊：照片那套是「实心深底 + 白字」，淡彩底上要换成软底 + 深色字，
   否则一块灰贴在浅蓝上，读起来像贴错了卡（同首页 nophoto 那一档）。 */
.mast--nophoto .mast__pill {
  color: var(--text);
  background: var(--surface-3);
}
.mast--nophoto .mast__pill--soon {
  color: var(--warn);
  background: var(--warn-soft);
}
.mast--nophoto .mast__pill--live {
  color: var(--ok);
  background: var(--ok-soft);
}
.mast--nophoto .mast__pill--past {
  color: var(--text-2);
  background: var(--surface-3);
}

.mast--nophoto .mast__cchip {
  color: var(--text-2);
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid var(--glass-border);
}
.mast--nophoto .mast__cchip--act {
  color: var(--text);
}

@media (max-width: 860px) {
  /* 窄屏整页只有这一块是深色照片，再给它卡边、圆角与侧边距，读起来就是一页纸上浮着
     另一屏（用户截图原话「感觉像是两个界面」）。面板在窄屏本来就是通铺
     （main.css ≤860 那一档把 padding/圆角/白线全清零），照片跟着通铺：从顶栏下沿铺到
     两侧边，浅色内容直接接在照片下沿。桌面的「一张卡」读法不变。 */
  .mast {
    --mast-h: clamp(184px, 26vh, 214px);
    margin: 0;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }
  /* 写在 .mast 之后：同一元素上的两个同名令牌，后出现的才算数。 */
  .mast--nophoto {
    --mast-h: clamp(150px, 20vh, 172px);
  }
  /* 窄屏的展示字比宽屏收一档（真实 430px 上量到 26px 是这一档的上限）。 */
  .mast__name {
    font-size: calc(clamp(22px, 6vw, 26px) * var(--fs-scale));
  }
}
</style>

<style>
/* 一次性入场：`mast-settle` 是照片从 1.12 落回 1.0。静止态不写 scale，
   动画只负责从初态走过去——反过来（静止态不写、靠 fill-mode 停在末帧）会在
   结束后把这一格留在 1.12 上。 */
@keyframes mast-settle {
  from {
    transform: scale(1.12);
  }
}
</style>
