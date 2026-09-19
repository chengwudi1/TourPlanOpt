/**
 * Display-only time helpers. Mirror of backend/app/util/timefmt.py.
 *
 * Times are integer minutes since midnight everywhere -- in the DB, on the wire and in
 * the stores. Nothing here may feed back into a calculation.
 */

export const MIN_PER_DAY = 24 * 60

export function formatMin(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || minutes < 0) return ''
  const day = Math.floor(minutes / MIN_PER_DAY)
  const rem = minutes % MIN_PER_DAY
  const hh = String(Math.floor(rem / 60)).padStart(2, '0')
  const mm = String(rem % 60).padStart(2, '0')
  if (day === 0) return `${hh}:${mm}`
  return day === 1 ? `次日 ${hh}:${mm}` : `+${day}天 ${hh}:${mm}`
}

export function formatDuration(minutes: number | null | undefined): string {
  if (!minutes) return '0 分钟'
  const m = Math.abs(Math.round(minutes))
  const hours = Math.floor(m / 60)
  const mins = m % 60
  if (hours && mins) return `${hours} 小时 ${mins} 分`
  if (hours) return `${hours} 小时`
  return `${mins} 分钟`
}

export function parseHHMM(text: string): number | null {
  const s = (text ?? '').trim()
  if (!s.includes(':')) return null
  const [hh, mm] = s.split(':')
  if (!/^\d+$/.test(hh) || !/^\d+$/.test(mm)) return null
  const hours = Number(hh)
  const mins = Number(mm)
  if (hours < 0 || hours > 23 || mins < 0 || mins > 59) return null
  return hours * 60 + mins
}

/**
 * formatDuration 的反向：只服务停留时长输入框，认不出就返回 null 让调用方退回上一个好值。
 * 孤零零一个数字按分钟算——输入框里已经写着「1 小时 40 分」，改成 20 的意图是 20 分钟，
 * 不是 20 小时。
 */
export function parseDuration(text: string): number | null {
  const s = (text ?? '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '')
  if (!s) return null
  const both = s.match(/^(\d+(?:\.\d+)?)(?:小时|h|时)(\d+(?:\.\d+)?)(?:分|m)?$/)
  if (both) return Math.round(Number(both[1]) * 60 + Number(both[2]))
  const hours = s.match(/^(\d+(?:\.\d+)?)(?:小时|h|时)$/)
  if (hours) return Math.round(Number(hours[1]) * 60)
  const mins = s.match(/^(\d+(?:\.\d+)?)(?:分|m|min)$/)
  if (mins) return Math.round(Number(mins[1]))
  if (/^\d+(\.\d+)?$/.test(s)) return Math.round(Number(s))
  return null
}

/**
 * 这个文件里唯一处理 ISO 时间戳的函数——其余都只认「零点起算的分钟数」。
 * 首页要说「最近编辑 3 天前」，那是一堵墙上时间，不是行程里的时刻，所以放这儿而不是另开文件。
 */
export function formatAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const mins = Math.floor((Date.now() - then) / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days === 1) return '昨天'
  if (days < 30) return `${days} 天前`
  const d = new Date(then)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
