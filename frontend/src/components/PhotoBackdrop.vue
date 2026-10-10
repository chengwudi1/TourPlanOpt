<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useReduceMotion } from '@/composables/useReduceMotion'

/**
 * 全屏照片背景：定案版本是背景对比页里的 P1「满屏缓推」（docs/F-BG3-2026.html），
 * 这里逐参数照搬那道 mock 的 CSS，不是重新调过的另一版：
 *
 * - 六张照片各自一条 144s 的线性时间表，用 animation-delay 逐张错开 24s；
 *   可 window 是 0%~19.2%，所以同一时刻最多两张可见、重叠约 3.6s，循环无缝。
 * - 缓推（kb）：可见的 24s 里从 1.03 推到 1.105、位移不到画面的 1.2%；
 *   复位段落在 19.2% 之后——照片已经全透明，跳变看不见。
 * - 缓推与淡化都是 transform/opacity，交给合成器，主线程不参与（这条是硬要求：
 *   背后还有地图、WebSocket 和拖拽在跑）。
 *
 * 三件在 mock 里不用管、进了应用必须管的事：
 *
 * 1. **减动效兜底**。main.css 的全局规则在 [data-motion='off'] 与系统 reduce 下把
 *    animation 一并掐死——掐死之后六层全是 opacity:0（初值），屏幕上只剩纸色。
 *    所以这里给 :first-child 一个静态态：只显示第一张，无缓推。判定不写媒体查询、
 *    只认 data-motion 那个已解析值（同 settingsStore 的口径，首绘前由 index.html 写）。
 * 2. **懒加载**。t=0 时真正可见的是第 1、2 张（第 2 张正处在淡出段），这两张立即取；
 *    第 3~6 张要 48s 之后才轮到，空闲时再取，别跟首屏抢带宽。1.68MB 的总量里
 *    首屏只需要前 550KB。
 * 3. **指针回声**。JS 只写 --mx/--my 两个 -1..1 的数，位移是 CSS 表达式做的；
 *    和 MessagePanel、海报头同一套约定，reduceMotion 为真时直接返回（不写也不清之外，
 *    那两处的 watch 清理这里也有）。
 *
 * 素材授权：六张全部来自 Unsplash（免费商用），原始 id 依次是
 * photo-1418065460487 / 1506744038136 / 1475924156734 / 1506905925346 /
 * 1483347756197 / 1502082553048。第 4 张刻意不是 mock 里的丹霞——那张出自 Bing
 * 每日壁纸，只许个人使用，出海前换成了 Unsplash 的雪岭。文件在 public/bg/ 下，
 * 引用走 BASE_URL 前缀（开发期 /tourplanopt/ 前缀下同样取得到）。
 */

const PHOTOS = [
  'bg-mist.jpg',
  'bg-valley.jpg',
  'bg-dusk.jpg',
  'bg-ridge.jpg',
  'bg-aurora.jpg',
  'bg-tree.jpg',
].map((name) => `${import.meta.env.BASE_URL}bg/${name}`)

/** 前两张是首屏那几秒里真正会露面的（见头部注释），随组件一起挂上。 */
const EAGER = 2
const loaded = ref(PHOTOS.map((_, i) => i < EAGER))

const rootRef = ref<HTMLElement | null>(null)
const reduce = useReduceMotion()
let filled = false

function fillRest(): void {
  if (filled) return
  filled = true
  PHOTOS.slice(EAGER).forEach((src, offset) => {
    const img = new Image()
    img.decoding = 'async'
    img.onload = () => {
      loaded.value[EAGER + offset] = true
    }
    img.src = src
  })
}

onMounted(() => {
  // 监听挂不挂与当下是否省动效无关：偏好中途能改，处理器自己会挡（同 MessagePanel）。
  window.addEventListener('pointermove', onPointerMove, { passive: true })
  document.documentElement.addEventListener('pointerleave', clearEcho)
  // 省动效时只有第一张会上屏（下面的静态兜底），其余五张不必取。
  if (reduce.value) return
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(fillRest, { timeout: 3000 })
  } else {
    setTimeout(fillRest, 1500)
  }
})

/** 偏好是中途能改的：先在省动效模式下开着，之后把动效打开的人，第 3~6 张此刻才去取。 */
watch(reduce, (off) => {
  if (!off && !filled) fillRest()
  if (off) clearEcho()
})

function onPointerMove(e: PointerEvent): void {
  if (reduce.value) return
  const el = rootRef.value
  if (!el) return
  el.style.setProperty('--mx', ((e.clientX / window.innerWidth) * 2 - 1).toFixed(4))
  el.style.setProperty('--my', ((e.clientY / window.innerHeight) * 2 - 1).toFixed(4))
}

function clearEcho(): void {
  const el = rootRef.value
  el?.style.removeProperty('--mx')
  el?.style.removeProperty('--my')
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onPointerMove)
  document.documentElement.removeEventListener('pointerleave', clearEcho)
  clearEcho()
})
</script>

<template>
  <!-- 纯装饰：不参与命中（pointer-events:none），也不进可访问性树。负 z-index 压在内容之下、
       画布底色之上——画布永远最先画，之后才是根元素的负层级子节点（同旧环境层的口径）。 -->
  <div ref="rootRef" class="pback" aria-hidden="true">
    <div
      v-for="(src, i) in PHOTOS"
      :key="src"
      class="pback__layer"
      :style="{
        backgroundImage: loaded[i] ? `url('${src}')` : undefined,
        animationDelay: `${-24 * i}s`,
      }"
    />
    <div class="pback__veil" />
  </div>
</template>

<style scoped>
.pback {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
}

/* inset -3.5%：缓推带到 1.105 倍、指针还有 ±10px 位移，这一圈余量保证任何一帧都不露边。
   六层用同一条动画名与同一条 delay，错峰只靠内联的 animation-delay；两条动画共用
   delay 一个是 CSS 列表按位循环匹配的规则，不是巧合。 */
.pback__layer {
  position: absolute;
  inset: -3.5%;
  background-position: center;
  background-size: cover;
  opacity: 0;
  animation:
    pback-cfp 144s linear infinite,
    pback-kb 144s linear infinite;
  translate: calc(var(--mx, 0) * 10px) calc(var(--my, 0) * 6px);
  transition: translate 700ms var(--ease-out);
  will-change: opacity, transform;
}

.pback__veil {
  position: absolute;
  inset: 0;
  /* 纱罩从 --bg 派生（同旧环境层：不带自己的硬编码色，换肤与深色态自动跟上）。
     上重下更重、中间透：文字几乎都落在面板上，露出来的照片只求「在」，不求抢。 */
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--bg) 36%, transparent),
    color-mix(in srgb, var(--bg) 6%, transparent) 30%,
    color-mix(in srgb, var(--bg) 8%, transparent) 66%,
    color-mix(in srgb, var(--bg) 50%, transparent)
  );
}

/* 深色态不能沿用同一组透明度：照片是亮的，按亮档那点白/暗纱在夜纸上会整屏发白。
   这里把同一件事（照片退成底纹）用 --bg 的暗色多盖一档。 */
:root[data-theme='dark'] .pback__veil {
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--bg) 58%, transparent),
    color-mix(in srgb, var(--bg) 30%, transparent) 30%,
    color-mix(in srgb, var(--bg) 26%, transparent) 66%,
    color-mix(in srgb, var(--bg) 66%, transparent)
  );
}

/* 省动效时动画被全局规则掐死（连 transform 一起），六层都会停在 opacity:0。
   这里只让第一张静态显示——判据用 data-motion 那个已解析值；媒体查询那条是
   「没 JS 也得保住」的同构兜底（先例见 main.css 那一段的写法）。 */
:root[data-motion='off'] .pback__layer:first-child {
  opacity: 1;
}
@media (prefers-reduced-motion: reduce) {
  .pback__layer:first-child {
    opacity: 1;
  }
}

/* 换图在 0%~19.2% 的窗口里完成，2.6% 淡入、16.6% 起淡出、19.2% 归零并一直归零到 100%；
   144s 环上相邻两张的窗口正好首尾相接（19.2% - 16.6% = 2.6% 即 3.7s 交叉）。 */
@keyframes pback-cfp {
  0% {
    opacity: 0;
  }
  2.6% {
    opacity: 1;
  }
  16.6% {
    opacity: 1;
  }
  19.2% {
    opacity: 0;
  }
  100% {
    opacity: 0;
  }
}

/* 缓推只走可见的那 19%：之后停在终点直到 100%，复位发生在不可见段里。 */
@keyframes pback-kb {
  0% {
    transform: scale(1.03) translate3d(0, 0, 0);
  }
  19% {
    transform: scale(1.105) translate3d(-1.1%, -0.7%, 0);
  }
  100% {
    transform: scale(1.105) translate3d(-1.1%, -0.7%, 0);
  }
}
</style>
