import { ref } from 'vue'

/**
 * 跨天拖动的瞬时状态（M29e）。
 *
 * 只有三个值，且只在一次拖动这几百毫秒里存在：从哪天拿起、此刻悬在哪一天、有没有在拖。
 * 它们必须是模块级的——发起拖动的 DaySection 与要被高亮成接收方的**另一个** DaySection
 * 是两个组件实例，谁都不该为了这件事去改 trip store（store 是行程数据，拖动不是）。
 *
 * 也正因为它们是瞬时状态：任何一条路径没清干净，界面上就会留下一天永远亮着。
 * 所以 onEnd 里无论落点是否有效都会写一次 null，见 `useDragSort`。
 */

export const dragFromDay = ref<string | null>(null)
export const dragOverDay = ref<string | null>(null)

function endPlaceDrag(): void {
  dragFromDay.value = null
  dragOverDay.value = null
}

export function usePlaceDrag() {
  return { dragFromDay, dragOverDay, endPlaceDrag }
}
