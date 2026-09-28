/**
 * 日头天气的客户端状态：**按城市**存，不按行程。
 *
 * 两座城市的行程共用不到一起，但同一座城市的五个「天」只该问一次——预报一次就给 4 天，
 * 按天发请求是最容易犯的错（服务端会缓存住，配额不炸，但白等四轮网络）。所以这里刻意
 * 只有城市这一个键。
 *
 * 每城一次：服务端另有 6 小时的预报缓存，同一页里重复问只会多等一次网络，拿回的还是
 * 同一份。失败也算「问过了」，界面上给一颗重试按钮由人手动再问。
 */

import { acceptHMRUpdate, defineStore } from 'pinia'
import { ref } from 'vue'

import { apiFetch } from '@/utils/api'
import { castOnDate } from '@/utils/weather'
import type { WeatherCast, WeatherResponse } from '@/utils/weather'

interface CityWeather {
  casts: WeatherCast[]
  reason: string
  loading: boolean
  asked: boolean
}

const NO_CITY: CityWeather = { casts: [], reason: '', loading: false, asked: false }

function keyOf(city: string | null | undefined): string {
  return (city ?? '').trim()
}

export const useWeatherStore = defineStore('weather', () => {
  const byCity = ref<Record<string, CityWeather>>({})
  // 谁在飞不由界面猜：ensure 可能被同一帧里三个组件各调一次。
  const inflight = new Set<string>()

  function peek(city: string | null | undefined): CityWeather {
    return byCity.value[keyOf(city)] ?? NO_CITY
  }

  function touch(city: string): CityWeather {
    const key = keyOf(city)
    if (!byCity.value[key]) {
      byCity.value[key] = { casts: [], reason: '', loading: false, asked: false }
    }
    // 再读一次拿代理：改本地那个字面量对象不会惊动界面——预报落地那一刻天头不动，
    // 要等用户点别的东西才亮。
    return byCity.value[key] as CityWeather
  }

  async function ensure(city: string | null | undefined): Promise<void> {
    const key = keyOf(city)
    if (!key) return
    const state = peek(key)
    if (state.asked || state.loading || inflight.has(key)) return
    inflight.add(key)
    const target = touch(key)
    target.loading = true
    try {
      const params = new URLSearchParams({ city: key })
      const data = await apiFetch<WeatherResponse>(`/api/weather/forecast?${params}`)
      target.casts = Array.isArray(data.casts) ? data.casts : []
      target.reason = typeof data.reason === 'string' ? data.reason : ''
    } catch {
      // 降级成「没有天气」，不冒泡：天头那一行宁可空着，也不该把行程页打成错误条。
      target.casts = []
      target.reason = 'upstream'
    } finally {
      target.loading = false
      target.asked = true
      inflight.delete(key)
    }
  }

  /** 手动重来一发：给「暂时没取到」那句解释留的键。 */
  function refresh(city: string | null | undefined): Promise<void> {
    const key = keyOf(city)
    if (!key) return Promise.resolve()
    const target = touch(key)
    target.asked = false
    target.reason = ''
    return ensure(key)
  }

  function castsOf(city: string | null | undefined): WeatherCast[] {
    return peek(city).casts
  }

  function castOf(
    city: string | null | undefined,
    date: string | null | undefined,
  ): WeatherCast | null {
    if (!date) return null
    return castOnDate(peek(city).casts, date)
  }

  function reasonOf(city: string | null | undefined): string {
    return peek(city).reason
  }

  function loadingOf(city: string | null | undefined): boolean {
    return peek(city).loading
  }

  return { byCity, ensure, refresh, castsOf, castOf, reasonOf, loadingOf }
})

export type WeatherStore = ReturnType<typeof useWeatherStore>

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useWeatherStore, import.meta.hot))
}
