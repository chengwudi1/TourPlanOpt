import { computed, type ComputedRef } from 'vue'

import { useSettingsStore } from '@/stores/settings'

/**
 * 「现在该不该省动效」——全站只从这一个口子读。
 *
 * 为什么不许各处自己 matchMedia：设置面板多了一档「减少动效」之后，浏览器那条媒体查询
 * 就不再等于答案了。更要紧的是 WAAPI（数字滚动、列表 FLIP、弹层退场）根本不吃 CSS 那条
 * 全局兜底，每个自建动画的地方都得自己挡一道 —— 而它们挡的必须是同一个值，也就是
 * settingsStore 写到 <html data-motion> 上的那份已解析结果。
 *
 * 返回 computed 而不是布尔：偏好可以中途改，快照会把「改完还是老动画」这种 bug 藏起来。
 */
export function useReduceMotion(): ComputedRef<boolean> {
  const settings = useSettingsStore()
  return computed(() => settings.reduceMotion)
}
