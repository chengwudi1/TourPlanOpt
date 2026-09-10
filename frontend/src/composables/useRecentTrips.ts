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
 */

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
