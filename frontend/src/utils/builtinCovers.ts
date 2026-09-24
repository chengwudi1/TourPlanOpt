/**
 * 内置海报：六张写实风景，用户没传图时海报头与首页卡片用的就是这里的一张。
 *
 * 为什么不让自动档去挑高德的城市图/地点图：那两级挑出来的 `photo` 其实是**网友上传的评论图**
 * （`aos-comment.amap.com/.../comment/...`），热度第一条完全可能是一张室内随拍——用户原话
 * 「太丑了」。站内图仍能在封面面板里手动选，只是不再自动上海报。
 *
 * URL 一律走 `public/covers/` 而不是 `import`：这些串会被写进 `trips.cover_url` 落库，
 * 而 import 出来的是带构建 hash 的路径，下一次构建就把库里那条变成 404。public 原样拷贝，
 * 路径是稳定的。
 */
export interface BuiltinCover {
  key: string
  url: string
  /** 面板与菜单里说人话用的名字，不是文件名。 */
  label: string
}

export const BUILTIN_COVERS: readonly BuiltinCover[] = [
  { key: 'mountain', url: '/covers/mountain.jpg', label: '山' },
  { key: 'sea', url: '/covers/sea.jpg', label: '海' },
  { key: 'watertown', url: '/covers/watertown.jpg', label: '水乡' },
  { key: 'field', url: '/covers/field.jpg', label: '田野' },
  { key: 'lake', url: '/covers/lake.jpg', label: '湖' },
  { key: 'snow', url: '/covers/snow.jpg', label: '雪' },
]

/**
 * 海报头与首页卡片在「没传图」时用的那一张：按 trip_id 稳定散列挑。
 *
 * 同一趟行程永远同一张（刷新不换、协同两端看到的一样），不同行程不重样。用 FNV-1a 而不是
 * `Math.random`，也不是清单下标——下标会让所有新行程都拿到第一张。
 */
export function builtinCoverFor(tripId: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < tripId.length; i += 1) {
    h ^= tripId.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return BUILTIN_COVERS[h % BUILTIN_COVERS.length].url
}

/**
 * 首页门面卡与网格卡的封面：自定义 > 内置散列。后端 `cover_photo` 只回自定义那一张，
 * 内置这一半留在前端——散列规则必须和海报头是**同一份代码**，否则首页与行程页各显示一张，
 * 看着像两个不同的行程。
 */
export function summaryCover(summary: { id: string; cover_photo: string }): string {
  return summary.cover_photo || builtinCoverFor(summary.id)
}
