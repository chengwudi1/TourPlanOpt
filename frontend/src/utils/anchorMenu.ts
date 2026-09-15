/**
 * 贴锚点弹出菜单的定位：先量菜单自己的尺寸，再往视口里夹。
 *
 * 卡片菜单原来写的是 `Math.min(y, innerHeight - 150)`——一个凭空的估计。菜单高度随行程
 * 天数长：七天行程有六行「移到 D×」再加固定几项，约 300px，于是底部被裁掉，而且被裁掉的
 * 恰好是最常用的那几行。锚点位置与菜单尺寸都是运行时才有的量，只能挂载之后量完再定位。
 */

export interface MenuPosition {
  left: number
  top: number
}

/** 菜单与锚点之间的缝。 */
const GAP = 6
/** 离视口边缘留的呼吸位，也是夹取的下限。 */
const EDGE = 8

/** 锚点：一个元素的外接矩形，或指点出来的那一个点（left=right、top=bottom）。 */
export type MenuAnchor = Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>

export function anchorMenu(
  anchor: MenuAnchor,
  menu: { offsetWidth: number; offsetHeight: number },
  align: 'start' | 'end' = 'start',
): MenuPosition {
  const vw = document.documentElement.clientWidth
  const vh = document.documentElement.clientHeight
  const w = menu.offsetWidth
  const h = menu.offsetHeight

  let left = align === 'end' ? anchor.right - w : anchor.left
  // 夹在 [EDGE, vw-w-EDGE]：窄屏上 w 可能比视口还宽，这时宁可贴左边也不推出负值。
  left = Math.max(EDGE, Math.min(left, Math.max(EDGE, vw - w - EDGE)))

  let top = anchor.bottom + GAP
  if (top + h > vh - EDGE) {
    // 下面放不下就翻到锚点上方；再挤就贴着下边缘站住。
    top = anchor.top - h - GAP
    if (top < EDGE) top = Math.max(EDGE, vh - h - EDGE)
  }
  return { left, top }
}
