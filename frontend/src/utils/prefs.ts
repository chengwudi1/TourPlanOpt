/**
 * M31 个人偏好的纯逻辑：类型、默认值、本机读写、以及「偏好 → 界面」的解析算式。
 *
 * 刻意不依赖 Vue，也没有任何响应式：index.html 里那段防闪内联脚本必须在应用挂载之前
 * 就把 data-theme / --fs-scale / data-motion 写到 <html> 上，而它在文档解析阶段跑，
 * 拿不到模块。所以它自带一份同样的算式，那份以本文件为准 —— 改这里必须同时改
 * index.html。两处不一致的症状是「刷新时闪一下」，而防闪正是这段代码存在的唯一理由，
 * docs/_theme-probe.mjs 第 1 条判据就是盯这个的。
 *
 * 为什么偏好只存本机 + users.prefs，绝不走 op、绝不进 trips：深色是我自己的界面，
 * 我一翻深色同行的人跟着翻，那是 bug 不是功能（同 [[chat-feature-decisions]] 的边界）。
 *
 * 存两个键是有意的：`prefs` 是这台设备当前生效的那份（匿名也能用），`prefs_synced`
 * 是「上一次和服务端对齐时的内容」。有了后者才能逐键判断哪些是本机真正改过的，
 * 换设备登录时才不会把账号上已有的偏好当成没改过。
 */

export type ThemePref = 'auto' | 'light' | 'dark'
export type FontSizePref = 'md' | 'lg' | 'xl'
export type MotionPref = 'auto' | 'reduce'
export type BasemapPref = 'auto' | 'light'

export interface Prefs {
  theme: ThemePref
  font_size: FontSizePref
  motion: MotionPref
  basemap: BasemapPref
  pet_visible: boolean
}

export const PREFS_KEY = 'tourplanopt.prefs'
export const SYNCED_KEY = 'tourplanopt.prefs_synced'

export const DEFAULT_PREFS: Prefs = {
  theme: 'auto',
  font_size: 'md',
  motion: 'auto',
  basemap: 'auto',
  pet_visible: true,
}

const THEME_VALUES: readonly string[] = ['auto', 'light', 'dark']
const FONT_VALUES: readonly string[] = ['md', 'lg', 'xl']
const MOTION_VALUES: readonly string[] = ['auto', 'reduce']
const BASEMAP_VALUES: readonly string[] = ['auto', 'light']

function pick<T extends string>(allowed: readonly string[], value: unknown, fallback: T): T {
  return typeof value === 'string' && allowed.includes(value) ? (value as T) : fallback
}

/** 后端只存「用户改过的键」，所以这里稀疏读、逐项兜默认值；认不下的值同样兜回去，
 *  而不是让一个手改坏掉的 localStorage 把整块偏好废掉。 */
export function sanitizePrefs(raw: unknown): Prefs {
  const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  return {
    theme: pick(THEME_VALUES, o.theme, DEFAULT_PREFS.theme),
    font_size: pick(FONT_VALUES, o.font_size, DEFAULT_PREFS.font_size),
    motion: pick(MOTION_VALUES, o.motion, DEFAULT_PREFS.motion),
    basemap: pick(BASEMAP_VALUES, o.basemap, DEFAULT_PREFS.basemap),
    pet_visible: typeof o.pet_visible === 'boolean' ? o.pet_visible : DEFAULT_PREFS.pet_visible,
  }
}

/** 这台设备真正改过的键：与「上次对齐的那份」逐项比。
 *
 *  服务端那份是稀疏的（只存用户改过的键），所以「云端没有这个键」要按默认值来比——
 *  否则一个从没动过字号的设备，登录瞬间会把自己的默认字号当成改动推上去，把账号在
 *  别的设备上调大的那一档抹回默认。这正是这条判据要挡的事故。 */
export function dirtyKeys(prefs: Prefs, synced: Record<string, unknown> | null): (keyof Prefs)[] {
  const base = sanitizePrefs(synced ?? {})
  return (Object.keys(DEFAULT_PREFS) as (keyof Prefs)[]).filter(
    (key) => prefs[key] !== base[key],
  )
}

export type SparsePrefs = Partial<Prefs>

/** 逐键写时要过一次的形状：Prefs 是接口、没有索引签名，联合键又写不回去。 */
type Slots = Record<string, unknown>

const PREF_KEYS = Object.keys(DEFAULT_PREFS) as (keyof Prefs)[]

export interface Reconciled {
  prefs: Prefs
  /** 要推给服务端的键：本机相对上次对齐真正改过的那些。一次都没改过就是空对象，
   *  调用方据此跳过那次 PUT。 */
  push: SparsePrefs
}

/** 本机相对上次对齐真正改过的键，连同取值。空对象就是「没什么要推的」。 */
export function dirtyPayload(local: Prefs, synced: Record<string, unknown> | null): SparsePrefs {
  const push: SparsePrefs = {}
  for (const key of dirtyKeys(local, synced)) (push as unknown as Slots)[key] = local[key]
  return push
}

/** 本机的、云端的、上次对齐的，三份放在一起决定每个键听谁。
 *
 *  规则只有一条：**谁改过听谁的，都改过听本机**。本机改过的键（与上次对齐不同）本机赢；
 *  本机没改过的键说明这台设备从没动过它，云端那份（可能是另一台设备调的）才是新的。
 *  没有「上次对齐」这份快照就退化成「以默认值判断改没改过」，也就是首次登录：
 *  云端存着的照单收下，本机唯一算改动的会被推上去。 */
export function reconcile(
  local: Prefs,
  server: SparsePrefs,
  synced: Record<string, unknown> | null,
): Reconciled {
  const base = sanitizePrefs(synced ?? {})
  const next: Prefs = { ...local }
  // 联合键写不回去（取值会塌成 never），和 dirtyPayload 一样过一道 Record。
  const target = next as unknown as Slots
  for (const key of PREF_KEYS) {
    if (local[key] !== base[key]) continue
    const remote = server[key]
    if (remote !== undefined) target[key] = remote
  }
  return { prefs: next, push: dirtyPayload(local, synced) }
}

function readJson(key: string): unknown {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    // 隐私模式下 localStorage 会抛，坏 JSON 也会抛：一份读不出来的偏好就是默认偏好。
    return null
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 存不下只是下次开机回到默认值，不该把设置面板本身弄崩。
  }
}

export function readPrefs(): Prefs {
  return sanitizePrefs(readJson(PREFS_KEY))
}

export function writePrefs(prefs: Prefs): void {
  writeJson(PREFS_KEY, prefs)
}

/** 上次与服务端对齐的那一份，按账号存。
 *
 *  为什么不是一份裸快照：换账号登录时，A 留下的快照会被拿去判断「这台设备真正改过哪些
 *  键」，于是 B 账号在云端早就存好的偏好会被当成 B 本机没改过、A 本机改过，反手覆盖回去。
 *  对不上账号就当从没对齐过（返回 null），首次登录因此以服务端为准。 */
export interface SyncedPrefs {
  user_id: string
  prefs: Record<string, unknown>
}

export function readSynced(userId: string): Record<string, unknown> | null {
  const raw = readJson(SYNCED_KEY) as SyncedPrefs | null
  if (!raw || typeof raw !== 'object' || raw.user_id !== userId) return null
  return raw.prefs && typeof raw.prefs === 'object' ? raw.prefs : {}
}

export function writeSynced(userId: string, prefs: Record<string, unknown>): void {
  writeJson(SYNCED_KEY, { user_id: userId, prefs })
}

// ---------- 偏好 → 界面 ----------

/** 字号三档的缩放因子。用倍率而不是「+1px / +2px」：正文 15px 与 hero 52px 差三倍多，
 *  同一个加数在小字上是放大、在大字上是没变，而乘法在两端都只是「大一点点」。 */
export const FONT_SCALES: Record<FontSizePref, string> = {
  md: '1',
  lg: '1.08',
  xl: '1.16',
}

export interface MediaState {
  systemDark: boolean
  systemReduce: boolean
}

export type ResolvedTheme = 'light' | 'dark'

export function resolveTheme(pref: ThemePref, systemDark: boolean): ResolvedTheme {
  if (pref === 'auto') return systemDark ? 'dark' : 'light'
  return pref
}

export function resolveReduceMotion(pref: MotionPref, systemReduce: boolean): boolean {
  return pref === 'auto' ? systemReduce : pref === 'reduce'
}

/** 底图要不要翻成夜航图。单一真源在这里：MapPanel 只读这一个判断，别自己再看一遍
 *  主题令牌——「界面是深色」和「底图是深色」在用户能强制亮底图之后就是两件事了。 */
export function resolveBasemapDark(prefs: Prefs, theme: ResolvedTheme): boolean {
  if (prefs.basemap === 'light') return false
  return theme === 'dark'
}

/** 把三个解析值写到 <html> 上。CSS 与 JS 都只认这些已解析值，不再有第二处真源。 */
export function applyPrefs(prefs: Prefs, media: MediaState): void {
  const root = document.documentElement
  root.dataset.theme = resolveTheme(prefs.theme, media.systemDark)
  root.style.setProperty('--fs-scale', FONT_SCALES[prefs.font_size])
  root.dataset.motion = resolveReduceMotion(prefs.motion, media.systemReduce) ? 'off' : 'on'
}
