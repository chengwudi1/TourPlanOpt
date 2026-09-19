import { ref } from 'vue'

/**
 * 与 main.css 的 860px 断点同源：那一条线以下列表与地图互换、dock 出现、地点编辑器
 * 从卡片内联改成底部抽屉。JS 侧要问的是同一个问题，所以全站共用一份订阅——
 * 各写各的 matchMedia 迟早会有一天两处在同一个宽度上打架。
 */
const mql = window.matchMedia('(max-width: 860px)')
const narrow = ref(mql.matches)
mql.addEventListener('change', (e) => {
  narrow.value = e.matches
})

export function useNarrowView() {
  return narrow
}
