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

export function getClientId(): string {
  let id = sessionStorage.getItem(ID_KEY)
  if (!id) {
    id = randomId()
    sessionStorage.setItem(ID_KEY, id)
  }
  return id
}

export function getClientName(): string {
  let name = sessionStorage.getItem(NAME_KEY)
  if (!name) {
    name = randomName()
    sessionStorage.setItem(NAME_KEY, name)
  }
  return name
}

export function setClientName(name: string): void {
  sessionStorage.setItem(NAME_KEY, name.trim() || randomName())
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
