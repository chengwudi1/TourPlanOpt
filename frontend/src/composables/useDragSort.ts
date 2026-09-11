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
  /** 行元素上存 id 的 data 属性名（camelCase 会自动转成 kebab-case 的 dataset 键）。 */
  idAttr?: string
}

const DEFAULTS = {
  item: '.place',
  handle: '.place__drag',
  idAttr: 'data-place-id',
}

export function useDragSort(
  container: Ref<HTMLElement | null>,
  onReorder: (orderedIds: string[]) => void,
  onDragging?: (dragging: boolean) => void,
  selectors: DragSortSelectors = {},
) {
  const { item, handle, idAttr } = { ...DEFAULTS, ...selectors }
  let sortable: Sortable | null = null

  function idOf(el: Element): string | null {
    return el instanceof HTMLElement ? el.getAttribute(idAttr) : null
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
        draggable: item,
        ghostClass: 'place--ghost',
        onStart() {
          onDragging?.(true)
        },
        onEnd(evt) {
          onDragging?.(false)
          const ids = Array.from(el.children ?? [])
            .map(idOf)
            .filter((x): x is string => x !== null)
          const { oldIndex, newIndex } = evt
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
