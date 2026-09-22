import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { useReduceMotion } from '@/composables/useReduceMotion'

/** ease-out cubic：起步快、收尾慢，读起来像数字自己落位，不是匀速滚过去。 */
function easeOut(k: number): number {
  return 1 - (1 - k) ** 3
}

/**
 * 数字滚动：把「地点 12 个」这类读数从上一次的值走到新值。
 *
 * 首帧从 0 起，所以挂载本身就有一次滚动；之后每次数值变化只走增量，不会回到 0 重播。
 * 挂起时按时间差推进（不是按帧数），所以切回前台的第一帧就直接落到终值。
 *
 * 但后台标签页根本不跑 rAF，那一趟滚动会永远停在起点上——屏上是一个彻头彻尾的错数字，
 * 而 CSS 动画停摆只是「不动」，性质完全不同。所以 document.hidden 与 reduced-motion
 * 同样走直达终值的路径。
 *
 * WAAPI 与 CSS 的那条全局兜底管不到这里，设置面板里那一档「减少动效」也只能由 JS 看见，
 * 所以两道判断都收在 composables/useReduceMotion 那一个出口上。
 */
export function useCountUp(source: () => number, durationMs = 760) {
  const reduceMotion = useReduceMotion()
  const shown = ref(0)
  let raf = 0
  let started = false

  function run(from: number, to: number) {
    if (reduceMotion.value || durationMs <= 0 || from === to || document.hidden) {
      shown.value = to
      return
    }
    cancelAnimationFrame(raf)
    const t0 = performance.now()
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / durationMs)
      shown.value = from + (to - from) * easeOut(k)
      if (k < 1) raf = requestAnimationFrame(step)
      else shown.value = to
    }
    raf = requestAnimationFrame(step)
  }

  watch(
    source,
    (to) => {
      run(started ? Math.round(shown.value) : 0, to)
      started = true
    },
    { immediate: true, flush: 'post' },
  )

  onBeforeUnmount(() => cancelAnimationFrame(raf))

  return computed(() => Math.round(shown.value))
}
