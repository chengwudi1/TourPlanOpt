import type { Ref } from 'vue'
import { nextTick, watch } from 'vue'

import { useReduceMotion } from '@/composables/useReduceMotion'

/**
 * 令牌 → WAAPI 的取值口。
 *
 * 为什么在 JS 里读 CSS 变量而不是抄一份字面量：这条重排走的是**指针时钟**（用户对一次
 * 点击的响应），而指针时钟的数值全站只有一处真源，就是 `main.css` 那组 `--ease` / `--dur`。
 * 抄在 JS 里的话，改令牌的那次提交就会让 CSS 与 JS 的曲线分家——这条已经有过先例
 * （`useMotion` 的 `MOTION` 常量同一条口径）。读不到才退回字面量，一次改名不该把动画整个弄没。
 */
function token(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}
/** `--dur-slow` 这类是 `220ms`，WAAPI 要的是毫秒数。 */
function tokenMs(name: string, fallback: number): number {
  const raw = token(name, `${fallback}ms`)
  const n = Number.parseFloat(raw)
  if (!Number.isFinite(n)) return fallback
  return raw.includes('s') && !raw.includes('ms') ? n * 1000 : n
}
/** 与 main.css 里 .flash-in 的那 900ms 同步：类留着只是为了让动画跑完，摘晚了没人管。 */
const FLASH_MS = 900

function rowKey(node: Element): string | null {
  const el = node as HTMLElement
  return el.dataset.flipKey ?? el.dataset.placeId ?? null
}

/**
 * 手写 FLIP：一天里的地点换了顺序之后，让每一行自己走过去，而不是瞬间换位。
 *
 * 为什么不用 <TransitionGroup>：每个地点要渲染两行（连接行 + 卡片），而 Vue 3.5 的
 * 编译器规定 `<template v-for>` 的 key 只能挂在 template 上（挂在子节点直接报
 * X_V_FOR_TEMPLATE_KEY_PLACEMENT）。于是 TransitionGroup 看到的是一堆 keyed Fragment，
 * keyed Fragment 又故意不摊平，move 类只会加到 Fragment 的第一个 DOM 节点上。更麻烦的是
 * 它的 ref 拿到的是组件实例，交给 sortablejs 会在零报错的情况下失效。所以这里守着原来的
 * DOM 结构，只补「重排可见」这一件事。
 *
 * 时序：watch 默认 pre-flush，回调跑的时候组件还没重渲染，此刻量到的就是旧位置；
 * nextTick 之后新位置出来，差值反向推回去再放开，就是 FLIP。
 *
 * 拖拽那一下不经过这里：sortablejs 直接在 DOM 里挪节点，store 要等 onEnd 才收到完整
 * 顺序，届时 DOM 已经是新顺序了，差值≈0，不会二次动画。
 */
export function useFlipList(container: Ref<HTMLElement | null>, keys: () => string[]) {
  // WAAPI 建的动画不吃 CSS 的全局兜底，设置面板那一档也只能由 JS 看见：单一出口见
  // composables/useReduceMotion。
  const reduceMotion = useReduceMotion()

  watch(
    () => keys().join('|'),
    () => {
      // 首帧不从这里过：列表还没渲染出 <ul> 时容器是 null，snapshot 落地的第一次
      // 填充因此不会拿到「旧位置」去比，也就不会给一批凭空出现的行做位移。
      const el = container.value
      if (!el || reduceMotion.value) return

      // 一次重排内只读一遍：这些值不该在同一批行的动画之间变卦。
      const ease = token('--ease', 'cubic-bezier(0.4, 0, 0.2, 1)')
      const swapMs = tokenMs('--dur-slow', 220)
      const enterMs = tokenMs('--dur', 180)

      const before = new Map<string, DOMRect>()
      for (const node of Array.from(el.children)) {
        const key = rowKey(node)
        if (key) before.set(key, node.getBoundingClientRect())
      }

      void nextTick(() => {
        const root = container.value
        if (!root) return
        for (const node of Array.from(root.children)) {
          const key = rowKey(node)
          if (!key) continue
          const oldRect = before.get(key)
          // 老行做位移，新来的行做淡入 + 点名底色；对不上号的（换到天那边去了）什么都不做。
          if (!oldRect) {
            const row = node as HTMLElement
            const flash = (e?: AnimationEvent) => {
              if (e && (e.target !== row || e.animationName !== 'flash-in')) return
              row.classList.remove('flash-in')
              row.removeEventListener('animationend', flash)
            }
            row.classList.add('flash-in')
            // animationend 只对 CSS 动画响，WAAPI 那次不触发它；行如果在 900ms 内被卸掉
            // 就再也收不到事件，所以超时兜底，两条都做同一件事。
            row.addEventListener('animationend', flash)
            setTimeout(flash, FLASH_MS)
            row.animate(
              [
                { opacity: 0, transform: 'scale(0.97)' },
                { opacity: 1, transform: 'none' },
              ],
              { duration: enterMs, easing: ease },
            )
            continue
          }
          const rect = node.getBoundingClientRect()
          const dx = oldRect.left - rect.left
          const dy = oldRect.top - rect.top
          if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue
          node.animate(
            [
              { transform: `translate(${dx}px, ${dy}px)` },
              { transform: 'translate(0, 0)' },
            ],
            { duration: swapMs, easing: ease },
          )
        }
      })
    },
  )
}
