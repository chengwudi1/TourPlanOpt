/**
 * 最近打开过的行程：本机一份，跨标签页共享。
 *
 * 首页要做「我的旅行」仪表盘，但账号是可选的：`GET /api/auth/trips` 没登录就 401，
 * 匿名访客此前创建/打开过的行程在后端根本没有一份可列的清单。所以这份索引只解决
 * 「我最近看过哪几个」，行程内容一律回服务端取（见 fetchTripSummaries）。
 *
 * 存 localStorage 而不是 sessionStorage，是和 [[useClientIdentity]] 反着来的：身份必须
 * 每标签页独立，否则两个测试窗口会塌成一个协作成员；而浏览记录属于「这台设备上的这个
 * 人」，新开一个标签页当然还看得到自己刚建的行程。
 *
 * 只存 id 和时间戳，不存标题/城市——那些是服务端的事实，缓存下来迟早和真实数据打架。
 *
 * 同文件还管一份「已从首页移除」黑名单（`hideTrip`）：列表有两个来源，服务端那份删不动，
 * 所以「移除」只能是这台设备上的过滤规则，而不是删除动作。
 */

import { ref } from 'vue'

const RECENT_KEY = 'tourplanopt.recent_trips'
const MAX_RECENT = 24

export interface RecentTrip {
  id: string
  openedAt: string
}

function isRecent(value: unknown): value is RecentTrip {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return typeof v.id === 'string' && v.id.length > 0 && typeof v.openedAt === 'string'
}

export function readRecentTrips(): RecentTrip[] {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(RECENT_KEY)
  } catch {
    return []
  }
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isRecent).slice(0, MAX_RECENT)
  } catch {
    return []
  }
}

/** Newest first, one row per trip. Called when a trip is created or opened. */
export function recordRecentTrip(id: string): void {
  if (!id) return
  const next: RecentTrip[] = [{ id, openedAt: new Date().toISOString() }, ...readRecentTrips().filter((t) => t.id !== id)]
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next.slice(0, MAX_RECENT)))
  } catch {
    // 隐私模式下 localStorage 会抛：首页少一块历史，不该连带行程页都打不开。
  }
  // 再打开一次就是「我还要用它」，之前的移除自动作废——不然登录用户的行程列表会永久缺一条。
  unhideTrip(id)
}

/** Drops ids the server no longer knows about, so a deleted trip stops haunting the home page. */
export function pruneRecentTrips(aliveIds: Set<string>): RecentTrip[] {
  const kept = readRecentTrips().filter((t) => aliveIds.has(t.id))
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(kept))
  } catch {
    /* 同上 */
  }
  return kept
}

/* ---------- 「从首页移除」的本地名单 ---------- */

const HIDDEN_KEY = 'tourplanopt.hidden_trips'
const MAX_HIDDEN = 60

/**
 * 黑名单的变更版本号。localStorage 不是响应式的：设置面板里点了「恢复」，首页那份
 * `hidden` 还是旧的，看着像按钮没反应。所以唯一那条写入路径 bump 一次，谁在渲染谁 watch 它。
 */
export const hiddenRev = ref(0)

export function readHiddenTrips(): string[] {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(HIDDEN_KEY)
  } catch {
    return []
  }
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((x): x is string => typeof x === 'string' && x.length > 0).slice(0, MAX_HIDDEN)
  } catch {
    return []
  }
}

function writeHidden(ids: string[]): void {
  try {
    localStorage.setItem(HIDDEN_KEY, JSON.stringify(ids.slice(0, MAX_HIDDEN)))
  } catch {
    /* 存不下就等于没有这个功能，首页照常渲染 */
  }
  hiddenRev.value += 1
}

/**
 * 只把行程从这台设备的首页摘掉，不删数据、不撤别人的访问权。
 *
 * 光删本地「最近打开」不够：登录用户的列表还多来自服务端的 `GET /api/auth/trips`，
 * 那份删不掉。所以另存一份黑名单，渲染时两个来源一起过滤。
 */
export function hideTrip(id: string): string[] {
  const next = [id, ...readHiddenTrips().filter((x) => x !== id)]
  writeHidden(next)
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(readRecentTrips().filter((t) => t.id !== id)))
  } catch {
    /* 同上 */
  }
  return next
}

export function unhideTrip(id: string): void {
  if (!readHiddenTrips().includes(id)) return
  writeHidden(readHiddenTrips().filter((x) => x !== id))
}

/** 清空名单，返回恢复了几条。设置面板那句「全部恢复」要的数就是它。 */
export function unhideAllTrips(): number {
  const n = readHiddenTrips().length
  if (!n) return 0
  writeHidden([])
  return n
}
