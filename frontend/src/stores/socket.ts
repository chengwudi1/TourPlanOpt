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
  /** 已经发出去、服务端还没认账的 op。限流拒回时只有拿得回这一帧，才谈得上重发。 */
  const inFlight = new Map<string, OpSendFrame>()
  // 服务端普通消息上限是 30 条/10 秒。一次倾巢而出的补发会把它撞穿：第 30 条之后全部
  // 被拒，而那些帧已经出队、不会再发——用户看到的是「我改的没了，也没人说过为什么」。
  const FLUSH_BURST = 25
  const FLUSH_PAUSE_MS = 10_500
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  function connect(id: string) {
    if (tripId === id && (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) {
      return
    }
    // 换房间：先把旧 ws 的 handler 逐个摘掉再关，否则它的 onclose 会在新连接已建好后
    // 把 ws 置 null 并重新起一条重连链，旧房间的 op 也会一路写进当前这份 store。
    dropSocket(ws)
    ws = null
    // 队列是「为那个房间没送出去的话」。换了房间它们就成了写给别人的指令：welcome 一落地
    // flushQueue 会把 A 的 day_add / checklist_add / message_add / trip_update 原样发进 B，
    // 而服务端按 conn.trip_id 落库——改的是 B 这一趟行程。旧房间的待发一律作废。
    if (tripId !== id) {
      pendingQueue.length = 0
      inFlight.clear()
      // 挡回来还没被人接走的那句话同属旧房间：新行程的聊天框把它捞回来的话，
      // 人以为自己在接着 A 说，发出去改的却是 B。
      useTripStore().chatBounced = null
    }
    tripId = id
    closedByUs = false
    fatalError.value = null
    attempt = 0
    openSocket()
  }

  /** 拆掉一条连接的全部钩子再关它。少了「先摘钩」这一步，它的 onclose 会在新连接已经
   *  建好之后把 ws 置空、再起重连链——两条连接互相顶号，每一轮都重取快照抹用户的输入。 */
  function dropSocket(sock: WebSocket | null) {
    if (!sock) return
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

  function openSocket() {
    teardownTimers()
    if (ws) {
      const stale = ws
      ws = null
      dropSocket(stale)
    }
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
      // 只处理「我这一条」的关闭：钩子没摘干净的旧连接如果把 ws 置空，会把刚建好的那条
      // 一起带走（心跳定时器也被它清掉），于是房间里的自己凭空消失。
      if (event.target !== ws) return
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
    // CONNECTING 也算「已经在连了」：visibilitychange 与 online 常常同一拍到达（手机回
    // 前台正好网络恢复），只挡 OPEN 就会在同一拍里再造一条 socket。
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
    teardownTimers()
    openSocket()
  }

  /** 「行程不存在」：这一屏没有归宿了，重连也连不出一个房间来。 */
  function tripGone() {
    teardownTimers()
    const sock = ws
    ws = null
    dropSocket(sock)
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
    dropSocket(sock)
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
    // 补发的下一片挂在定时器上：连接一断就停掉，等下一次 welcome 再接着发（队列还在）。
    if (flushTimer) {
      clearTimeout(flushTimer)
      flushTimer = null
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
    } else {
      inFlight.set(op_id, frame)
    }
    return op_id
  }

  /** 分片补发：一次最多 FLUSH_BURST 条，剩下的按限流窗口排下去，绝不一次性倾巢。 */
  function flushQueue() {
    if (flushTimer) return
    const burst = pendingQueue.splice(0, FLUSH_BURST)
    for (let k = 0; k < burst.length; k += 1) {
      const frame = burst[k]
      if (!send(frame)) {
        // 又断了：把没发出去的原样退回队首，不许凭空蒸发。
        pendingQueue.unshift(...burst.slice(k))
        break
      }
      inFlight.set(frame.op_id, frame)
    }
    if (pendingQueue.length) {
      flushTimer = setTimeout(() => {
        flushTimer = null
        flushQueue()
      }, FLUSH_PAUSE_MS)
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
        // 重连的 hello 只带名字与颜色，服务端会把在场的「哪天哪站」清空。不补这一发，
        // 同伴地图上的头像就一路消失，直到本人再点一次别的东西才回来——而他什么都没做。
        if (latestPresence) {
          lastPresenceSent = Date.now()
          send({ v: PROTOCOL_VERSION, type: ClientMsg.PRESENCE, data: latestPresence })
        }
        break
      }
      case ServerMsg.OP: {
        inFlight.delete(String(frame.op_id ?? ''))
        trip.applyRemoteOp(frame)
        break
      }
      case ServerMsg.OP_REJECT: {
        const op_id = String(frame.op_id ?? '')
        if (frame.reason === 'rate_limited') {
          // 服务端只是嫌快，不是不认这笔改动：原样塞回队列，等窗口过去再说。
          // 立刻 flushQueue() 会变成紧循环——被拒一帧就重发一帧，帧本身也在刷新服务端的
          // 限流窗口，10 秒能把连接打成几千帧的忙等。等一个窗口再发。
          const queued = inFlight.get(op_id)
          inFlight.delete(op_id)
          if (queued) {
            pendingQueue.unshift(queued)
            if (!flushTimer) {
              flushTimer = setTimeout(() => {
                flushTimer = null
                flushQueue()
              }, FLUSH_PAUSE_MS)
            }
            break
          }
        }
        inFlight.delete(op_id)
        trip.applyReject(op_id, frame.reason, frame.data)
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
    /** 乐观写被拒且无权威回数组时，重连重取快照把本地收敛回服务端真相——统一回滚出口。 */
    resync: reconnectForce,
  }
})

if (import.meta.hot) {
  acceptHMRUpdate(useSocketStore, import.meta.hot)
}
