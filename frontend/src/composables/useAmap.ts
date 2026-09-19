import AMapLoader from '@amap/amap-jsapi-loader'
import { readonly, ref } from 'vue'

/**
 * Loads the Amap JS API exactly once per page.
 *
 * ORDERING IS LOAD-BEARING: `window._AMapSecurityConfig` must be assigned
 * BEFORE `AMapLoader.load()`. Assigning it later — for example from a component
 * `onMounted` that runs after the loader — silently produces
 * INVALID_USER_SCODE at runtime. That is why this lives at module scope in a
 * single composable instead of in a component.
 */

export interface AmapBootConfig {
  js_key: string
  scode: string
  jsapi_version: string
  plugins: string[]
}

export type AmapStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface AmapDiagnostic {
  level: 'ok' | 'warn' | 'danger'
  title: string
  hint: string
}

// The Amap JS API namespace is untyped and huge; `any` is the pragmatic choice.
export type AMapNS = any

const status = ref<AmapStatus>('idle')
const diagnostics = ref<AmapDiagnostic[]>([])
const bootConfig = ref<AmapBootConfig | null>(null)

let ns: AMapNS = null
let loadPromise: Promise<AMapNS> | null = null

function push(level: AmapDiagnostic['level'], title: string, hint = '') {
  diagnostics.value = [...diagnostics.value, { level, title, hint }]
}

function reset() {
  diagnostics.value = []
}

async function fetchBootConfig(): Promise<AmapBootConfig> {
  const res = await fetch('/api/config')
  if (!res.ok) throw new Error(`/api/config 返回 HTTP ${res.status}`)
  const body = await res.json()
  return body.amap as AmapBootConfig
}

/**
 * Probe the loaded API with one cheap call. This is the only way to validate a
 * JS API key from code: the backend cannot do it, because the key is checked
 * in-browser against the console's domain whitelist.
 */
async function probeWithAutocomplete(AMap: AMapNS): Promise<void> {
  const autoComplete = new AMap.AutoComplete({ city: '全国', citylimit: false })
  const result = await new Promise<unknown>((resolve) => {
    autoComplete.search('测试', (state: string, res: unknown) => resolve({ state, res }))
  })
  const { state, res } = result as { state: string; res: any }

  if (state === 'complete') return

  const info = String(res ?? '')
  const text = `${info} ${JSON.stringify(res?.info ?? '')}`

  if (text.includes('INVALID_USER_SCODE')) {
    throw new Error(
      'INVALID_USER_SCODE：安全密钥缺失或不正确。请把 JS API Key 对应的「安全密钥」填进 backend/.env 的 AMAP_JS_SCODE'
    )
  }
  if (text.includes('USERKEY_PLAT_NOMATCH')) {
    throw new Error(
      'USERKEY_PLAT_NOMATCH：Key 类型与平台不符。AMAP_JS_KEY 需要的是「Web端(JS API)」类型的 Key，不是「Web服务」'
    )
  }
  if (text.includes('DAILY_QUERY_OVER_LIMIT')) {
    throw new Error('DAILY_QUERY_OVER_LIMIT：该 Key 今日配额已用完')
  }
  if (text.includes('INVALID_USER_KEY')) {
    throw new Error('INVALID_USER_KEY：AMAP_JS_KEY 不正确或已过期')
  }
  throw new Error(`高德 JS API 校验失败：${state} ${info}`)
}

function warnAboutHostname() {
  const host = location.hostname
  if (host === 'localhost' || host === '127.0.0.1') return
  push(
    'warn',
    '换别的地址访问时地图可能变灰',
    `当前地址是 ${host}。如果高德控制台里给这个 Key 设了域名白名单且不包含该地址，地图会显示为灰色；开发期建议把白名单留空。`
  )
}

export function ensureAmap(): Promise<AMapNS> {
  if (ns) return Promise.resolve(ns)
  if (loadPromise) return loadPromise

  status.value = 'loading'
  reset()

  loadPromise = (async () => {
    let cfg: AmapBootConfig
    try {
      cfg = await fetchBootConfig()
    } catch (err) {
      status.value = 'error'
      push('danger', '地图服务连不上：没读到本机配置', `请确认后端已启动（在 backend/ 目录执行 uv run uvicorn app.main:app）。原始错误：${err}`)
      throw err
    }
    bootConfig.value = cfg

    if (!cfg.js_key) {
      status.value = 'error'
      push(
        'danger',
        '网页里的地图 Key 还没填',
        'backend/.env 缺少 AMAP_JS_KEY。请到 https://console.amap.com 添加一个「Web端(JS API)」类型的 Key。注意它与后端用的「Web服务」Key 是两个不同的 Key。'
      )
      throw new Error('AMAP_JS_KEY 未配置')
    }
    if (!cfg.scode) {
      push(
        'warn',
        '网页地图缺少配套的安全密钥',
        'backend/.env 缺少 AMAP_JS_SCODE。2021-12-02 之后创建的 JS API Key 必须配套安全密钥，否则下一步大概率报 INVALID_USER_SCODE。'
      )
    }

    // MUST happen before load(). See the file header.
    window._AMapSecurityConfig = { securityJsCode: cfg.scode }

    let AMap: AMapNS
    try {
      AMap = await AMapLoader.load({
        key: cfg.js_key,
        version: cfg.jsapi_version || '2.0',
        plugins: cfg.plugins?.length ? cfg.plugins : ['AMap.AutoComplete', 'AMap.PlaceSearch'],
      })
    } catch (err) {
      status.value = 'error'
      push('danger', '地图没能加载出来', String(err))
      throw err
    }

    try {
      await probeWithAutocomplete(AMap)
    } catch (err) {
      status.value = 'error'
      push('danger', '网页里的地图 Key 用不了', String(err instanceof Error ? err.message : err))
      throw err
    }

    warnAboutHostname()
    ns = AMap
    status.value = 'ready'
    push('ok', '地图已就绪', `JS API 版本 ${cfg.jsapi_version}`)
    return AMap
  })()

  loadPromise.catch(() => {
    // Keep the rejected promise from being rethrown on every later call; the
    // diagnostics array already carries the explanation for the UI.
    loadPromise = null
  })

  return loadPromise
}

export function useAmap() {
  return {
    status: readonly(status),
    diagnostics: readonly(diagnostics),
    bootConfig: readonly(bootConfig),
    ensureAmap,
    getAMap: () => ns,
  }
}
