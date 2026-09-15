import { useDialogStore } from '@/stores/dialog'
import { useFeedbackStore } from '@/stores/feedback'

/**
 * 复制类动作的统一出口（M26b）。
 *
 * 原来 4 处「复制到剪贴板」各自 try/catch，失败就 window.prompt 一段文本让用户手动复制——
 * 目的对，但样式、按钮文字、能不能全选都交给浏览器。这里只换掉失败分支：
 * 成功给一条 toast 回执（O4：复制完什么都没发生），失败弹一个已经全选的只读文本框。
 */
export function useCopy() {
  const dialog = useDialogStore()
  const feedback = useFeedbackStore()

  /** 返回是否真的写进了剪贴板，调用方拿它决定按钮上那句「已复制」要不要翻。 */
  return async function copy(
    text: string,
    opts: { receipt: string; fallbackTitle: string },
  ): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      // 非安全上下文、权限被拒、老浏览器都会走到这里。
      await dialog.showText({
        title: opts.fallbackTitle,
        message: '浏览器不允许自动复制，请手动复制下面已选中的文本。',
        text,
      })
      return false
    }
    feedback.show({ message: opts.receipt })
    return true
  }
}
