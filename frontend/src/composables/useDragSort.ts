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
export function useDragSort(
  container: Ref<HTMLElement | null>,
  onReorder: (orderedIds: string[]) => void,
  onDragging?: (dragging: boolean) => void,
) {
  let sortable: Sortable | null = null

  function idOf(el: Element): string | null {
    return el instanceof HTMLElement ? (el.dataset.placeId ?? null) : null
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
        handle: '.place__drag',
        draggable: '.place',
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
