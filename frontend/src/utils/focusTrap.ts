/**
 * 焦点圈在弹层里转圈（O1）。
 *
 * 弹层是模态的：Tab 不该跑到遮罩背后的页面里去。AppModal 与「加入行程」遮罩都要这一条，
 * 而 `<script setup>` 里没有可共享的模块作用域，所以放在这里。
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'

/** 处理过了返回 true（默认行为已在内部拦掉）；根节点不在或没有可聚焦项时不管。 */
export function trapTab(root: HTMLElement | null, e: KeyboardEvent): boolean {
  if (!root) return false
  const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  )
  if (!items.length) return false
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  if (e.shiftKey && (active === first || active === root)) {
    e.preventDefault()
    last.focus()
    return true
  }
  if (!e.shiftKey && active === last) {
    e.preventDefault()
    first.focus()
    return true
  }
  return false
}
