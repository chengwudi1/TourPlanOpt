import { defineStore } from 'pinia'
import { ref } from 'vue'

import type { ClientFrame, OpSendFrame, PresenceSendFrame, ServerFrame } from '@/types/protocol'
import { ClientMsg, PROTOCOL_VERSION, ServerMsg } from '@/types/protocol'
import { colorForClient, useClientIdentity } from '@/composables/useClientIdentity'
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
  const lastError = ref<{ message: string; hint: string } | null>(null)

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
        status.value = 'idle'
        lastError.value = { message: '行程不存在', hint: '检查链接里的行程 ID。' }
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
        lastError.value = { message: frame.message, hint: frame.hint ?? '' }
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
    connect,
    disconnect,
    sendOp,
    sendPresence,
  }
})
