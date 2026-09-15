import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 全站唯一的「要用户回答一句」通道（M26b）。
 *
 * 存在的原因：这类交互原来全是 window.confirm / window.prompt，一共 16 处。原生框不受样式
 * 与文案控制（手机上是系统弹窗、深色模式下刺眼、按钮文字改不成「删除」这种准确动词），
 * 而且它同步阻塞——正好鼓励了「静默丢掉非法输入」这种写法。换成 AppModal 之后，
 * 校验能就地报错，金额能换成数字键盘。
 *
 * 队列一次只演一条：原生框的语义本来就是阻塞，连着弹两个会把答案串到错误的问题上。
 * 出场的时序由 DialogHost 负责（退场动画得播完才换下一条），这里只管排队和兑现承诺。
 */

export type DialogKind = 'confirm' | 'prompt' | 'text'

export interface ConfirmRequest {
  title: string
  /** 副文案，允许多段（渲染时保留换行）。 */
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  /** 会丢数据的确认用 danger：--accent 只留给可逆的交互。 */
  danger?: boolean
}

export interface PromptRequest {
  title: string
  message?: string
  value?: string
  placeholder?: string
  /** decimal 让手机弹小键盘；输入框仍是 type=text，不然 `1,280` 这种写法会被拦在浏览器里。 */
  inputMode?: 'text' | 'decimal' | 'numeric' | 'email' | 'url' | 'tel'
  /** 单位后缀，只做显示——解析仍然交给调用方（金额的 parseMoneyToCents）。 */
  unit?: string
  maxLength?: number
  /** 默认 true：全空白的提交就地挡掉。留空本身有意义的（改名恢复默认、清预算）传 false。 */
  required?: boolean
  emptyMessage?: string
  confirmLabel?: string
  cancelLabel?: string
  /** 返回一条错误文案就挡住提交，返回 null 放行。 */
  validate?: (raw: string) => string | null
}

export interface TextRequest {
  title: string
  message?: string
  text: string
}

/** confirm → boolean，prompt → string | null（null 是「取消」，空串是「留空」），text → void。 */
export type DialogValue = boolean | string | null

export interface DialogEntry {
  seq: number
  kind: DialogKind
  req: ConfirmRequest | PromptRequest | TextRequest
  resolve: (value: DialogValue) => void
  /** 已兑现。退场期间用户再点一次也不会重复 resolve，也不会把答案算到下一条头上。 */
  answered: boolean
}

const CANCEL_VALUE: Record<DialogKind, DialogValue> = {
  confirm: false,
  prompt: null,
  text: null,
}

export const useDialogStore = defineStore('dialog', () => {
  const pending: DialogEntry[] = []
  /** 每投递一条自增一次，DialogHost 盯它决定是否从队列里取下一条来显示。 */
  const ticket = ref(0)
  let seq = 0

  function push(kind: DialogKind, req: DialogEntry['req']): Promise<DialogValue> {
    return new Promise<DialogValue>((resolve) => {
      pending.push({ seq: ++seq, kind, req, resolve, answered: false })
      ticket.value += 1
    })
  }

  /** Host 播完上一条的退场才会来取，所以这里的 take 就是「出队」。 */
  function take(): DialogEntry | null {
    return pending.shift() ?? null
  }

  function settle(entry: DialogEntry | null, value: DialogValue) {
    if (!entry || entry.answered) return
    entry.answered = true
    entry.resolve(value)
  }

  /** 遮罩、Esc、关闭按钮都走这里：没回答就等于取消。 */
  function settleCancel(entry: DialogEntry | null) {
    if (entry) settle(entry, CANCEL_VALUE[entry.kind])
  }

  function confirm(request: string | ConfirmRequest): Promise<boolean> {
    const req = typeof request === 'string' ? { title: request } : request
    return push('confirm', req) as Promise<boolean>
  }

  function prompt(request: PromptRequest): Promise<string | null> {
    return push('prompt', request) as Promise<string | null>
  }

  /** 只读展示一段文本（剪贴板不可用时的手动复制）。关闭即 resolve(null)。 */
  function showText(request: TextRequest): Promise<void> {
    return (push('text', request) as Promise<null>).then(() => undefined)
  }

  return { ticket, take, settle, settleCancel, confirm, prompt, showText }
})
