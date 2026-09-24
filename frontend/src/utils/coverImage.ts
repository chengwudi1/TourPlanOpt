/**
 * 封面预缩：把用户挑的一张图压成后端该收的大小。
 *
 * 后端没有图像库（也不该为了这一个功能装 Pillow），而手机直出的照片动辄 4 MB / 4000 px 宽，
 * 海报头最多用得到 1600 px 的长边。所以缩放放在前端，后端的 4 MB 只是绕开前端直接打接口时的
 * 硬闸。
 *
 * 透明像素按白底合成：JPEG 没有 Alpha，留着的话 `toBlob` 会把透明区域画成黑色。
 * 解码失败时**原样返回**那张文件——预缩是省流量的顺手事，不该把「传不了图」变成新功能故障。
 */

export const COVER_MAX_EDGE = 1600
export const COVER_QUALITY = 0.82

/** 已经够小、且本来就是 JPEG 的直接跳过一次重编码：再压一遍只会更糊，不省什么。 */
function alreadySmallEnough(file: Blob, width: number, height: number): boolean {
  return (
    Math.max(width, height) <= COVER_MAX_EDGE &&
    file.type === 'image/jpeg' &&
    file.size <= 600 * 1024
  )
}

async function decode(file: Blob): Promise<{ width: number; height: number; draw: () => CanvasImageSource } | null> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file)
      return { width: bitmap.width, height: bitmap.height, draw: () => bitmap }
    } catch {
      return null
    }
  }
  // 老浏览器：走 <img> + objectURL。canvas 能画它，尺寸从 naturalWidth 拿。
  const url = URL.createObjectURL(file)
  try {
    const img = new Image()
    img.src = url
    await img.decode().catch(() => undefined)
    if (!img.naturalWidth) return null
    return { width: img.naturalWidth, height: img.naturalHeight, draw: () => img }
  } finally {
    // objectURL 一旦画进 canvas 就再也不需要了；解码失败时也要放掉，否则每次选图漏一个。
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

/** 压成 JPEG（长边 ≤1600、q 0.82）。拿不到像素时返回原文件。 */
export async function shrinkToCover(file: Blob): Promise<Blob> {
  const source = await decode(file)
  if (!source) return file
  const { width, height } = source
  if (alreadySmallEnough(file, width, height)) return file

  const scale = Math.min(1, COVER_MAX_EDGE / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width * scale))
  canvas.height = Math.max(1, Math.round(height * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(source.draw(), 0, 0, canvas.width, canvas.height)
  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob((b) => resolve(b), 'image/jpeg', COVER_QUALITY),
  )
  return blob ?? file
}
