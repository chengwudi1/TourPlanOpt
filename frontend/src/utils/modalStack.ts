/**
 * 叠放模态的键盘归属（M26b）。
 *
 * 一个 `<script setup>` 里没有模块作用域，所以这条栈必须放在外面：每个 AppModal 实例
 * 都往 document 挂同一条 keydown，而 `stopPropagation()` 挡不住**同一节点上的另一个监听器**
 * ——一次 Esc 会把两层模态一起关掉，底下那层刚填好的内容跟着没了。Tab 圈同理，两个实例
 * 互相拽焦点会原地打转。
 *
 * 规矩只有一条：键盘只由栈顶那一层回应。
 */
const layers: number[] = []
let seq = 0

/** 登记一层，返回它的票据（挂载时调用，与 keydown 监听同一时机）。 */
export function pushModalLayer(): number {
  const id = ++seq
  layers.push(id)
  return id
}

export function popModalLayer(id: number): void {
  const at = layers.indexOf(id)
  if (at >= 0) layers.splice(at, 1)
}

export function isTopModalLayer(id: number): boolean {
  return layers[layers.length - 1] === id
}
