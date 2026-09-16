import Sortable from 'sortablejs'
import type { Ref } from 'vue'
import { onBeforeUnmount, watch } from 'vue'

/**
 * Drag-to-reorder for one day's place list, on top of sortablejs (NOT vuedraggable,
 * whose latest published build targets Vue 2).
 *
 * The contract with the protocol: on drop we hand the FULL ordered id array to
 * `onReorder` -- never an index delta. Concurrent drags diverge permanently with
 * deltas; a full array either applies (permutation) or is rejected with the
 * authoritative order back (`order_stale`), which `applyOrder` converges to.
 *
 * The container is watched, not read once: the list is `v-if`-rendered and first
 * appears only after the snapshot arrives, long after the parent mounted.
 */
export interface DragSortSelectors {
  /** sortablejs 的 draggable 选择器。 */
  item?: string
  /** 只有摸到它才能拖。 */
  handle?: string
  /**
   * 摸到这些就不发起拖动。整行都是把手之后必须给一份，否则按住行里的按钮挪两像素
   * 就把卡片拖走了，而那一次点击也不会发生。
   */
  filter?: string
  /** 命中 filter 时是否 preventDefault。默认 false：按钮照常点击、输入框照常选字。 */
  preventOnFilter?: boolean
  /** 行元素上存 id 的 data 属性名（camelCase 会自动转成 kebab-case 的 dataset 键）。 */
  idAttr?: string
  /** 占位幽灵的类名。各列表的行名不同，样式只能各自写，所以跟着行名一起给。 */
  ghostClass?: string
  /**
   * 跨列表拖动：同一个组名的列表之间可以互 drop。不给（默认）就只在列表内排序，
   * 与 M29e 之前的行为逐字一致。
   */
  group?: string
  /** 容器上存「这一列是谁」的 data 属性名，配合 group 用。 */
  listIdAttr?: string
  /**
   * 落到了别的列表：(被拖的行, 来源列表, 目标列表, 它前面的那一行或 null)。
   * `beforeId` 为 null 就是落在目标列表末尾。
   */
  onTransfer?: (itemId: string, fromList: string, toList: string, beforeId: string | null) => void
  /** 指针此刻停在哪个列表上（给目标列高亮用），null 表示已经移出所有列表。 */
  onHoverList?: (listId: string | null) => void
}

const DEFAULTS = {
  item: '.place',
  // 正文整行，不只是那一枚 grip：鼠标瞄 14px 宽的窄条太苛刻了。触屏不受这个影响——
  // 行上没有 touch-action:none，手指一划仍归浏览器滚动，能拖的仍然只有 grip（见 PlaceCard）。
  handle: '.place__row',
  preventOnFilter: false,
  idAttr: 'data-place-id',
  ghostClass: 'place--ghost',
  listIdAttr: 'data-day-id',
}

export function useDragSort(
  container: Ref<HTMLElement | null>,
  onReorder: (orderedIds: string[]) => void,
  onDragging?: (dragging: boolean) => void,
  selectors: DragSortSelectors = {},
) {
  const {
    item,
    handle,
    filter,
    preventOnFilter,
    idAttr,
    ghostClass,
    group,
    listIdAttr,
    onTransfer,
    onHoverList,
  } = { ...DEFAULTS, ...selectors }
  let sortable: Sortable | null = null

  function idOf(el: Element): string | null {
    return el instanceof HTMLElement ? el.getAttribute(idAttr) : null
  }

  function listIdOf(el: Element | null | undefined): string {
    return el instanceof HTMLElement ? (el.getAttribute(listIdAttr) ?? '') : ''
  }

  /** 被拖行在目标列表里紧挨着的下一行（跳过腿、脚这些不参与的兄弟）。 */
  function nextItemId(dragged: HTMLElement): string | null {
    let node = dragged.nextElementSibling
    while (node) {
      if (node.matches(item)) return idOf(node)
      node = node.nextElementSibling
    }
    return null
  }

  /**
   * 把跨列表的行塞回原来的容器、原来的位次。
   *
   * 必须做：Vue 的虚拟 DOM 认为这一行还长在 `from` 里，而 Sortable 已经把它搬进别的父节点了。
   * 下一次 keyed patch 会拿这个「外来节点」当 insertBefore 的锚点，浏览器直接抛
   * NotFoundError —— 整条列表裂在原地。挪回去之后由模型驱动重渲染，两边各自重建。
   */
  function restoreTo(from: HTMLElement, dragged: HTMLElement, oldIndex: number | undefined) {
    const siblings = Array.from(from.children).filter((c) => c.matches(item))
    const anchor = oldIndex === undefined ? null : (siblings[oldIndex] ?? null)
    from.insertBefore(dragged, anchor)
  }

  function destroy() {
    sortable?.destroy()
    sortable = null
  }

  const stop = watch(
    container,
    (el) => {
      destroy()
      if (!el) return
      sortable = Sortable.create(el, {
        animation: 150,
        handle,
        filter,
        // 命中 filter 只是「这一次不发起拖动」，点击与选字要照常发生。
        preventOnFilter,
        draggable: item,
        ghostClass,
        // 把手上写了 touch-action:none，落在把手上的滑动就不再是滚动了；所以触屏要先按住
        // 一小下才算拖动（鼠标保持零延迟），否则手指一划就飞出去一张卡。
        delay: 120,
        delayOnTouchOnly: true,
        touchStartThreshold: 5,
        // 目标列被 .daysec__list--locked 之类设了 pointer-events:none 时，命中测试根本
        // 落不到它身上，Sortable 不会把它当候选接收方——所以「拖进别人正在调序的那天」
        // 这条不需要额外的判定条件，浏览器已经替我们挡了。
        group: group ? { name: group, pull: true, put: true } : undefined,
        onMove(evt) {
          onHoverList?.(listIdOf(evt.to))
          return true
        },
        onStart() {
          onDragging?.(true)
        },
        onEnd(evt) {
          onDragging?.(false)
          const { from, to, oldIndex } = evt
          const dragged = evt.item
          onHoverList?.(null)

          if (from !== to) {
            const itemId = idOf(dragged)
            const fromList = listIdOf(from)
            const toList = listIdOf(to)
            const beforeId = nextItemId(dragged)
            restoreTo(from, dragged, oldIndex)
            if (itemId && fromList && toList) onTransfer?.(itemId, fromList, toList, beforeId)
            return
          }

          const ids = Array.from(el.children ?? [])
            .map(idOf)
            .filter((x): x is string => x !== null)
          const { newIndex } = evt
          if (oldIndex === undefined || newIndex === undefined) return
          if (oldIndex === newIndex) return
          if (ids.length) onReorder(ids)
        },
      })
    },
    { flush: 'post' },
  )

  onBeforeUnmount(() => {
    stop()
    destroy()
  })

  return {
    get instance() {
      return sortable
    },
  }
}
