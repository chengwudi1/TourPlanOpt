/**
 * 语音输入的线型声明。
 *
 * TypeScript 的 `lib.dom.d.ts`（5.9）里有 `SpeechRecognitionResultList` 这类**结果**类型，
 * 却没有 `SpeechRecognition` 构造器，也没有 `navigator.speechRecognition` ——
 * 那半套至今是 W3C 草稿。所以这里只补构造器和事件，结果类型仍然复用 DOM 里已有的，
 * 免得抄一份两份口径。
 *
 * 一个真实差异要记住：Safari 的 `start()` 返回 Promise，Chrome 返回 undefined。
 * 签名写成 `void | Promise<void>`，调用处不许 `await` 之后假设它一定活过 microtask。
 */

interface SpeechRecognitionEventMap {
  audioend: Event
  audiostart: Event
  end: Event
  error: SpeechRecognitionErrorEvent
  nomatch: SpeechRecognitionEvent
  result: SpeechRecognitionEvent
  soundstart: Event
}

interface SpeechRecognition extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start(): void | Promise<void>
  stop(): void | Promise<void>
  abort(): void
  onaudioend: ((this: SpeechRecognition, ev: Event) => unknown) | null
  onaudiostart: ((this: SpeechRecognition, ev: Event) => unknown) | null
  onend: ((this: SpeechRecognition, ev: Event) => unknown) | null
  onerror: ((this: SpeechRecognition, ev: SpeechRecognitionErrorEvent) => unknown) | null
  onnomatch: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => unknown) | null
  onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => unknown) | null
  onsoundstart: ((this: SpeechRecognition, ev: Event) => unknown) | null
}

interface SpeechRecognitionEvent extends Event {
  readonly resultIndex: number
  readonly results: SpeechRecognitionResultList
}

interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string
  readonly message: string
}

declare var SpeechRecognition: {
  prototype: SpeechRecognition
  new (): SpeechRecognition
}

interface Window {
  SpeechRecognition?: typeof SpeechRecognition
  webkitSpeechRecognition?: typeof SpeechRecognition
}
