<script setup lang="ts">
import { X } from '@/components/icons'
import type { Toast } from '@/stores/feedback'
import { useFeedbackStore } from '@/stores/feedback'

/**
 * 全局反馈的落点：挂在 App.vue 的 RouterView 之外，所以换页不会把它带走，
 * 它也不在带 transform 的过渡容器里（fixed 定位会改以页面为参照，见 App.vue 注释）。
 */
const feedback = useFeedbackStore()

function runAction(toast: Toast) {
  toast.action?.run()
  feedback.dismiss(toast.id)
}
</script>

<template>
  <div v-if="feedback.toasts.length" class="toasts" aria-live="polite" aria-label="操作反馈">
    <!-- 入场靠 CSS animation（新建节点才播），离场直接移除：
         不用 <Transition>，因为 transitionend 在后台标签页里可能永远不来，元素会卡在离场态。 -->
    <div
      v-for="toast in feedback.toasts"
      :key="toast.id"
      class="toast"
      :class="`toast--${toast.kind}`"
      :role="toast.kind === 'danger' ? 'alert' : 'status'"
    >
      <span class="toast__text">
        {{ toast.message }}<template v-if="toast.hint"> · {{ toast.hint }}</template>
      </span>
      <button v-if="toast.action" class="toast__action" type="button" @click="runAction(toast)">
        {{ toast.action.label }}
      </button>
      <button class="toast__close" type="button" aria-label="关闭提示" @click="feedback.dismiss(toast.id)">
        <X class="ic" :size="13" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  z-index: var(--z-toast);
  left: 50%;
  bottom: 28px;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: max-content;
  max-width: min(46ch, calc(100vw - 28px));
  /* 容器本身不接点击：它横跨整屏宽，接了就在内容上盖一层看不见的挡板。 */
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 100%;
  padding: 7px 8px 7px 13px;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text);
  background: var(--ok-soft);
  border: 1px solid var(--ok-border);
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-md);
  animation: rise-in var(--dur-slow) var(--ease-out) backwards;
}
.toast--info {
  background: var(--surface);
  border-color: var(--border);
}
.toast--danger {
  background: var(--danger-soft);
  border-color: var(--danger-border);
}
.toast__text {
  min-width: 0;
}
.toast__action {
  flex: none;
  padding: 3px 10px;
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  color: var(--accent-strong);
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius-pill);
}
.toast__action:hover {
  background: var(--surface-hover);
}
.toast__close {
  flex: none;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  color: var(--text-2);
  background: none;
  border: 0;
  border-radius: 50%;
}
.toast__close:hover {
  color: var(--text);
  background: var(--surface-3);
}

@media (max-width: 860px) {
  .toasts {
    /* 底部是 dock 与精灵的地盘（M26e 刚把精灵抬到 dock 之上），压在它们上面等于少一个按钮；
       顶部除了页头没有常驻交互，而且不管滚到哪一屏都保证看得见——这正是 S5 缺的性质。
       62px 的手算在最大字号档会把 toast 骑进顶栏，统一读含安全区的 --header-total-h。 */
    top: calc(var(--header-total-h) + 8px);
    bottom: auto;
    align-items: flex-start;
  }
}
</style>
