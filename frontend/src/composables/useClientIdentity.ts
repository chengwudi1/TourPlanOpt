/**
 * Who am I in this browser.
 *
 * `client_id` lives in sessionStorage, NOT localStorage: localStorage is shared across
 * every tab of one browser profile, so two test windows would collapse into a single
 * participant and the whole collaboration story becomes untestable. A new tab is a new
 * participant -- that is the semantics we want both for testing and for "open the share
 * link on my phone".
 *
 * The display name is asked once per tab (M3's JoinGate replaces the prompt with a real
 * welcome screen) and kept in sessionStorage so a reload keeps your identity.
 */

const ID_KEY = 'tourplanopt.client_id'
const NAME_KEY = 'tourplanopt.name'

const ADJECTIVES = ['敏捷的', '好奇的', '快乐的', '沉着的', '灵光的', '靠谱的']
const ANIMALS = ['旅行家', '向导', '摄影师', '船长', '美食家', '探险家']

export interface ClientIdentity {
  client_id: string
  name: string
}

function randomId(): string {
  const bytes = new Uint8Array(8)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function randomName(): string {
  const a = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)]
  const b = ANIMALS[Math.floor(Math.random() * ANIMALS.length)]
  return `${a}${b}`
}

// Safari 无痕模式下 sessionStorage 的读写会抛 SecurityError。裸调用会在 onopen 里抛出，
// 连 HELLO 都发不出去、永远连不上房间。退一步：可用就持久化，抛错就用本次页面加载内的
// 内存兜底（重新加载会换个新访客，也远比整页连不上好）。
const memoryStore = new Map<string, string>()

export function sessionGet(key: string): string | null {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return memoryStore.get(key) ?? null
  }
}

export function sessionSet(key: string, value: string): void {
  memoryStore.set(key, value)
  try {
    sessionStorage.setItem(key, value)
  } catch {
    /* 无痕模式：只在本页面加载内保住身份，不崩。 */
  }
}

function sGet(key: string): string | null {
  return sessionGet(key)
}

function sSet(key: string, value: string): void {
  sessionSet(key, value)
}

export function getClientId(): string {
  let id = sGet(ID_KEY)
  if (!id) {
    id = randomId()
    sSet(ID_KEY, id)
  }
  return id
}

export function getClientName(): string {
  let name = sGet(NAME_KEY)
  if (!name) {
    name = randomName()
    sSet(NAME_KEY, name)
  }
  return name
}

export function setClientName(name: string): void {
  sSet(NAME_KEY, name.trim() || randomName())
}

/** Stable per-tab identity, safe to call anywhere. */
export function useClientIdentity(): ClientIdentity {
  return { client_id: getClientId(), name: getClientName() }
}

/** Participant colours come from the `--warp-*` tokens in main.css, not a second
 *  hardcoded list -- the previous one drifted, and still carried a `#2f6feb` the
 *  audit had already retired. Resolved lazily because map markers and the share-card
 *  canvas both need a real hex string. Those eight tokens are identity colours and
 *  are deliberately not overridden in the dark theme, so this cache cannot go stale. */
const WARP_SLOTS = 8
let warpCache: string[] | null = null

function warpPalette(): string[] {
  if (!warpCache) {
    const cs = getComputedStyle(document.documentElement)
    warpCache = Array.from({ length: WARP_SLOTS }, (_, i) =>
      cs.getPropertyValue(`--warp-${i + 1}`).trim(),
    )
  }
  return warpCache
}

export function colorForClient(clientId: string): string {
  let hash = 0
  for (let i = 0; i < clientId.length; i += 1) {
    hash = (hash * 31 + clientId.charCodeAt(i)) >>> 0
  }
  const palette = warpPalette()
  return palette[hash % palette.length]
}
