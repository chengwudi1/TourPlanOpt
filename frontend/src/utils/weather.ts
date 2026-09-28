/**
 * 日头天气：把高德那串中文文案变成界面上的一枚图标、一句话，以及「为什么没有」的一句解释。
 *
 * 三条值得单独说清的判断：
 *
 * 1. **星期由日期现算，不读接口回的那个 `week`。** 同一天有两个名字就是有两个真相源，
 *    而日期是行程里可编辑的那一个（改了日期，星期必须跟着变）。
 * 2. **图标按关键字归类，不建枚举表。** 高德的取值一共就那么几十个（「阵雨」「雷阵雨有时
 *    有雾」这类还会拼起来），枚举表漏一个就画不出图，`includes` 漏不到东西只会退回那朵云。
 * 3. **「为什么没有天气」必须就地给一条走得通的路。** 城市没填、日期还没进预报窗口、
 *    上游暂时失败——这三种沉默在界面上长得一模一样，只有文案能把它们分开。
 */

import type { Component } from 'vue'

import {
  Cloud,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Haze,
  Sun,
  Wind,
} from '@/components/icons'
import { parseDateOnly } from '@/utils/tripstatus'

export interface WeatherCast {
  date: string
  week: string
  day_weather: string
  night_weather: string
  day_temp: number | null
  night_temp: number | null
  day_wind: string
  day_power: string
}

/** GET /api/weather/forecast 的响应。字段名钉在 backend/tests/test_weather.py 的契约测试里。 */
export interface WeatherResponse {
  city: string
  adcode: string
  casts: WeatherCast[]
  cached: boolean
  reason: string
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/** 'YYYY-MM-DD' -> 该日的星期中文。日期读不懂就不硬编。 */
export function weekdayLabel(value: string | null | undefined): string {
  const date = parseDateOnly(value)
  if (!date) return ''
  return WEEKDAYS[(date.getDay() + 6) % 7] ?? ''
}

/** 日期斜线短写：'2026-09-28' -> '9/28'。天头那一行放不下「9月28日」。 */
export function dateSlash(value: string | null | undefined): string {
  const date = parseDateOnly(value)
  if (!date) return ''
  return `${date.getMonth() + 1}/${date.getDate()}`
}

/** 日期全称：'2026-09-28' -> '9月28日 周一'。展开态与悬停 title 用它。 */
export function dateWithWeekday(value: string | null | undefined): string {
  const date = parseDateOnly(value)
  if (!date) return ''
  const day = `${date.getMonth() + 1}月${date.getDate()}日`
  const week = weekdayLabel(value)
  return week ? `${day} ${week}` : day
}

export function castOnDate(casts: WeatherCast[], date: string | null): WeatherCast | null {
  if (!date) return null
  return casts.find((c) => c.date === date) ?? null
}

/** 预报覆盖的最后一天。casts 为空时 null——「查不到」和「没覆盖到」是两句话。 */
export function lastCastDate(casts: WeatherCast[]): string | null {
  return casts.length ? (casts[casts.length - 1]?.date ?? null) : null
}

function dayStart(value: string | null | undefined): number {
  const date = parseDateOnly(value)
  return date ? date.getTime() : Number.NaN
}

/** 图标分类。顺序即优先级：「雷阵雨」不是雨，「雨夹雪」算雪。 */
export function weatherIcon(cast: WeatherCast | null): Component | null {
  if (!cast) return null
  const text = `${cast.day_weather}${cast.night_weather}`
  if (text.includes('雹') || text.includes('雪')) return CloudSnow
  if (text.includes('雷')) return CloudLightning
  if (text.includes('雨')) return CloudRain
  if (text.includes('雾')) return CloudFog
  if (text.includes('霾') || text.includes('沙') || text.includes('尘')) return Haze
  if (text.includes('云')) return CloudSun
  if (text.includes('晴')) return Sun
  if (text.includes('风') || text.includes('浪')) return Wind
  return Cloud
}

/** 「阴转阵雨」；白天夜间一样就只说一次。 */
export function weatherPhrase(cast: WeatherCast | null): string {
  if (!cast) return ''
  const day = cast.day_weather.trim()
  const night = cast.night_weather.trim()
  if (!day) return night ? `夜间${night}` : ''
  return !night || night === day ? day : `${day}转${night}`
}

/** 「21~29°」。只有一头时给那一头，两头都没有时返回空串（不写「-~-°」）。 */
export function tempRange(cast: WeatherCast | null): string {
  if (!cast) return ''
  const { night_temp: low, day_temp: high } = cast
  if (low === null && high === null) return ''
  if (low === null) return `${high}°以上`
  if (high === null) return `${low}°以下`
  return low === high ? `${low}°` : `${low}~${high}°`
}

/** 「北风 1-3 级」；风力缺失时只说风向。 */
export function windPhrase(cast: WeatherCast | null): string {
  if (!cast) return ''
  const wind = cast.day_wind.trim()
  const power = cast.day_power.trim()
  if (!wind && !power) return ''
  if (!power) return `${wind}风`
  return `${wind}风 ${power} 级`
}

/** 悬停与读屏用的整句。 */
export function weatherSentence(cast: WeatherCast | null, date: string | null): string {
  if (!cast) return ''
  const parts = [dateWithWeekday(date), weatherPhrase(cast), tempRange(cast), windPhrase(cast)]
  return parts.filter(Boolean).join('，')
}

function castIn(casts: WeatherCast[], date: string | null): boolean {
  return castOnDate(casts, date) !== null
}

/**
 * 这一天为什么没有天气。返回空串表示「不必解释」。
 *
 * `cityFilled` 是行程有没有填目的地城市——没填时前端根本不会发这一枪，
 * 这条解释只能在这儿给。
 */
export function weatherNote(
  casts: WeatherCast[],
  date: string | null,
  opts: { cityFilled: boolean; reason: string },
): { text: string; action: 'city' | 'retry' | '' } {
  if (!date) return { text: '', action: '' }
  if (castIn(casts, date)) return { text: '', action: '' }
  if (!opts.cityFilled) return { text: '先填写目的地城市', action: 'city' }
  if (!casts.length) {
    if (opts.reason === 'upstream') return { text: '天气预报暂时没取到', action: 'retry' }
    if (opts.reason === 'not_found') return { text: '这座城市高德查不到', action: 'city' }
    return { text: '', action: '' }
  }
  const last = dayStart(lastCastDate(casts))
  const target = dayStart(date)
  if (!Number.isNaN(last) && !Number.isNaN(target) && target > last) {
    return { text: '预报只覆盖未来 4 天，临近出发自动补上', action: '' }
  }
  return { text: '这一天已不在预报窗口内', action: '' }
}
