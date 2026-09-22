<script setup lang="ts">
/**
 * 自绘的「一条虚线路线 + 两个端点」——没有封面照片时垫底的那块图。
 *
 * 两处共用（首页 hero 的淡彩底、行程页海报头的 ramp 底），所以两条入场动画也搬进来了：
 * `route-draw` 走 clip-path 推进而不是 stroke-dashoffset（点状虚线用后者只会让点往前爬，
 * 不会「长出来」，末端还差半个线帽），`dot-pop` 要 `transform-box: fill-box` 才不把
 * 圆心当成用户坐标系原点、缩放时乱飞。这两条都是搬走就会忘的细节。
 *
 * 颜色刻意不写在这里：整块走 `currentColor`，由宿主决定压在哪层底上（首页是水系蓝的
 * 淡彩底，海报头是当天那一档 ramp）。动效的关闭交给 main.css 那条全局兜底。
 */
</script>

<template>
  <svg class="routeart" viewBox="0 0 400 200" preserveAspectRatio="none" aria-hidden="true">
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
</template>

<style scoped>
.routeart {
  display: block;
  width: 100%;
  height: 100%;
}

.routeart path {
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

.routeart circle {
  transform-box: fill-box;
  transform-origin: center;
  animation: dot-pop var(--dur-slow) var(--ease-pop) backwards;
}

.routeart circle:first-of-type {
  animation-delay: calc(var(--stagger) * 2);
}

.routeart circle:last-of-type {
  animation-delay: 1.62s;
}

@keyframes dot-pop {
  from {
    opacity: 0;
    transform: scale(0.2);
  }
}
</style>
