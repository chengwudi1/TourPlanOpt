/**
 * 封面面板「城市图片」那一档的候选。
 *
 * M37 起海报头不再自动挑这一档的图，自动档换成了打包在前端的内置海报（见 utils/builtinCovers），
 * 这里只剩一件事：面板打开时把这个城市能用的图列出来给用户挑。挑中之后写进 `trips.cover_url`，
 * 也就是从「默认那张」变成「自己选的那张」；这一档本身不落库，空串才是默认档的落点。
 *
 * 缓存要记**两件事**：取到了哪些，以及「这个城市取过了、没取到」。后者不记的话，每次重渲染
 * 都会为一批根本不存在的图再打一次请求。失败同样按「空」记一次——对同一个城市不重试：
 * 少一批候选是个便宜故障，重试风暴才是贵的。
 */

import { type Ref, ref, watch } from 'vue'

import { apiFetch } from '@/utils/api'

interface Recommendation {
  pois?: { photo?: string }[]
}

const settled = new Map<string, string[]>()
const inFlight = new Map<string, Promise<string[]>>()

async function load(city: string): Promise<string[]> {
  const params = new URLSearchParams({ city, category: 'scenic', sort: 'hot' })
  const data = await apiFetch<Recommendation>(`/api/city/recommendations?${params}`)
  // 「带图的候选，按候选顺序」整份留着：选择面板要挑，海报头只取第一条。
  return (data.pois ?? []).map((poi) => poi.photo ?? '').filter(Boolean)
}

/** 该城市的候选图（已去掉无图项）；并发只发一次请求，空数组 = 取过了、没有。 */
export function cityCoverCandidates(city: string): Promise<string[]> {
  const name = city.trim()
  if (!name) return Promise.resolve([])
  const known = settled.get(name)
  if (known) return Promise.resolve(known)
  const pending = inFlight.get(name)
  if (pending) return pending
  const task = load(name)
    .then((urls) => {
      settled.set(name, urls)
      return urls
    })
    .catch(() => {
      settled.set(name, [])
      return []
    })
    .finally(() => {
      inFlight.delete(name)
    })
  inFlight.set(name, task)
  return task
}

/** 跟着城市变化的候选图。城市换了必须先清空——留着上一个城市的图，等于海报在说谎。 */
export function useCityCoverCandidates(source: () => string): Ref<string[]> {
  const urls = ref<string[]>([])
  let seq = 0
  watch(
    source,
    async (city) => {
      const mine = ++seq
      urls.value = []
      const found = await cityCoverCandidates(city)
      if (mine === seq) urls.value = found
    },
    { immediate: true },
  )
  return urls
}
