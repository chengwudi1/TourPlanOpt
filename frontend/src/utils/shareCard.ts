/**
 * 行程分享图：把一天（或整个行程的摘要）画成 1080×1440（3:4，小红书/朋友圈比例）
 * 的 PNG。纯 canvas + 系统字体，零依赖。交付方式优先 Web Share（手机上直接进
 * 微信/小红书），否则触发下载。
 */

import type { Place, Trip } from '@/types/domain'

// 分享图是「发布物」，不跟查看者的深浅色走，所以这里钉死亮色值。
// 数字来自 main.css 的亮色 :root（--accent / --accent-strong / --text / --text-2 / --bg），
// 改色板时这一份要跟着改——它不参与主题，别改成 getComputedStyle。
const W = 1080
const H = 1440
const SEA = '#2f6a99'
const SEA_DEEP = '#24517a'
const INK = '#1b1d1f'
const GRAY = '#4a4d50'
const BG = '#f7f7f6'

const FONT = `-apple-system, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif`

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxLines: number,
): string[] {
  const lines: string[] = []
  let line = ''
  for (const char of text) {
    if (ctx.measureText(line + char).width > maxWidth && line) {
      lines.push(line)
      line = char
      if (lines.length === maxLines) return lines
    } else {
      line += char
    }
  }
  if (line && lines.length < maxLines) lines.push(line)
  return lines.length ? lines : ['']
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

export function drawTripCard(trip: Trip, places: Place[]): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')!

  // 底色
  ctx.fillStyle = BG
  ctx.fillRect(0, 0, W, H)

  // 头部色带：行程名 + 城市/站数
  const headerH = 300
  ctx.fillStyle = SEA
  ctx.fillRect(0, 0, W, headerH)
  ctx.fillStyle = SEA_DEEP
  ctx.fillRect(0, headerH - 14, W, 14)

  ctx.fillStyle = '#ffffff'
  ctx.font = `700 30px ${FONT}`
  ctx.fillText('我们的行程', 72, 78)

  // 测量与绘制必须同一把字号：以前先 wrap（还是 30px 的字体）后画（68px），量出来的行
  // 比实宽长一倍，长标题整行冲出画布。放不进一行就降一档字号分两行，行距与 meta 行
  // 都按字面高度留出间隙（68px 两行会直接啃到 meta 上）。
  const title = trip.title || '未命名行程'
  ctx.font = `800 68px ${FONT}`
  let titleLines: string[]
  if (ctx.measureText(title).width <= W - 144) {
    titleLines = [title]
  } else {
    ctx.font = `800 54px ${FONT}`
    titleLines = wrapText(ctx, title, W - 144, 2)
  }
  titleLines.forEach((line, i) => {
    ctx.fillText(line, 72, titleLines.length > 1 ? 150 + i * 60 : 160)
  })

  ctx.font = `500 30px ${FONT}`
  ctx.fillStyle = 'rgba(255,255,255,0.85)'
  const meta = [trip.city, `${places.length} 个地点`].filter(Boolean).join(' · ')
  ctx.fillText(meta, 72, headerH - 46)

  // 地点列表（最多 10 条；超出的折叠成摘要行）。签名条从 H-130 起，第 11 行的字身
  // 已经会画进那条白条里——超出时只放 9 行，把第 10 行的位置让给摘要。
  const MAX_ROWS = 10
  const shown = places.slice(0, places.length > MAX_ROWS ? MAX_ROWS - 1 : MAX_ROWS)
  let y = headerH + 84
  shown.forEach((place, index) => {
    // 序号圈
    ctx.fillStyle = SEA
    ctx.beginPath()
    ctx.arc(96, y, 30, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.font = `700 30px ${FONT}`
    ctx.textAlign = 'center'
    ctx.fillText(String(index + 1), 96, y + 11)
    ctx.textAlign = 'left'

    // 名称 + 地址
    ctx.fillStyle = INK
    ctx.font = `600 38px ${FONT}`
    ctx.fillText(place.name, 156, y + 2)
    if (place.address) {
      ctx.fillStyle = GRAY
      ctx.font = `400 25px ${FONT}`
      const addr = wrapText(ctx, place.address, W - 400, 1)[0]
      ctx.fillText(addr, 156, y + 44)
    }

    // 已排程的时间靠右
    if (place.start_min !== null && place.start_min !== undefined) {
      const hh = String(Math.floor((place.start_min % 1440) / 60)).padStart(2, '0')
      const mm = String(place.start_min % 60).padStart(2, '0')
      ctx.fillStyle = SEA_DEEP
      ctx.font = `600 30px ${FONT}`
      ctx.textAlign = 'right'
      ctx.fillText(`${hh}:${mm}`, W - 84, y + 8)
      ctx.textAlign = 'left'
    }

    y += 96
  })

  if (places.length > shown.length) {
    ctx.fillStyle = GRAY
    ctx.font = `400 28px ${FONT}`
    ctx.fillText(`…还有 ${places.length - shown.length} 个地点`, 156, y - 30)
    y += 40
  }

  // 底部签名条
  ctx.fillStyle = '#ffffff'
  roundRect(ctx, 72, H - 130, W - 144, 74, 20)
  ctx.fill()
  ctx.fillStyle = INK
  ctx.font = `600 30px ${FONT}`
  ctx.fillText('TourPlanOpt', 104, H - 82)
  ctx.fillStyle = GRAY
  ctx.font = `400 26px ${FONT}`
  ctx.fillText('一起把行程排好', 300, H - 80)

  return canvas
}

export async function shareTripCard(trip: Trip, places: Place[]): Promise<'shared' | 'downloaded'> {
  const canvas = drawTripCard(trip, places)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (!blob) throw new Error('图片生成失败')
  const filename = `行程-${trip.title || trip.id}.png`

  const file = new File([blob], filename, { type: 'image/png' })
  const canShare = 'canShare' in navigator && navigator.canShare?.({ files: [file] })
  if (canShare) {
    await navigator.share({ files: [file], title: trip.title })
    return 'shared'
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  // 点击后同步 revoke 会把某些浏览器还没开始的下载一并拽掉：错开一拍再释放。
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return 'downloaded'
}
