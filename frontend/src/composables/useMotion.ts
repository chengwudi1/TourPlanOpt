import { gsap } from 'gsap'
import { onBeforeUnmount, onMounted, type Ref } from 'vue'

import { useReduceMotion } from '@/composables/useReduceMotion'

/**
 * 组件作用域内跑 GSAP，卸载时整体 revert。
 *
 * 为什么包这一层：`gsap.context(fn, root)` 会把 fn 里创建的所有 tween 记账，
 * `ctx.revert()` 一次收干净——不用手写 kill 列表，也不会留下挂在已卸载节点上的计时器。
 * 项目里没有 `@gsap/vue3`：那个包的 setup 时机对不上这里的需求（根元素要先存在，
 * context 才有作用域可锚）。
 *
 * 两种时机都要支持，所以 API 是"建作用域 + 往后排"：
 * - `useMotion(rootRef, build)` 在挂载那一刻跑一次 build，管首屏揭幕；
 * - `run(build)` 给"数据晚于挂载才到"的场景用（行程快照是 WS 推来的，挂载时列表是空的）。
 *   挂载前调用会排队，挂载后随作用域一起跑，不会漏、也不会跑在 root 不存在的时候。
 *
 * `q` 只在 root 里找元素：跨组件抓到别人的节点，revert 就会替别人擦状态。
 *
 * 三条规矩写在这里而不是散在各组件的注释里：
 * 1. **`reduce` 为真时时长与 stagger 一律归零**，不是"改短"。留着 0.01s 的错峰，
 *    对需要减少动效的人而言仍然是逐条闪现。
 * 2. **收尾要 `clearProps`**。位移留在 transform 上，sortablejs 量拖拽起点、
 *    吸顶行量 sticky 边界都会跟着错——这条是 M15 那轮 TransitionGroup 事故的原样。
 * 3. **只碰 opacity 与 transform**。改布局属性的 tween 会和 v-show、grid 0fr↔1fr
 *    那一类既有机制抢同一格几何。
 */
export function useMotion(
  rootRef: Ref<HTMLElement | null>,
  build?: (api: MotionApi) => void,
) {
  const reduce = useReduceMotion()
  let ctx: gsap.Context | null = null
  let root: HTMLElement | null = null
  const queue: Array<(api: MotionApi) => void> = []

  const api = (): MotionApi => ({
    q: (selector: string) => Array.from((root ?? document).querySelectorAll(selector)),
    reduce: reduce.value,
    gsap,
  })

  function run(fn: (api: MotionApi) => void) {
    if (!ctx) {
      queue.push(fn)
      return
    }
    ctx.add(() => fn(api()))
  }

  onMounted(() => {
    root = rootRef.value
    if (!root) return
    ctx = gsap.context(() => {
      if (build) build(api())
      for (const fn of queue.splice(0)) fn(api())
    }, root)
  })

  onBeforeUnmount(() => {
    ctx?.revert()
    ctx = null
  })

  return { run, reduce }
}

export interface MotionApi {
  /** 只在本组件的 root 里找元素。 */
  q: (selector: string) => Element[]
  /** 此刻是否该省动效。它是取值那一刻的快照，中途改偏好不会追认——揭幕本来就只跑一次。 */
  reduce: boolean
  gsap: typeof gsap
}

/** 揭幕时钟与指针时钟在 JS 侧的镜像。
 *  单一真源是 `main.css` 的那组令牌，这里只是把同一份数交给 timeline——
 *  两边各写一份 ms 的话，改令牌的那次提交就会让 CSS 与 JS 的曲线分家。 */
export const MOTION = {
  reveal: 0.8,
  stage: 1.0,
  quick: 0.15,
  easeOut: 'expo.out',
  easeIn: 'power2.in',
  easeInOut: 'power2.inOut',
} as const
