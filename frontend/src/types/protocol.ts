/**
 * Hand-mirror of backend/app/models/protocol.py. tests/test_protocol.py asserts every
 * op literal defined in Python appears in this file, so the two cannot drift silently.
 */

export const PROTOCOL_VERSION = 1

export const ClientMsg = {
  HELLO: 'hello',
  OP: 'op',
  PRESENCE: 'presence',
  RESYNC: 'resync',
  PING: 'ping',
} as const

export const ServerMsg = {
  WELCOME: 'welcome',
  OP: 'op',
  OP_REJECT: 'op_reject',
  PRESENCE_JOIN: 'presence_join',
  PRESENCE_LEAVE: 'presence_leave',
  PRESENCE_UPDATE: 'presence_update',
  ERROR: 'error',
  PONG: 'pong',
} as const

/** Past tense: server frames announce an applied result, never an intent. */
export const Ops = {
  PLACE_ADD: 'place_add',
  PLACE_UPDATE: 'place_update',
  PLACE_DELETE: 'place_delete',
  PLACE_LOCK: 'place_lock',
  DAY_REORDER: 'day_reorder',
  DAY_ADD: 'day_add',
  DAY_UPDATE: 'day_update',
  TRIP_UPDATE: 'trip_update',
} as const

export interface HelloFrame {
  v: number
  type: 'hello'
  data: { client_id: string; name: string; color: string }
}

export interface OpSendFrame {
  v: number
  type: 'op'
  op: string
  op_id: string
  data: Record<string, unknown>
}

export interface PresenceSendFrame {
  v: number
  type: 'presence'
  data: { current_day_id: string | null; focusing_place_id: string | null; dragging_day_id?: string | null }
}

export interface ResyncFrame {
  v: number
  type: 'resync'
  data: { last_seq: number }
}

export type ClientFrame = HelloFrame | OpSendFrame | PresenceSendFrame | ResyncFrame | { v: number; type: 'ping' }

export interface WelcomeFrame {
  v: number
  type: 'welcome'
  seq: number
  data: { you: unknown; snapshot: unknown }
}

export interface OpBroadcastFrame {
  v: number
  type: 'op'
  seq: number
  op: string
  origin: string
  op_id: string
  ts: number
  data: Record<string, unknown>
}

export interface OpRejectFrame {
  v: number
  type: 'op_reject'
  op_id: string
  reason: string
  data: Record<string, unknown>
}

export interface PresenceBroadcastFrame {
  v: number
  type: 'presence_join' | 'presence_leave' | 'presence_update'
  seq: number
  data: Record<string, unknown>
}

export interface ErrorFrame {
  v: number
  type: 'error'
  message: string
  hint: string
}

export type ServerFrame = WelcomeFrame | OpBroadcastFrame | OpRejectFrame | PresenceBroadcastFrame | ErrorFrame | { v: number; type: 'pong'; ts: number }
