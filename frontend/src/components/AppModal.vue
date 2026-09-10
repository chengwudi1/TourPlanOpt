<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { X } from '@/components/icons'

/**
 * 全站唯一的弹层。`variant="sheet"` 是「手机底部抽屉 + 桌面居中模态」的同一条实现：
 * 断点交给 CSS，JS 只在手机上启用下滑关闭（居中模态往下拽读起来像坏了）。
 *
 * 关闭只有一条路径：`requestClose()` → 播完退场 → emit('close')。父组件依然只用 v-if
 * 决定在不在，退场由这里自己演——所以父组件不许直接把它 v-if 掉，否则退场永远看不到。
 */
const props = withDefaults(
  defineProps<{
    title: string
    sub?: string
    variant?: 'sheet' | 'center'
    /** 退场动画的时长（ms），也用来兜底超时。 */
    leaveMs?: number
  }>(),
  { variant: 'sheet', leaveMs: 220 },
)

const emit = defineEmits<{ close: [] }>()

const panel = ref<HTMLElement | null>(null)
const backdrop = ref<HTMLElement | null>(null)
const dragging = ref(false)

let closing = false
let prevOverflow = ''
let pointerStartY = 0
let pointerStartTime = 0
let pointerId = -1

/** 手机上才有的「可下滑关闭」。断点与 CSS 的 860px 对齐。 */
const canSwipe = computed(
  () => props.variant === 'sheet' && window.matchMedia('(max-width: 860px)').matches,
)

/**
 * 位移写 CSS 变量而不是 style.transform：后者要连带关掉入场动画，而 class 一撤
 * `animation: sheet-up` 就会重新播一遍，松手后抽屉看着像又弹出来一次。交给变量，
 * 动画只在播放期间覆盖基础 transform，两者不再抢同一条声明。
 */
function setDragY(px: number) {
  panel.value?.style.setProperty('--drag-y', `${Math.max(0, px)}px`)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    void requestClose()
    return
  }
  if (e.key === 'Tab') trapTab(e)
}

/** 焦点不外逃到遮罩后面的页面：抽屉是模态的，Tab 就该在里头转圈。 */
function trapTab(e: KeyboardEvent) {
  const root = panel.value
  if (!root) return
  const items = [
    ...root.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'),
  ].filter((el) => el.offsetParent !== null || el === document.activeElement)
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  if (e.shiftKey && (active === first || active === root)) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && active === last) {
    e.preventDefault()
    first.focus()
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * 退场用 WAAPI 而不是 CSS class：class 方案要等 transitionend，而在隐藏的内嵌浏览器里
 * 动画时钟是停的，事件永远不来，弹层就再也关不掉了。所以 `Promise.race` 一个纯定时器
 * 兜底。同理，CSS 那条全局 prefers-reduced-motion 兜不住 WAAPI，必须自己挡。
 */
async function requestClose() {
  if (closing) return
  closing = true
  if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const frames = props.variant === 'sheet' && canSwipe.value
      ? [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(100%)' }]
      : [{ opacity: 1, transform: 'translateY(0) scale(1)' }, { opacity: 0, transform: 'translateY(12px) scale(0.98)' }]
    const runs: Animation[] = []
    if (backdrop.value) {
      runs.push(
        backdrop.value.animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: props.leaveMs,
          fill: 'forwards',
        }),
      )
    }
    if (panel.value) {
      runs.push(
        panel.value.animate(frames, {
          duration: props.leaveMs,
          easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
          fill: 'forwards',
        }),
      )
    }
    const done = runs.map((a) => a.finished.catch(() => undefined))
    await Promise.race([Promise.all(done), delay(props.leaveMs + 160)])
  }
  emit('close')
}

/* ---------- 无把手下滑关闭：顶部一条全透明的 pointer-capture 区 ---------- */

function onGrabDown(e: PointerEvent) {
  if (!canSwipe.value || closing) return
  pointerId = e.pointerId
  pointerStartY = e.clientY
  pointerStartTime = e.timeStamp
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(pointerId)
}

function onGrabMove(e: PointerEvent) {
  if (!dragging.value || e.pointerId !== pointerId) return
  setDragY(e.clientY - pointerStartY)
}

function onGrabUp(e: PointerEvent) {
  if (!dragging.value || e.pointerId !== pointerId) return
  dragging.value = false
  const dy = Math.max(0, e.clientY - pointerStartY)
  const dt = Math.max(1, e.timeStamp - pointerStartTime)
  const height = panel.value?.offsetHeight || 1
  // 位移过三分之一，或者轻轻一甩（速度 > 0.6px/ms）——TREK 的两个阈值，抄的是判据不是代码。
  if (dy / height > 0.3 || dy / dt > 0.6) void requestClose()
  else setDragY(0)
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  prevOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  const first = panel.value?.querySelector<HTMLElement>('input, textarea, select')
  if (first) first.focus()
  else panel.value?.focus()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = prevOverflow
})

defineExpose({ close: requestClose })
</script>

<template>
  <Teleport to="body">
    <div ref="backdrop" class="modal" :class="`modal--${variant}`" @click.self="requestClose">
      <div
        ref="panel"
        class="modal__panel card"
        :class="{ 'modal__panel--dragging': dragging }"
        tabindex="-1"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
      >
        <div
          v-if="canSwipe"
          class="modal__grab"
          aria-hidden="true"
          @pointerdown="onGrabDown"
          @pointermove="onGrabMove"
          @pointerup="onGrabUp"
          @pointercancel="onGrabUp"
        />
        <header class="modal__head">
          <div class="modal__heading">
            <h2>{{ title }}</h2>
            <p v-if="sub" class="tiny muted">{{ sub }}</p>
          </div>
          <button class="iconbtn" type="button" title="关闭" @click="requestClose">
            <X :size="16" />
          </button>
        </header>
        <div class="modal__body">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="modal__foot">
          <slot name="footer" />
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(30, 20, 10, 0.42);
  animation: modal-fade var(--dur) var(--ease-out);
}

.modal__panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 440px;
  max-height: calc(100vh - 40px);
  padding: 0;
  overflow: hidden;
  box-shadow: var(--shadow-modal);
  /* 基础 transform 常驻，下滑改的只是 --drag-y；松手回位要有弹性，所以留一条过渡。 */
  transform: translateY(var(--drag-y, 0px));
  transition: transform var(--dur-slow) var(--ease-out);
}

/* 宽屏上 sheet 也是居中的，居中浮层从屏幕底下推上来读起来像坏了——
   所以抽屉式上滑只在手机断点里给，退场帧按同一个 canSwipe 判据选。 */
.modal--center .modal__panel,
.modal--sheet .modal__panel {
  animation: modal-pop var(--dur) var(--ease-pop) backwards;
}

/* 跟手期间不许有过渡：那几十毫秒的滞后读起来像抽屉粘住了。 */
.modal__panel--dragging {
  transition: none;
}

/* 无把手：一条 22px 的全透明捕获区。`touch-action: none` 只给它，
   内容区照常滚动，不会和列表拖拽抢手势。 */
.modal__grab {
  flex: 0 0 auto;
  height: 22px;
  cursor: grab;
  touch-action: none;
}
.modal__grab:active {
  cursor: grabbing;
}

.modal__head {
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
  align-items: flex-start;
  padding: 16px 16px 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--border-faint);
}

.modal__heading {
  flex: 1;
  min-width: 0;
}

.modal__heading h2 {
  font-size: 16px;
}

.modal__heading p {
  margin: 2px 0 0;
}

.modal__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.modal__foot {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  padding: 12px 16px 16px;
  background: var(--surface);
  border-top: 1px solid var(--border-faint);
}

.modal__panel:focus-visible {
  outline: none;
}

@keyframes modal-fade {
  from {
    opacity: 0;
  }
}

@keyframes modal-pop {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.97);
  }
}

@keyframes sheet-up {
  from {
    transform: translateY(100%);
  }
}

/* ---------- 手机：底部抽屉 ---------- */
@media (max-width: 860px) {
  .modal--sheet {
    place-items: end center;
    padding: 0;
  }
  .modal--sheet .modal__panel {
    animation: sheet-up var(--dur-slow) var(--ease-out) backwards;
    width: 100%;
    max-width: none;
    /* 键盘弹起时 dvh 会跟着缩，vh 不会——先写 vh 兜老浏览器，再让 dvh 覆盖。 */
    max-height: 88vh;
    max-height: 88dvh;
    border: 0;
    border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    border-bottom: 0;
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }
  .modal--sheet .modal__head {
    padding-top: 4px;
  }
  .modal--sheet .modal__foot {
    padding-bottom: 16px;
  }
}
</style>
