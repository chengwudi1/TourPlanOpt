/**
 * Frontend port of backend/app/util/coords.py.
 *
 * Everything in this project is GCJ-02 (Amap native): map, DB, API -- no conversion
 * anywhere. The ONE exception is navigator.geolocation, which returns WGS-84 and is
 * off by 100-700 m in mainland China. Convert at exactly that boundary and nowhere
 * else. Outside China the offset is undefined -> identity.
 */

export type Coord = [number, number]

const PI = Math.PI
const A = 6378245.0 // 克拉索夫斯基椭球长半轴
const EE = 0.00669342162296594323

export function isInChina(lng: number, lat: number): boolean {
  return lng >= 73.0 && lng <= 136.0 && lat >= 17.0 && lat <= 54.0
}

function transformLat(x: number, y: number): number {
  let ret =
    -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(y * PI) + 40.0 * Math.sin((y / 3.0) * PI)) * 2.0) / 3.0
  ret += ((160.0 * Math.sin((y / 12.0) * PI) + 320.0 * Math.sin((y * PI) / 30.0)) * 2.0) / 3.0
  return ret
}

function transformLng(x: number, y: number): number {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(x * PI) + 40.0 * Math.sin((x / 3.0) * PI)) * 2.0) / 3.0
  ret += ((150.0 * Math.sin((x / 12.0) * PI) + 300.0 * Math.sin((x / 30.0) * PI)) * 2.0) / 3.0
  return ret
}

/** WGS-84 -> GCJ-02. Identity outside the Chinese grid. */
export function wgs84ToGcj02(lng: number, lat: number): Coord {
  if (!isInChina(lng, lat)) return [lng, lat]
  let dlat = transformLat(lng - 105.0, lat - 35.0)
  let dlng = transformLng(lng - 105.0, lat - 35.0)
  const radlat = (lat / 180.0) * PI
  let magic = Math.sin(radlat)
  magic = 1 - EE * magic * magic
  const sqrtmagic = Math.sqrt(magic)
  dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
  dlng = (dlng * 180.0) / ((A / sqrtmagic) * Math.cos(radlat) * PI)
  return [lng + dlng, lat + dlat]
}

const EARTH_R_M = 6371000

/** 大圆距离（米）。GCJ-02 上算，小尺度下与真实路网距离的偏差可忽略——只用于展示。 */
export function haversineM(
  a: { lng: number; lat: number },
  b: { lng: number; lat: number },
): number {
  const rad = PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLng = (b.lng - a.lng) * rad
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2
  return 2 * EARTH_R_M * Math.asin(Math.sqrt(s))
}

/** 展示用距离文本：<1km 显示米，其余 1 位小数公里。 */
export function formatDistance(m: number): string {
  if (m < 1000) return `${Math.round(m / 10) * 10} 米`
  return `${m / 1000 >= 10 ? Math.round(m / 1000) : (m / 1000).toFixed(1)} 公里`
}
