import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 全站唯一的瞬时反馈通道（M26a）。
 *
 * 存在的原因：反馈以前分三套——HomeView 自己一个 `.home__say`、TripView 把 opError 渲染在
 * 滚动容器最顶部（被拒的消息落在视口外，用户只看到刚加的那行自己消失了）、复制成功的回执
 * 各写各的。三套都缺同一个东西：**保证在屏幕上的固定位置**。
 */

export type ToastKind = 'ok' | 'info' | 'danger'

export interface ToastAction {
  label: string
  run: () => void
}

export interface Toast {
  id: number
  kind: ToastKind
  message: string
  hint: string
  action: ToastAction | null
}

const MAX_TOASTS = 3
const OK_MS = 2600
/** 数据没写进去的告警要留够时间读完，但不能挂到下一次操作才走（旧 banner 的毛病）。 */
const DANGER_MS = 9000

export const useFeedbackStore = defineStore('feedback', () => {
  const toasts = ref<Toast[]>([])
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  let seq = 0

  function dismiss(id: number) {
    const timer = timers.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.delete(id)
    }
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  /** 回执一律不带焦点：抢焦点会把用户从正在输入的字段里拽出来。 */
  function show(input: {
    message: string
    kind?: ToastKind
    hint?: string
    action?: ToastAction
    durationMs?: number
  }): number {
    const id = ++seq
    const kind = input.kind ?? 'ok'
    toasts.value = [...toasts.value.slice(-(MAX_TOASTS - 1)), {
      id,
      kind,
      message: input.message,
      hint: input.hint ?? '',
      action: input.action ?? null,
    }]
    const ms = input.durationMs ?? (kind === 'danger' ? DANGER_MS : OK_MS)
    if (ms > 0) timers.set(id, setTimeout(() => dismiss(id), ms))
    return id
  }

  return { toasts, show, dismiss }
})
