/**
 * 语音输入（M29d / M29g）：一个话筒，两条识别路。
 *
 * 后端转写优先：`POST /api/trips/{id}/assistant/speech` 收一段录音、回一行文字。加这条
 * 路的理由是浏览器那条在本机是死的——Chrome 的 Web Speech 把识别放在自己那台云端服务上，
 * 端点不可配置，网络到不了就只剩一个 `network` 错误，用户手上没有任何补救办法。后端这条路
 * 把端点交给 `ASR_BASE_URL`（本地 whisper.cpp 或国内云的 OpenAI 兼容接口），没配 ASR 时才
 * 退回浏览器识别，本来能用的浏览器不受影响。
 *
 * 四件必须承认的现实，都变成了下面的状态：
 * 1. **不是所有浏览器都有识别**。Safari 的 `webkitSpeechRecognition` 是半成品，Firefox 没有；
 *    `MediaRecorder` 那半则要求安全上下文。不支持时按钮要亮着并说明原因——藏起来的东西用户
 *    只会当成"又坏了"。
 * 2. **它会自己停**（浏览器那条静音一会儿就 end），所以 `listening` 不该被当成一直为真。
 * 3. **转写要等**。录音停在话筒那一下，文字几十毫秒到几秒后才回来，这段时间输入框归后端写。
 * 4. **空文字是「没听到」，不是「坏了」**。后端只在真失败时才报错，回空串就是这段录音里没人
 *    说话，两者在界面上必须是两条不同的话。
 *
 * 文字直接写进调用方给的那根 ref：界面上只有一个输入框，两处写它必然打架。听写/转写期间该
 * ref 由本文件独占（面板把它设为只读），这是不打架的代价，也是唯一干净的做法。
 */

import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'

import { apiFetch, type ApiError } from '@/utils/api'

export type SpeechError =
  | ''
  | 'denied'
  | 'no-mic'
  | 'network'
  | 'no-speech'
  | 'unsupported'
  | 'failed'
  /** 后端转写这条路没配好或没答上来：文案取服务端给的 message，不在前端另编一套。 */
  | 'server'

const ERROR_TEXT: Record<Exclude<SpeechError, ''>, string> = {
  denied: '麦克风权限未放行，需要在浏览器地址栏里允许使用麦克风',
  'no-mic': '未找到麦克风设备',
  network: '浏览器语音识别要把音频发到境外服务，当前网络到不了那台服务',
  'no-speech': '未识别到语音',
  unsupported: '当前浏览器不提供语音识别，可改用 Chrome 或 Edge',
  failed: '语音识别中断',
  server: '语音转写服务没有应答',
}

/** 报完就不会再有结果、引擎也常常不补一发 onend 的错误码：在 onerror 里就地收摊。 */
const NO_RECOVERY = new Set([
  'network',
  'not-allowed',
  'service-not-allowed',
  'audio-capture',
  'no-speech',
  'language-not-supported',
])

/** 尽量挑 opus：webm/opus 是浏览器能录、上游普遍认的唯一一档。 */
const RECORD_MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']

export interface SpeechOptions {
  /** 后端转写可用（`/api/assistant/status` 的 speech_ready）；为假就走浏览器识别。 */
  serverReady?: () => boolean
  /** 录音交给哪个行程转写；没有行程时话筒不该被点到。 */
  tripId?: () => string | null | undefined
  /** 后端允许的最长录音秒数：到点自己收，不等用户发现。 */
  maxSeconds?: () => number
}

function recognizerClass(): (new () => SpeechRecognition) | null {
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null
}

function recorderAvailable(): boolean {
  return typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

/**
 * 把已有文字和新听到的一段拼起来。中文之间不插空格；英文与数字混着说时
 * （「加个 starbucks」）靠标点判断要不要留一个空格，否则词会黏在一起。
 */
function join(base: string, heard: string): string {
  const left = base.replace(/\s+$/, '')
  const right = heard.replace(/^\s+/, '')
  if (!left) return right
  if (!right) return left
  return /[，。？！、；：,.?!;:]$/.test(left) ? left + right : `${left} ${right}`
}

function mimeForRecording(): string {
  for (const mime of RECORD_MIMES) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported?.(mime)) return mime
  }
  return ''
}

/** 头部那串 codecs= 只是给浏览器看的，文件名要的是容器。 */
function suffixOf(mime: string): string {
  const base = mime.split(';')[0]
  return { 'audio/webm': 'webm', 'audio/mp4': 'm4a', 'audio/ogg': 'ogg' }[base] ?? 'bin'
}

function micErrorOf(err: unknown): SpeechError {
  const name = (err as { name?: string })?.name ?? ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'denied'
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError')
    return 'no-mic'
  return 'failed'
}

export function useSpeechInput(target: Ref<string>, options: SpeechOptions = {}) {
  const listening = ref(false)
  /** 录音已停、文字还没回来的那段：面板据此把输入框钉成只读，别让手打的字被转写结果盖掉。 */
  const busy = ref(false)
  const error = ref<SpeechError>('')
  /** 服务端说的那句「怎么了」：后端比前端清楚是缺配置还是没应答，原文直接用它。 */
  const serverText = ref('')
  /** 服务端说的那句「下一步改哪一行」。 */
  const errorHint = ref('')
  const interim = ref('')
  /** 这一句最后由谁认的：说清「服务器转写」还是「浏览器识别」，用户才知道配额和隐私的边界。 */
  const mode = computed(() => (options.serverReady?.() && recorderAvailable() ? 'server' : 'browser'))

  let rec: SpeechRecognition | null = null
  let media: MediaRecorder | null = null
  let stream: MediaStream | null = null
  let chunks: Blob[] = []
  let capTimer: ReturnType<typeof setTimeout> | undefined
  /** 按下话筒那一刻已有的文字。之后每帧都从它出发重写，绝不在"当前值"上叠。 */
  let base = ''
  let errorTimer: ReturnType<typeof setTimeout> | undefined

  const supported = computed(() =>
    mode.value === 'server' ? recorderAvailable() : recognizerClass() !== null,
  )

  function render(): void {
    target.value = join(base, interim.value)
  }

  function showError(next: SpeechError, message = '', hint = ''): void {
    error.value = next
    serverText.value = message
    errorHint.value = hint
    clearTimeout(errorTimer)
    if (next) errorTimer = setTimeout(() => (error.value = ''), 6000)
  }

  /** 麦克风流必须显式停掉：它不停，浏览器的录音指示灯就一直亮着。 */
  function releaseMic(): void {
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
    clearTimeout(capTimer)
    capTimer = undefined
  }

  function teardown(): void {
    if (rec) {
      rec.onresult = null
      rec.onerror = null
      rec.onend = null
      rec = null
    }
    if (media) {
      media.ondataavailable = null
      media.onstop = null
      media = null
    }
    chunks = []
    releaseMic()
  }

  /** 收到一段确定的文字（或收尾时剩下半句草稿）：并进正文，草稿清零。 */
  function commit(text: string): void {
    base = join(base, text)
    interim.value = ''
  }

  // -- 路 A：浏览器自带的 Web Speech -----------------------------------------------------

  function startBrowser(): void {
    const Klass = recognizerClass()
    if (!Klass) {
      showError('unsupported')
      return
    }

    rec = new Klass()
    rec.lang = 'zh-CN'
    rec.continuous = true
    rec.interimResults = true
    rec.maxAlternatives = 1

    rec.onresult = (ev) => {
      // resultIndex 之后才是本次新增；一次事件可能夹多条，逐条吃完。
      for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
        const result = ev.results[i]
        const text = result[0]?.transcript ?? ''
        if (result.isFinal) commit(text)
        else interim.value = text
      }
      render()
    }
    rec.onerror = (ev) => {
      const kind = ev.error as string
      if (kind === 'no-speech') showError('no-speech')
      else if (kind === 'not-allowed' || kind === 'service-not-allowed') showError('denied')
      else if (kind === 'audio-capture') showError('no-mic')
      else if (kind === 'network') showError('network')
      else if (kind !== 'aborted') showError('failed')
      // 报完这一发就不会再有结果，而 Chrome 常常也不补 onend（start 阶段就报 network 时最典型）。
      // 不等它：否则 listening 永远挂在 true，面板停在「正在听写」、输入框只读，
      // 那句「输入框仍然可以用」就成了假话。aborted / failed 仍交给 onend，那条路可能有半句尾巴要留。
      if (NO_RECOVERY.has(kind)) {
        interim.value = ''
        render()
        listening.value = false
        teardown()
      }
    }
    rec.onend = () => {
      // 引擎自己收尾时可能只剩半句草稿（没等到 isFinal）：留下它比丢掉好，用户能删。
      commit(interim.value)
      render()
      listening.value = false
      teardown()
    }

    try {
      // Safari 的 start() 返回 Promise，权限被拒是 reject 而不是 onerror。
      const started = rec.start()
      if (started && typeof (started as Promise<void>).catch === 'function') {
        ;(started as Promise<void>).catch(() => {
          showError('denied')
          listening.value = false
          teardown()
        })
      }
      listening.value = true
    } catch {
      showError('failed')
      listening.value = false
      teardown()
    }
  }

  /** 浏览器那条用 stop() 而不是 abort()：前者会等引擎把最后半句判完，后者直接丢掉尾巴。 */
  function stopBrowser(): void {
    if (!rec) {
      listening.value = false
      return
    }
    try {
      rec.stop()
    } catch {
      listening.value = false
      teardown()
    }
  }

  // -- 路 B：录一段音发给后端转写 ---------------------------------------------------------

  async function startServer(tripId: string): Promise<void> {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      showError(micErrorOf(err))
      listening.value = false
      releaseMic()
      return
    }
    // 授权框挂着的这几秒里，用户可能已经关了面板，或者又点了一次话筒。
    if (!listening.value) {
      releaseMic()
      return
    }

    const mime = mimeForRecording()
    chunks = []
    try {
      media = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    } catch {
      showError('failed')
      listening.value = false
      releaseMic()
      return
    }
    media.ondataavailable = (ev) => {
      if (ev.data.size) chunks.push(ev.data)
    }
    media.onstop = () => void upload(tripId)
    // timeslice 只是让大块分成几片收，别让最后一下一次性占内存。
    media.start(1000)

    const limit = Math.max(5, options.maxSeconds?.() ?? 90)
    capTimer = setTimeout(() => {
      if (listening.value) stopRecording()
    }, limit * 1000)
  }

  async function upload(tripId: string): Promise<void> {
    const recorded = new Blob(chunks, { type: media?.mimeType || mimeForRecording() })
    teardown()
    if (!recorded.size) {
      showError('no-speech')
      return
    }
    const form = new FormData()
    form.append('file', recorded, `speech.${suffixOf(recorded.type)}`)
    busy.value = true
    try {
      const reply = await apiFetch<{ text?: string }>(`/api/trips/${tripId}/assistant/speech`, {
        method: 'POST',
        body: form,
      })
      const heard = (reply?.text ?? '').trim()
      if (heard) commit(heard)
      else showError('no-speech')
    } catch (err) {
      const e = err as ApiError
      showError('server', e?.message || '', e?.hint || '')
    } finally {
      render()
      busy.value = false
    }
  }

  function stopRecording(): void {
    if (!media) {
      listening.value = false
      return
    }
    try {
      media.stop()
    } catch {
      listening.value = false
      teardown()
    }
  }

  // -- 共用出口 -------------------------------------------------------------------------

  async function start(): Promise<void> {
    if (listening.value || busy.value) return
    base = target.value
    interim.value = ''
    showError('')

    const tripId = options.tripId?.() ?? ''
    if (options.serverReady?.() && recorderAvailable()) {
      if (!tripId) {
        showError('failed')
        return
      }
      listening.value = true
      await startServer(tripId)
      return
    }
    if (!recognizerClass()) {
      listening.value = false
      showError('unsupported')
      return
    }
    listening.value = true
    startBrowser()
  }

  function stop(): void {
    if (!listening.value) return
    listening.value = false
    if (media) stopRecording()
    else stopBrowser()
  }

  function toggle(): void {
    if (listening.value) stop()
    else void start()
  }

  watch(interim, render)
  onBeforeUnmount(() => {
    try {
      rec?.abort()
    } catch {
      /* 已经停了 */
    }
    try {
      if (media?.state === 'recording') media.stop()
    } catch {
      /* 已经停了 */
    }
    teardown()
    clearTimeout(errorTimer)
  })

  const errorText = computed(() => {
    if (!error.value) return ''
    return serverText.value || ERROR_TEXT[error.value]
  })

  return {
    supported,
    listening,
    busy,
    mode,
    error,
    errorText,
    errorHint,
    stop,
    toggle,
  }
}
