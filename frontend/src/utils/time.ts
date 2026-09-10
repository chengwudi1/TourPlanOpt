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
