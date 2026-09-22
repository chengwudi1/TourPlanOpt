import { acceptHMRUpdate, defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { apiFetch, putJson } from '@/utils/api'
import {
  applyPrefs,
  dirtyPayload,
  readPrefs,
  readSynced,
  reconcile,
  resolveBasemapDark,
  resolveReduceMotion,
  resolveTheme,
  writePrefs,
  writeSynced,
  type Prefs,
  type ResolvedTheme,
  type SparsePrefs,
} from '@/utils/prefs'

/**
 * 个人偏好：主题、字号、动效、底图、精灵。本机一份（localStorage，永远即时生效），
 * 登录了再多一份云端副本（users.prefs），登录态下每次改动防抖 300ms 写回去。
 *
 * 三件事决定了这个 store 的形状：
 *
 * 1. **CSS 只认已解析值**。偏好可以是「跟随系统」，但那件事在这里解析完就丢了，写到
 *    <html> 上的只有 data-theme / --fs-scale / data-motion。所以本文件之外（包括
 *    main.css 与 MapPanel）都不该再出现第二处 prefers-color-scheme 判断。首绘前那一次
 *    由 index.html 的内联脚本写，两处算式的关系见 [[prefs]] 顶部。
 * 2. **偏好绝不进 op、绝不进 trips**。深色是我自己的界面，走协同通道等于我一翻深色，
 *    同行的人跟着翻（同聊天那轮划下的边界：房间才是边界，个人设置不是房间里的东西）。
 * 3. **同步是 best-effort**。PUT 失败只在控制台留一句，不弹通知条：一次没存上不该让
 *    用户以为自己把界面改坏了——改动在本机已经生效，下次改动会连带把这一步补上。
 */

const SYNC_DEBOUNCE_MS = 300

export const useSettingsStore = defineStore('settings', () => {
  const prefs = ref<Prefs>(readPrefs())
  const syncing = ref(false)
  /** 面板只有一个人开，所以开合放在 store 里而不是各入口自己 v-if：两个入口（首页齿轮、
   *  行程页「更多」）指向同一个实例，SettingsPanel 也只挂一份在 App.vue。 */
  const open = ref(false)

  const darkMq = window.matchMedia('(prefers-color-scheme: dark)')
  const reduceMq = window.matchMedia('(prefers-reduced-motion: reduce)')
  const systemDark = ref(darkMq.matches)
  const systemReduce = ref(reduceMq.matches)
  darkMq.addEventListener('change', (e) => (systemDark.value = e.matches))
  reduceMq.addEventListener('change', (e) => (systemReduce.value = e.matches))

  const theme = computed<ResolvedTheme>(() => resolveTheme(prefs.value.theme, systemDark.value))
  const reduceMotion = computed(() => resolveReduceMotion(prefs.value.motion, systemReduce.value))
  /** 地图要不要换夜航底图。单一真源：MapPanel 只读这一个判断，不许自己再看一遍主题。 */
  const basemapDark = computed(() => resolveBasemapDark(prefs.value, theme.value))

  // ---------- 生效 ----------

  function apply(): void {
    applyPrefs(prefs.value, { systemDark: systemDark.value, systemReduce: systemReduce.value })
    writePrefs(prefs.value)
    // 系统状态栏/地址栏的底色跟着主题走。这里读 --bg 而不是再写一遍 hex 值：全站只有
    // index.html 那枚 meta 的初值是硬编码底色，已登记在 main.css 的例外清单里。
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    const meta = document.querySelector('meta[name="theme-color"]')
    if (bg && meta) meta.setAttribute('content', bg)
  }

  watch([prefs, systemDark, systemReduce], apply, { deep: true, immediate: true })

  function set<K extends keyof Prefs>(key: K, value: Prefs[K]): void {
    if (prefs.value[key] === value) return
    prefs.value = { ...prefs.value, [key]: value }
  }

  function show(): void {
    open.value = true
  }

  function hide(): void {
    open.value = false
  }

  // ---------- 与账号对齐 ----------

  const auth = useAuthStore()
  let syncedFor: string | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  async function pushDirty(userId: string): Promise<void> {
    const body = dirtyPayload(prefs.value, readSynced(userId))
    if (Object.keys(body).length === 0) return
    try {
      const res = await apiFetch<{ prefs: SparsePrefs }>('/api/auth/prefs', putJson(body))
      // 快照以服务端回的那份为准：它是逐键合并之后的结果，可能含着别的设备刚写的键。
      writeSynced(userId, res.prefs)
    } catch (err) {
      console.warn('[settings] 偏好没存上，本机改动照样生效', err)
    }
  }

  async function pull(userId: string): Promise<void> {
    syncing.value = true
    try {
      const res = await apiFetch<{ prefs: SparsePrefs }>('/api/auth/prefs')
      const { prefs: merged, push } = reconcile(prefs.value, res.prefs, readSynced(userId))
      // 快照先落在「已采纳云端、还没推本机改动」的那一份上。紧接着给 prefs 赋值会触发
      // 一次推送，那趟的脏键必须只剩 push 里这些——否则刚读来的云端值会被当成改动反推
      // 回去，白跑一趟不说，还会把别的设备的取值锁死在本机这一份上。
      writeSynced(userId, { ...res.prefs, ...push })
      prefs.value = merged
      syncedFor = userId
      await pushDirty(userId)
    } catch (err) {
      console.warn('[settings] 没读到账号上的偏好，先用本机这份', err)
      syncedFor = userId // 读不到也要停止重试风暴：本机这份照样能用
    } finally {
      syncing.value = false
    }
  }

  watch(
    () => auth.user?.id ?? null,
    (userId) => {
      if (!userId || userId === syncedFor) return
      void pull(userId)
    },
    { immediate: true },
  )

  watch(prefs, () => {
    const userId = auth.user?.id
    if (!userId || userId !== syncedFor) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void pushDirty(userId), SYNC_DEBOUNCE_MS)
  })

  return { prefs, theme, reduceMotion, basemapDark, syncing, open, set, show, hide }
})

if (import.meta.hot) {
  acceptHMRUpdate(useSettingsStore, import.meta.hot)
}
