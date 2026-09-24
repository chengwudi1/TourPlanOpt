<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import RouteArt from '@/components/RouteArt.vue'
import { ImagePlus } from '@/components/icons'
import { useReduceMotion } from '@/composables/useReduceMotion'
import { useTripStore } from '@/stores/trip'
import type { CoverSource } from '@/types/domain'
import { formatMoney } from '@/utils/money'
import { countdownOf, formatDateRange } from '@/utils/tripstatus'

/**
 * D1 海报头（V3 骨架 + V2 动效）：一枚圆角卡，照片铺到底、票券浮在下半部。
 *
 * 为什么字要搬到票券上：照片内容不可控，白字压在亮天空上随时掉到 2:1，而这块是整页
 * 唯一必须读清的信息。落在 `--surface` 上之后，对比度与照片无关。
 * 为什么整块要做成卡：照片的下沿由卡的圆角裁掉，于是那条边既不是硬切（M32 第一版）
 * 也不是淡出，而是这张卡自己的轮廓——和首页那张封面卡是同一种东西。
 *
 * 「不像截图」靠的是三件动效，不是靠边怎么切：照片 22s Ken Burns 呼吸 + hover 推进、
 * 滚动视差（照片比列表慢 0.34 倍、封顶 40px）、指针在照片上留一道柔光带。入场是三段——
 * 照片从 1.12 落回 1.0、票券晚三拍升起、kicker 胶囊最后淡入。
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

const cover = computed(() => store.coverPhoto)
/** 坏图不是「没有图」这一档的装饰——它是同一个兜底形态，所以走同一条分支，
 *  而不是留下一个 1×1 的裂图标压在字下面。记的是**哪一张**坏过：`<img>` 在 `v-if` 里，
 *  一旦判坏就连根拔起，光靠新图的 `load` 清不掉这个标记（那时候已经没有元素在加载了）。
 *  按 URL 记，换一张就自动重新挂载重试——一次网络抖动不该把海报永久打死。 */
const brokenUrl = ref('')
const hasPhoto = computed(() => !!cover.value && brokenUrl.value !== cover.value)
/** 出的是两档链里的哪一档。屏幕上只有一处读它（下面那枚「换封面」键），复验时两条判据都要读它。 */
const coverSource = computed<CoverSource>(() => store.coverSource)
/** 还站在默认那张上：链头空着，或者内置资产读不出来（同一档的兜底形态，键照样要给）。 */
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

/** 三段里任何一段没填，整段连同它前面那个分隔符一起消失：「· 10月1日」这种开头
 *  的空段比没有这一段更难看。 */
const kicker = computed(() => {
  const parts = [store.trip?.city ?? '', store.days.length ? `${store.days.length} 天` : '', dateRange.value]
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

onBeforeUnmount(() => {
  stageEl.value = null
})
</script>

<template>
  <section
    ref="rootEl"
    class="mast"
    :class="{ 'mast--photo': hasPhoto, 'mast--nophoto': !hasPhoto, 'mast--min': collapsed }"
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
      </div>
      <div v-else class="mast__art" aria-hidden="true">
        <RouteArt />
      </div>

      <div v-if="hasPhoto && !reduceMotion" class="mast__glow" aria-hidden="true"></div>
      <p v-if="kicker" class="mast__kick">{{ kicker }}</p>
    </div>

    <!-- 票券：正文唯一的落脚处，浮在照片上，两侧与底部各留 12px 的原图。 -->
    <div class="mast__ticket">
      <h1 class="mast__title">
        <button
          class="mast__name tap-pad"
          type="button"
          :title="title ? '点击重命名行程' : '点击设置行程名'"
          @click="emit('rename')"
        >
          {{ title || '未命名行程' }}
        </button>
        <span v-if="countdown.label" class="mast__pill" :class="`mast__pill--${countdown.tone}`">
          {{ countdown.label }}
        </span>
      </h1>
      <div v-if="stats" class="mast__rule" aria-hidden="true"></div>
      <p v-if="stats" class="mast__stats">{{ stats }}</p>
      <!-- 默认档就地给键（决策 8）：内置海报保证海报头永远有图，所以这一段不再是救火，
           而是说清「此刻是系统挑的那张」并就地给换的入口。全部落在票券里——票券长高会自己
           往下顶，`.mast` 的总高与 `mastOccupied()` 不变，M34/M35 的收合几何才不用重开。 -->
      <div v-if="usingDefault" class="mast__need">
        <span class="mast__need-text tiny muted">当前使用的是默认封面</span>
        <span class="mast__need-acts">
          <button class="btn btn--sm btn--primary" type="button" @click="emit('pickCover')">
            <ImagePlus class="ic" :size="13" /> 换封面
          </button>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 一整个圆角卡，照片铺满、票券浮在下沿——首页那张封面卡就是这么长的，两页说同一种话。
   照片的下沿由此变成「卡自己的圆角」，而不是横切页面的一条硬边（「像贴进来」的另一半在这）。
   总高是这里唯一随收合变的一格（连 margin 一起收，不然折完还剩 10px 的空带）。
   不用 max-height：那玩意要写成一个大常量，动画全程大部分时间在走空档，看着像迟了一拍
   （同 DaySection 的 fold 那条口径）。clip 而不是 hidden——不许凭空造出滚动容器。 */
.mast {
  --mast-h: clamp(214px, 30vh, 258px);
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  isolation: isolate;
  overflow: clip;
  height: var(--mast-h);
  margin: 10px 12px 0;
  background: var(--surface);
  border: 1px solid var(--border);
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
  /* 没有封面时这一格是「地图底」不是「假照片」：淡彩底 + 墨色 kicker 胶囊。
     取 ramp-1（水系蓝）——封面说的是这趟旅行，不是某一天；深色态这一档自己
     落回夜航蓝，所以不必再写一套。 */
  --stage-bg: linear-gradient(
    150deg,
    color-mix(in oklab, var(--ramp-1-soft) 72%, var(--surface)) 0%,
    var(--surface) 60%,
    color-mix(in oklab, var(--ramp-1-soft) 48%, var(--surface)) 100%
  );
  --kick-ink: var(--ramp-1-deep);
  --kick-bg: color-mix(in srgb, var(--surface) 76%, transparent);
}

.mast--photo {
  --stage-bg: var(--photo-scrim);
  --kick-ink: rgba(255, 255, 255, 0.92);
  --kick-bg: var(--photo-scrim);
}

/* 没有封面就不占海报的高度：一条 258px 高的空带上只画了一根虚线，读出来是「图没刷出来」，
   不是「这是一条装饰带」。首页 hero 的 nophoto 分支同一条口径（宁矮不装）。 */
.mast--nophoto {
  --mast-h: clamp(168px, 22vh, 196px);
}

.mast--nophoto .mast__art {
  color: var(--ramp-1-deep);
  opacity: 0.7;
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
  transition: transform var(--dur) var(--ease-out);
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

/* 压暗层：这里原本担着「白字压在任意照片上也要 3:1」的活，那活已经搬走了——大字在
   票券上，kicker 胶囊自带实心底。剩下的全是审美职责：把一张来路不明的照片收进页面，
   别让它比正文还响。所以只留一道上轻下重的暗角；再压就看不见照片了（深色态一度整块
   读成一条黑栏，就是这个原因）。 */
.mast--photo .mast__stage::after {
  position: absolute;
  inset: 0;
  z-index: 1;
  content: '';
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--photo-scrim) 42%, transparent) 0%,
    color-mix(in srgb, var(--photo-scrim) 22%, transparent) 40%,
    color-mix(in srgb, var(--photo-scrim) 40%, transparent) 72%,
    color-mix(in srgb, var(--photo-scrim) 62%, transparent) 100%
  );
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

.mast__kick {
  position: absolute;
  z-index: 3;
  top: 12px;
  left: 14px;
  max-width: calc(100% - 28px);
  overflow: hidden;
  padding: 3px 10px;
  margin: 0;
  color: var(--kick-ink);
  background: var(--kick-bg);
  font-family: var(--mono);
  font-size: var(--t-micro);
  font-weight: 600;
  letter-spacing: var(--ls-caps);
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
  border-radius: var(--radius-pill);
  animation: fade-in var(--dur-entrance) var(--ease-out) calc(var(--stagger) * 2) backwards;
}

/* -- 票券：正文的落脚处，浮在照片下沿，两侧与底部各留 12px ------------------------ */
.mast__ticket {
  position: relative;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  align-items: center;
  min-width: 0;
  margin: 0 12px 12px;
  padding: 11px 15px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  animation: rise-in var(--dur-entrance) var(--ease-out) calc(var(--stagger) * 3) backwards;
}

.mast__title {
  display: flex;
  flex: 1 1 auto;
  gap: 9px;
  align-items: baseline;
  min-width: 0;
  margin: 0;
  font-weight: 700;
  line-height: 1.14;
}

.mast__name {
  min-width: 0;
  padding: 0;
  overflow: hidden;
  color: inherit;
  font-family: var(--font-display);
  font-size: calc(clamp(22px, 3.2vw, 30px) * var(--fs-scale));
  letter-spacing: var(--ls-display);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: none;
  border: 0;
  cursor: pointer;
}

.mast__name:hover {
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 4px;
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

/* 虚线分隔：首页数据带同一条语汇，这里竖着切一刀，把票券分成「名字」和「这趟有什么」两格。 */
.mast__rule {
  flex: 0 0 auto;
  align-self: stretch;
  margin: 1px 0;
  border-left: 1px dashed var(--hairline);
}

.mast__stats {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  margin: 0;
  color: var(--text-2);
  font-size: calc(13px * var(--fs-scale));
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  text-align: right;
  text-overflow: ellipsis;
}

/* 空态那一行独占一票：说明在左、两枚键在右，中间那道虚线与上面的分隔线是同一种语汇。
   `flex-basis: 100%` 是「换行」的意思，只在宽屏的横向票券里成立，窄屏那段自己改回去。 */
.mast__need {
  display: flex;
  flex: 1 0 100%;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  padding-top: 9px;
  border-top: 1px dashed var(--hairline);
}

.mast__need-text {
  min-width: 0;
  line-height: 1.5;
}

.mast__need-acts {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
}

@media (max-width: 860px) {
  .mast {
    --mast-h: clamp(184px, 26vh, 214px);
    margin-right: 10px;
    margin-left: 10px;
  }
  /* 写在 .mast 之后：同一元素上的两个同名令牌，后出现的才算数。 */
  .mast--nophoto {
    --mast-h: clamp(150px, 20vh, 172px);
  }
  .mast__name {
    font-size: calc(clamp(19px, 5.2vw, 24px) * var(--fs-scale));
  }
  /* 窄屏塞不下两格：名字与数字上下排，分隔线跟着躺平（首页数据带在同一档换的向）。 */
  .mast__ticket {
    flex-direction: column;
    gap: 0;
    align-items: flex-start;
    padding: 10px 12px 11px;
  }
  .mast__title {
    flex: 0 0 auto;
  }
  .mast__rule {
    width: auto;
    margin: 9px 0 7px;
    border-top: 1px dashed var(--hairline);
    border-left: 0;
  }
  .mast__stats {
    align-self: stretch;
    text-align: left;
  }
  /* 纵向票券里「换行」这层意思没了：basis 100% 撑的是高度，这一档按内容占高、占满整行宽。 */
  .mast__need {
    flex: 0 0 auto;
    width: 100%;
    margin-top: 9px;
    padding-top: 10px;
  }
  .mast__kick {
    top: 10px;
    left: 12px;
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
