import { computed, ref } from 'vue'

import { ensureAmap, useAmap } from '@/composables/useAmap'

/**
 * 高德 Key 的健康度（M26d）。
 *
 * 原来这份状态长在 `AmapKeyCheck.vue` 里，后果是每条诊断横幅都常驻首屏：
 * `/api/amap/health` 一次要替我们打一发高德请求（实测 529–778ms 不等），
 * 而「Web服务 686.2ms」对用户不含任何可行动信息。状态提到模块级，两个消费者共用——
 * 诊断面板与顶栏那颗小点——并且一个会话只探一次。
 *
 * 后端只能验 Web服务 Key（那个 Key 从不出现在前端），浏览器只能验 JS Key
 * （域名白名单在浏览器侧才生效）——两半都要，这是这块诊断存在的理由。
 */

export interface KeyCheck {
  name: string
  ok: boolean
  present: boolean
  detail: string
  hint: string
  infocode?: string
  latency_ms?: number
}

export type AmapHealth = 'checking' | 'good' | 'bad'

const DISMISS_KEY = 'tourplanopt.keycheck.dismissed'
const RESULT_KEY = 'tourplanopt.keycheck.result'

/** 隐私模式下 localStorage / sessionStorage 本身就会抛，不是只有读写会失败。 */
function readStored(storage: 'local' | 'session', key: string): string {
  try {
    return (storage === 'local' ? localStorage : sessionStorage).getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeStored(storage: 'local' | 'session', key: string, value: string) {
  try {
    ;(storage === 'local' ? localStorage : sessionStorage).setItem(key, value)
  } catch {
    /* 存不下就算了：最坏结果是下次又探一遍 */
  }
}

/** 上一趟导航探到的结论。只存「探过了」的标记会造出一个凭标记亮起的绿点，
 * 所以缓存的是结论本身，开局直接回放；探不到的那一趟不缓存。 */
interface CachedProbe {
  web: KeyCheck | null
  js: KeyCheck | null
}

function readCachedProbe(): CachedProbe | null {
  const raw = readStored('session', RESULT_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as CachedProbe
    // 一次成活的探活必然同时带回两条结论；缺任何一条就说明这份缓存里没有结论
    // （旧格式那种「只记了 error」的 blob 就是），当没探过，重新探。
    if (!parsed?.web || !parsed?.js) return null
    return { web: parsed.web, js: parsed.js }
  } catch {
    return null
  }
}

const { status, diagnostics } = useAmap()

const cached = readCachedProbe()
const loading = ref(false)
const backendError = ref('')
const webKey = ref<KeyCheck | null>(cached?.web ?? null)
const jsKey = ref<KeyCheck | null>(cached?.js ?? null)
const probed = ref(cached !== null)
const expanded = ref(false)
const dismissedSignature = ref(readStored('local', DISMISS_KEY))

/**
 * 后端已经报了「AMAP_JS_KEY 未配置」时，把浏览器那条重复诊断压掉：
 * 一个真问题只占一行，而只有浏览器能查出来的东西（加载失败、INVALID_USER_SCODE、
 * 域名白名单）仍然要说。
 */
const frontendDiagnostics = computed(() => {
  const backendCoveredMissingKey = jsKey.value !== null && !jsKey.value.present
  return diagnostics.value.filter(
    (d) => !(backendCoveredMissingKey && `${d.title}${d.hint}`.includes('AMAP_JS_KEY')),
  )
})

const problems = computed(() => {
  const list: string[] = []
  if (backendError.value) list.push('后端自检读不到')
  if (webKey.value && !webKey.value.ok) list.push(`web:${webKey.value.detail}`)
  if (jsKey.value && !jsKey.value.ok) list.push(`js:${jsKey.value.detail}`)
  for (const d of frontendDiagnostics.value) if (d.level === 'danger') list.push(`fe:${d.title}`)
  if (status.value === 'error') list.push('fe:JS API 加载失败')
  return list
})

/** 出过的问题换个 Key 就好了：签名一变，之前点的「先不管」自动失效。 */
const failureSignature = computed(() => problems.value.join('|'))

const dismissed = computed(
  () => dismissedSignature.value !== '' && dismissedSignature.value === failureSignature.value,
)

const health = computed<AmapHealth>(() => {
  // 后端那一半可能来自缓存回放，但浏览器这一半（JS API 加载 + 探针）此刻还没结论时
  // 不能提前判 bad——否则面板上会铺出一排没有任何问题行的按钮。
  if (loading.value || !probed.value) return 'checking'
  if (status.value === 'idle' || status.value === 'loading') return 'checking'
  return problems.value.length === 0 ? 'good' : 'bad'
})

/** 面板只在「有问题且没被忽略」或用户主动展开时才出现；健康时一个字都不占。 */
const showPanel = computed(() => expanded.value || (health.value === 'bad' && !dismissed.value))

/**
 * @param force 「重新检查」按钮走的口子：忽略会话内的去重，也清掉上次的忽略签名。
 */
async function probe(force = false): Promise<void> {
  if (probed.value && !force) {
    await ensureAmap().catch(() => undefined)
    return
  }
  if (force) dismissedSignature.value = ''
  loading.value = true
  backendError.value = ''
  try {
    // 这一发是要花钱的（后端会真打一次高德），所以一个会话只打一次，绝不挂定时器。
    const res = await fetch('/api/amap/health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    webKey.value = data.web_key
    jsKey.value = data.js_key
  } catch (err) {
    backendError.value = `本机服务没有应答。请确认后端已启动（在 backend/ 目录执行 uv run uvicorn app.main:app --reload）。原始错误：${err}`
  } finally {
    loading.value = false
    probed.value = true
    // 只缓存「问到了答案」。读不到后端（后端在重启、瞬断）是「没问到」而不是「坏了」，
    // 连这个一起缓存会让红点一路挂到用户手动重查为止。
    if (backendError.value) {
      writeStored('session', RESULT_KEY, '')
    } else {
      writeStored(
        'session',
        RESULT_KEY,
        JSON.stringify({ web: webKey.value, js: jsKey.value } satisfies CachedProbe),
      )
    }
  }
  await ensureAmap().catch(() => undefined)
}

function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) void probe()
}

function dismiss() {
  dismissedSignature.value = failureSignature.value
  writeStored('local', DISMISS_KEY, dismissedSignature.value)
  expanded.value = false
}

export function useAmapHealth() {
  return {
    health,
    showPanel,
    dismissed,
    expanded,
    loading,
    backendError,
    webKey,
    jsKey,
    frontendDiagnostics,
    status,
    probe,
    toggle,
    dismiss,
  }
}
