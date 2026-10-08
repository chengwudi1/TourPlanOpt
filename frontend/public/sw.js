/*
 * TourPlanOpt service worker.
 *
 * 策略刻意保守——这是一个协作工具，stale 内容比重新加载更伤：
 * - /api 与 /ws 永不拦截（协同数据必须即时）；
 * - 页面导航：network-first，断网时回落到缓存的 shell（离线也能看最近行程的样子）；
 * - 带哈希的 /assets：cache-first（文件名即版本，命中即最新）；
 * - 其余同源 GET：network-first + 回写缓存。
 *
 * 只在 PROD 构建注册（main.ts），Vite dev 永远不会被它干扰。
 * 发新版本时把 CACHE 版本号 +1，activate 会清掉旧缓存。
 */

// 前缀从脚本自己的 URL 推，不写死第二份：这份文件在 dist 根上，浏览器看到的路径
// 就是本站的前缀（线上 /tourplanopt/sw.js）。写死会在换前缀时留下一个「缓存清单和
// 实际路径不一致」的坑——那种坑只会让部分人的手机离线时吐旧壳，最难归因。
const BASE = new URL('./', self.location).pathname

// v3：整站搬进 /tourplanopt/ 前缀，SHELL 与资源判断都跟着换了。
// v2 那份缓存的键是根路径（'/'、'/assets/…'），留着就是「旧壳顶着新站」的事故现场，
// activate 里按名字删掉。
const CACHE = 'tourplanopt-v3'
const SHELL = [
  BASE,
  `${BASE}index.html`,
  `${BASE}manifest.webmanifest`,
  `${BASE}icons/icon-192.png`,
  `${BASE}icons/icon-512.png`,
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  // 接口与协同长连接刻意留在域名根上（不在本前缀下），这里按根路径放行。
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) return

  // 页面导航：network-first（保证拿到最新构建），失败回落缓存 shell。
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE).then((cache) => cache.put(BASE, copy))
          return response
        })
        .catch(() => caches.match(BASE).then((hit) => hit || Response.error())),
    )
    return
  }

  // 带哈希的静态资源：cache-first。
  if (url.pathname.startsWith(`${BASE}assets/`)) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            const copy = response.clone()
            caches.open(CACHE).then((cache) => cache.put(request, copy))
            return response
          }),
      ),
    )
    return
  }

  // 其它同源资源（图标等）：network-first + 回写。
  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone()
        caches.open(CACHE).then((cache) => cache.put(request, copy))
        return response
      })
      .catch(() => caches.match(request).then((hit) => hit || Response.error())),
  )
})
