/**
 * 行程的对外链接。前缀由 vite 的 `base` 决定（开发期同样生效），所以这里只拼
 * `BASE_URL`，别再手抄一份 `/tourplanopt`。后端 share_url 是同一决定的另一处表达，
 * 见 backend/app/config.py 的 frontend_prefix。
 */
export function tripUrl(tripId: string): string {
  return `${window.location.origin}${import.meta.env.BASE_URL}trip/${encodeURIComponent(tripId)}`
}
