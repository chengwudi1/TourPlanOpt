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

/** Participant colours, assigned round-robin so two people rarely collide. */
const PALETTE = [
  '#2f6feb', // blue
  '#d64545', // red
  '#1f9d55', // green
  '#b7791f', // amber
  '#7c3aed', // violet
  '#0e7490', // teal
  '#db2777', // pink
  '#4d7c0f', // olive
]

export function colorForClient(clientId: string): string {
  let hash = 0
  for (let i = 0; i < clientId.length; i += 1) {
    hash = (hash * 31 + clientId.charCodeAt(i)) >>> 0
  }
  return PALETTE[hash % PALETTE.length]
}
