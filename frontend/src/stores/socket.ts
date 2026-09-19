import { acceptHMRUpdate, defineStore } from 'pinia'
import { ref } from 'vue'

import type { ClientFrame, OpSendFrame, PresenceSendFrame, ServerFrame } from '@/types/protocol'
import { ClientMsg, PROTOCOL_VERSION, ServerMsg } from '@/types/protocol'
import { colorForClient, useClientIdentity } from '@/composables/useClientIdentity'
import { useFeedbackStore } from '@/stores/feedback'
import { useTripStore } from '@/stores/trip'

export type SocketStatus = 'idle' | 'connecting' | 'online' | 'reconnecting'

const HEARTBEAT_MS = 25_000
const MAX_QUEUE = 50

/**
 * The WebSocket lifecycle: hello on open, heartbeat, reconnect with jittered backoff,
 * and routing of server frames into the trip store. All mutation *semantics* (optimistic
 * apply, echo filtering, rev guards) live in the trip store -- this module only moves
 * bytes and owns connection state.
 */
export const useSocketStore = defineStore('socket', () => {
  const status = ref<SocketStatus>('idle')
  /** 连接被服务端拒了一次：可以重试，走回执那条 danger toast。 */
  const lastError = ref<{ message: string; hint: string } | null>(null)
  /** 这一屏已经没有归宿了（行程不存在）：整栏给人话兜底，不是九秒后就消失的 toast。 */
  const fatalError = ref<{ message: string; hint: string } | null>(null)

  let ws: WebSocket | null = null
  let tripId = ''
  let attempt = 0
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let closedByUs = false

  /** Ops sent while offline. Server dedupes on op_id, so a flush after reconnect is
   * safe even if some of these actually made it out before the socket died. */
  const pendingQueue: OpSendFrame[] = []

  function connect(id: string) {
    if (tripId === id && (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) {
      return
    }
    tripId = id
    closedByUs = false
    fatalError.value = null
    openSocket()
  }

  function openSocket() {
    teardownTimers()
    status.value = attempt === 0 ? 'connecting' : 'reconnecting'

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${window.location.host}/ws/trips/${encodeURIComponent(tripId)}`)

    ws.onopen = () => {
      const identity = useClientIdentity()
      send({
        v: PROTOCOL_VERSION,
        type: ClientMsg.HELLO,
        data: {
          client_id: identity.client_id,
          name: identity.name,
          color: colorForClient(identity.client_id),
        },
      })
      heartbeatTimer = setInterval(() => send({ v: PROTOCOL_VERSION, type: ClientMsg.PING }), HEARTBEAT_MS)
    }

    ws.onmessage = (event: MessageEvent) => {
      let frame: ServerFrame
      try {
        frame = JSON.parse(event.data as string)
      } catch {
        return
      }
      handleFrame(frame)
    }

    ws.onclose = (event: CloseEvent) => {
      teardownTimers()
      ws = null
      if (closedByUs) {
        status.value = 'idle'
        return
      }
      if (event.code === 4404) {
        // 重连一个不存在的房间只会一圈圈转下去：这一屏到此为止，交给整页兜底。
        closedByUs = true
        status.value = 'idle'
        tripGone()
        return
      }
      scheduleReconnect()
    }

    ws.onerror = () => {
      // onclose always follows onerror; backoff is scheduled there.
    }
  }

  function scheduleReconnect() {
    status.value = 'reconnecting'
    const base = Math.min(8000, 500 * 2 ** attempt)
    // ±20% jitter so a room full of clients does not reconnect in lockstep and stampede.
    const delay = Math.round(base * (0.8 + Math.random() * 0.4))
    attempt += 1
    reconnectTimer = setTimeout(() => openSocket(), delay)
  }

  /** Background tabs get their timers throttled to once a minute by Chrome, so the
   * backoff loop alone can leave a hidden tab "重连中" long after the backend is back.
   * The moment the user LOOKS at the tab (or the network returns), retry immediately. */
  function reconnectSoon() {
    if (!tripId || closedByUs) return
    if (ws && ws.readyState === WebSocket.OPEN) return
    teardownTimers()
    openSocket()
  }

  /** 「行程不存在」：这一屏没有归宿了，重连也连不出一个房间来。 */
  function tripGone() {
    teardownTimers()
    const sock = ws
    ws = null
    if (sock) {
      sock.onclose = null
      sock.onmessage = null
      sock.onerror = null
      sock.onopen = null
      try {
        sock.close()
      } catch {
        /* 已经关了 */
      }
    }
    fatalError.value = {
      message: '这份行程已经打不开了',
      hint: '它可能刚被同伴删掉，或链接里的行程编号不对。',
    }
  }

  /** 回执上的「重试」：拆掉当前这条连接重来一次。reconnectSoon 在 OPEN 状态下原地不动，所以不复用它。 */
  function reconnectForce() {
    if (!tripId) return
    closedByUs = false
    lastError.value = null
    const sock = ws
    ws = null
    if (sock) {
      sock.onclose = null
      sock.onmessage = null
      sock.onerror = null
      sock.onopen = null
      try {
        sock.close()
      } catch {
        /* 已经关了 */
      }
    }
    attempt = 0
    openSocket()
  }

  /**
   * 服务端在连接里报的问题：过去它写进 `lastError` 就没人读，用户只看到屏幕悄悄不可信。
   *
   * 原始那句是协议层的自述（「请先发送 hello」一类），上屏只会成谜——它进控制台，
   * 界面留一句人话和一次重来的机会。
   */
  function reportServerError(message: string, hint: string) {
    if (message.includes('这份行程不存在')) {
      closedByUs = true
      status.value = 'idle'
      tripGone()
      return
    }
    lastError.value = { message, hint }
    console.warn('[socket] 服务端报错：', message, hint)
    useFeedbackStore().show({
      kind: 'danger',
      message: '这一步没有同步出去',
      hint: '与房间的对接出了岔子，可以立刻重来一次。',
      action: { label: '重试', run: reconnectForce },
    })
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') reconnectSoon()
    })
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('online', reconnectSoon)
  }

  function teardownTimers() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function disconnect() {
    closedByUs = true
    teardownTimers()
    ws?.close()
    ws = null
    status.value = 'idle'
  }

  function send(frame: ClientFrame): boolean {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame))
      return true
    }
    return false
  }

  /** Send a mutation op. Returns the op_id (caller may key optimistic state by it).
   * When offline the frame is queued and flushed after hello -- the server's op_id LRU
   * makes double delivery harmless. */
  function sendOp(op: string, data: Record<string, unknown>): string {
    const op_id = crypto.randomUUID()
    const frame: OpSendFrame = { v: PROTOCOL_VERSION, type: ClientMsg.OP, op, op_id, data }
    if (!send(frame)) {
      pendingQueue.push(frame)
      if (pendingQueue.length > MAX_QUEUE) pendingQueue.shift()
    }
    return op_id
  }

  function flushQueue() {
    while (pendingQueue.length && ws && ws.readyState === WebSocket.OPEN) {
      const frame = pendingQueue.shift()
      if (frame) ws.send(JSON.stringify(frame))
    }
  }

  // -- presence ----------------------------------------------------------------------

  let lastPresenceSent = 0
  let presenceTimer: ReturnType<typeof setTimeout> | null = null
  let latestPresence: PresenceSendFrame['data'] | null = null

  /** Coalesced to one frame per 250 ms server-side; we pre-coalesce here so a burst of
   * selection changes does not even reach the socket. `draggingDayId` tells the room
   * someone is mid-drag so their lists can show a gentle hint. */
  function sendPresence(
    currentDayId: string | null,
    focusingPlaceId: string | null,
    draggingDayId?: string | null,
  ) {
    latestPresence = {
      current_day_id: currentDayId,
      focusing_place_id: focusingPlaceId,
      dragging_day_id: draggingDayId ?? null,
    }
    if (presenceTimer) return
    const elapsed = Date.now() - lastPresenceSent
    const wait = Math.max(0, 250 - elapsed)
    presenceTimer = setTimeout(() => {
      presenceTimer = null
      lastPresenceSent = Date.now()
      if (latestPresence) send({ v: PROTOCOL_VERSION, type: ClientMsg.PRESENCE, data: latestPresence })
    }, wait)
  }

  // -- inbound frames ------------------------------------------------------------------

  function handleFrame(frame: ServerFrame) {
    const trip = useTripStore()

    switch (frame.type) {
      case ServerMsg.WELCOME: {
        attempt = 0
        status.value = 'online'
        lastError.value = null
        fatalError.value = null
        trip.applySnapshot(frame.data.snapshot as never)
        trip.clearPendingOps()
        flushQueue()
        break
      }
      case ServerMsg.OP: {
        trip.applyRemoteOp(frame)
        break
      }
      case ServerMsg.OP_REJECT: {
        trip.applyReject(frame.op_id, frame.reason, frame.data)
        break
      }
      case ServerMsg.PRESENCE_JOIN:
      case ServerMsg.PRESENCE_UPDATE: {
        trip.applyPresence(frame.data as never)
        break
      }
      case ServerMsg.PRESENCE_LEAVE: {
        trip.removePresence(String((frame.data as { client_id?: string }).client_id ?? ''))
        break
      }
      case ServerMsg.ERROR: {
        reportServerError(String(frame.message ?? ''), String(frame.hint ?? ''))
        break
      }
      case 'pong':
        break
      default:
        break
    }
  }

  return {
    status,
    lastError,
    fatalError,
    connect,
    disconnect,
    sendOp,
    sendPresence,
  }
})

if (import.meta.hot) {
  acceptHMRUpdate(useSocketStore, import.meta.hot)
}
