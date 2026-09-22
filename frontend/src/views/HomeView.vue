<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import CreateTripDialog from '@/components/CreateTripDialog.vue'
import HomeHero from '@/components/HomeHero.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import TripCard from '@/components/TripCard.vue'
import {
  ArrowRight,
  CalendarClock,
  CheckCheck,
  Compass,
  Link2,
  LogIn,
  Plus,
  Route,
  Search,
  Settings,
  X,
  Zap,
} from '@/components/icons'
import { useCopy } from '@/composables/useCopy'
import {
  hideTrip,
  hiddenRev,
  pruneRecentTrips,
  readHiddenTrips,
  readRecentTrips,
  recordRecentTrip,
  type RecentTrip,
} from '@/composables/useRecentTrips'
import { useAuthStore, type MyTrip } from '@/stores/auth'
import { useDialogStore } from '@/stores/dialog'
import { useFeedbackStore } from '@/stores/feedback'
import { useSettingsStore } from '@/stores/settings'
import type { TripStatus, TripSummary } from '@/types/domain'
import { apiFetch } from '@/utils/api'
import { formatMoney } from '@/utils/money'
import { formatAgo } from '@/utils/time'
import { HOME_TABS, type Countdown, countdownOf, formatDateRange, needsWrapUp, phaseOf } from '@/utils/tripstatus'

/**
 * 首页＝仪表盘：状态分档 + 出发看板 + 最近一段旅行的封面卡 + 行程网格。
 *
 * 数据来源是两份的：本地「最近打开」索引（匿名用户唯一有的东西，见 [[useRecentTrips]]）
 * 与服务端 `GET /api/auth/trips`（登录后才有）。两者按 id 合并，取更近的 openedAt，
 * 内容一律走只读的 `GET /api/trips/summary` 一次批量拿——不能用 `GET /api/trips/{id}`，
 * 那个接口在登录态下会顺手记一次「打开过」，把刚排好的顺序刷掉。
 *
 * 第三份是本地「已从首页移除」黑名单：列表有两个来源，服务端那份删不动，所以「移除」
 * 只能是一台设备上的过滤规则（见 [[hideTrip]]）。
 *
 * 首页没有 WebSocket，改状态/归档只能走 `PATCH /api/trips/{id}`；服务端会顺手朝房间
 * 广播 `trip_updated`，所以开着另一扇行程页也不会看到旧状态。
 */
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const feedback = useFeedbackStore()
const dialog = useDialogStore()
const settings = useSettingsStore()
const copy = useCopy()

interface HomeTrip {
  id: string
  openedAt: string
  summary: TripSummary | null
}

const recent = ref<RecentTrip[]>([])
const hidden = ref<string[]>([])
const serverTrips = ref<MyTrip[]>([])
const summaries = ref<Record<string, TripSummary>>({})
const summaryError = ref('')
const loading = ref(true)
const tab = ref<TripStatus>('planning')

const showCreate = ref(false)

const backend = ref<{ ok: boolean; detail: string }>({ ok: false, detail: '正在连接后端…' })

async function checkBackend() {
  try {
    const data = await apiFetch<{ version: string; uptime_s: number }>('/api/health')
    backend.value = { ok: true, detail: `v${data.version}` }
  } catch (err) {
    backend.value = { ok: false, detail: String(err) }
  }
}

const merged = computed<HomeTrip[]>(() => {
  const openedAt = new Map<string, string>()
  for (const r of recent.value) openedAt.set(r.id, r.openedAt)
  for (const t of serverTrips.value) {
    const prev = openedAt.get(t.id)
    if (!prev || t.last_seen > prev) openedAt.set(t.id, t.last_seen)
  }
  const drop = new Set(hidden.value)
  return Array.from(openedAt.entries())
    .filter(([id]) => !drop.has(id))
    .map(([id, at]) => ({ id, openedAt: at, summary: summaries.value[id] ?? null }))
    .sort((a, b) => (a.openedAt < b.openedAt ? 1 : -1))
})

const trips = computed(() => merged.value)

/** 摘要还没回来的行程按 planning 算：那是服务端的默认值，也让首屏不至于空白。 */
function statusOf(t: HomeTrip): TripStatus {
  return t.summary?.status ?? 'planning'
}

const counts = computed(() => {
  const c: Record<TripStatus, number> = { planning: 0, finished: 0, archived: 0 }
  for (const t of merged.value) c[statusOf(t)] += 1
  return c
})

const inTab = computed(() => merged.value.filter((t) => statusOf(t) === tab.value))

/** SegmentedControl 的 modelValue 是 string，这里负责收窄回 TripStatus。 */
const tabModel = computed({
  get: () => tab.value as string,
  set: (value: string) => {
    if (HOME_TABS.some((t) => t.key === value)) tab.value = value as TripStatus
  },
})
const tabOptions = computed(() =>
  HOME_TABS.map((t) => ({ value: t.key as string, label: t.label, hint: `${counts.value[t.key]} 段` })),
)

function daysOut(trip: TripSummary): number | null {
  return countdownOf(trip.start_date, trip.end_date).days
}

/**
 * 出发日从前往后排，同一天出发的按「最近还在编辑」排。
 *
 * 不能写成 `(a < b ? -1 : 1)`：两段的 start_date 相等时它对 (a,b) 和 (b,a) 都回答
 * 「b 在前」，比较器一旦自相矛盾，排序结果就取决于数组原来的摆法，同一份数据两次进
 * 首页能给出两种顺序。
 */
function byDeparture(a: TripSummary, b: TripSummary): number {
  const dates = (a.start_date ?? '').localeCompare(b.start_date ?? '')
  if (dates) return dates
  return (b.updated_at ?? '').localeCompare(a.updated_at ?? '')
}

/**
 * 门面只给「规划中」这一档——已完成和已归档是归档架，那里摆大卡会把列表里的一条吸走，
 * 看着像少了一段。门面内容优先给「最近要出发的」：14 天内出发且还没走的那条最该占第一屏，
 * 其次是最近编辑过且有内容的，最后退回最新一条。全是空行程时新手至少还有个 CTA 可点。
 */
const hero = computed<TripSummary | null>(() => {
  if (tab.value !== 'planning') return null
  const ready = inTab.value.flatMap((t) => (t.summary ? [t.summary] : []))
  const soon = ready
    .filter((s) => phaseOf(s.start_date, s.end_date) === 'upcoming' && (daysOut(s) ?? 99) <= 14)
    .sort(byDeparture)
  return soon[0] ?? ready.find((s) => s.place_count > 0) ?? ready[0] ?? null
})
const grid = computed(() => inTab.value.filter((t) => t.summary?.id !== hero.value?.id))

/** 看板的一行：模板只摆数据，不重复算倒计时和金额。 */
interface BoardRow {
  trip: TripSummary
  cd: Countdown
  range: string
  money: string
  readiness: string
}

function toRow(trip: TripSummary): BoardRow {
  const checklist = trip.checklist_total ? `清单 ${trip.checklist_done}/${trip.checklist_total}` : '清单未填写'
  return {
    trip,
    cd: countdownOf(trip.start_date, trip.end_date),
    range: formatDateRange(trip.start_date, trip.end_date) || '未设置日期',
    money: trip.budget_cents
      ? `${formatMoney(trip.spent_cents)} / ${formatMoney(trip.budget_cents)}`
      : trip.spent_cents
        ? `已花 ${formatMoney(trip.spent_cents)}`
        : '未设预算',
    readiness: checklist,
  }
}

/** 出发看板：还没走的那几段按日子排队。hero 已经代表了一条，这里列其余的。 */
const upcomingRest = computed<TripSummary[]>(() => {
  if (tab.value !== 'planning') return []
  return inTab.value
    .map((t) => t.summary)
    .filter(
      (s): s is TripSummary =>
        !!s && s.id !== hero.value?.id && phaseOf(s.start_date, s.end_date) === 'upcoming',
    )
    .sort(byDeparture)
})
/** 看板只列最近三段，但「几段在排队」要说的是总数——截断的是列表，不是事实。 */
const upcoming = computed(() => upcomingRest.value.slice(0, 3).map(toRow))

/** 回来了却没归档的行程：日期已经走完，status 还停在 planning。跟门面一样只属于规划中档。 */
const wrapUps = computed<BoardRow[]>(() =>
  tab.value !== 'planning'
    ? []
    : merged.value
        .map((t) => t.summary)
        .filter((s): s is TripSummary => !!s && needsWrapUp(s.status, phaseOf(s.start_date, s.end_date)))
        .slice(0, 4)
        .map(toRow),
)

/** 侧栏的「最近编辑」走服务端那份，同样要过一遍本地黑名单。 */
const sideTrips = computed(() => {
  const drop = new Set(hidden.value)
  return serverTrips.value.filter((t) => !drop.has(t.id)).slice(0, 4)
})

async function hydrate() {
  const ids = [...new Set([...recent.value.map((r) => r.id), ...serverTrips.value.map((t) => t.id)])].filter(
    (id) => !hidden.value.includes(id),
  )
  if (!ids.length) return
  summaryError.value = ''
  try {
    const res = await apiFetch<{ trips: TripSummary[] }>(`/api/trips/summary?ids=${encodeURIComponent(ids.join(','))}`)
    const next: Record<string, TripSummary> = {}
    for (const s of res.trips) next[s.id] = s
    summaries.value = next
    // 只在「一次问全了」的前提下摘除失效条目：ids 超过接口上限时，没回话可能只是没问到。
    if (ids.length <= 24) {
      const alive = new Set(res.trips.map((t) => t.id))
      if (recent.value.some((r) => !alive.has(r.id))) recent.value = pruneRecentTrips(alive)
    }
  } catch (err) {
    summaryError.value = err instanceof Error ? err.message : String(err)
  }
}

async function refresh() {
  recent.value = readRecentTrips()
  hidden.value = readHiddenTrips()
  await auth.load()
  serverTrips.value = auth.user ? await auth.myTrips().catch(() => [] as MyTrip[]) : []
  await hydrate()
  loading.value = false
}

/**
 * 设置面板里点了「恢复」之后要重画。名单是 localStorage，首页那份 `hidden` 只是它的一个
 * 副本，所以靠 useRecentTrips 的变更版本号通知。只补 hydrate 不重跑 refresh：账号那两份
 * 请求和恢复这件事无关，而恢复出来的那条恰好还没缓存过摘要，非问一次不可。
 */
watch(hiddenRev, () => {
  hidden.value = readHiddenTrips()
  void hydrate()
})

function say(message: string) {
  feedback.show({ message })
}

function go(tripId: string) {
  recordRecentTrip(tripId)
  router.push({ name: 'trip', params: { tripId } })
}

function onCreated(tripId: string) {
  showCreate.value = false
  if (tripId) go(tripId)
}

/** 建好就记账：用户可能只复制了链接就关掉抽屉，那条行程不该因此从首页消失。 */
function onTripCreated(tripId: string) {
  if (!tripId) return
  recordRecentTrip(tripId)
  // 光记账不够，还得把它读回来——否则首页停在旧列表上，像没建成。
  void refresh()
}

/* ---------- 卡片菜单：状态、分享链接、从首页移除 ---------- */

const busy = ref<Record<string, boolean>>({})

// 回执直接写全句：HOME_TABS 的 label 自带「已」字，拼 `已${label}` 会念成「已已归档」。
const STATUS_RECEIPT: Record<TripStatus, string> = {
  planning: '已放回规划中',
  finished: '已标记为已完成',
  archived: '已归档',
}

/** receipt 传 null 表示这步是批量操作的一环，播报交给调用方，免得一人喊一句。 */
function setStatus(tripId: string, next: TripStatus, receipt: string | null = STATUS_RECEIPT[next]) {
  const card = summaries.value[tripId]
  if (!card || card.status === next || busy.value[tripId]) return
  busy.value[tripId] = true
  // 先落本地再发请求：这一步几乎不会失败，而「点了没反应」比转圈更让人以为没生效。
  summaries.value = { ...summaries.value, [tripId]: { ...card, status: next } }
  apiFetch(`/api/trips/${encodeURIComponent(tripId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: next }),
  })
    .then(() => {
      if (receipt) say(receipt)
    })
    .catch((err: Error) => {
      summaries.value = { ...summaries.value, [tripId]: card }
      summaryError.value = err.message
    })
    .finally(() => {
      busy.value[tripId] = false
    })
}

async function copyLink(tripId: string) {
  const url = `${window.location.origin}/trip/${tripId}`
  await copy(url, {
    receipt: '分享链接已复制，同行者打开即可共同编辑',
    fallbackTitle: '分享链接',
  })
}

async function removeFromHome(tripId: string) {
  const ok = await dialog.confirm({
    title: '从首页移除这段行程？',
    message:
      '仅影响当前设备的首页，行程本身与分享链接保持不变；再次打开该行程会重新出现在首页。',
    confirmLabel: '移除',
    danger: true,
  })
  if (!ok) return
  hidden.value = hideTrip(tripId)
  // 登录用户还有一份服务端足迹，能撤就撤；撤不掉也达到了「首页看不到」的目的。
  if (auth.user) {
    apiFetch(`/api/auth/trips/${encodeURIComponent(tripId)}`, { method: 'DELETE' }).catch(() => {
      /* 匿名或网络不巧：本地黑名单已经生效，不必为此报错 */
    })
  }
  say('已从首页移除')
}

function finishAllWrapUps() {
  // 先快照：setStatus 会乐观改状态，而 wrapUps 是 computed，边循环边重算会漏掉后半部分。
  const rows = wrapUps.value
  for (const row of rows) setStatus(row.trip.id, 'finished', null)
  if (rows.length) say(`已将 ${rows.length} 段行程标记为完成`)
}

/* ---------- 账号（可选）：登录入口是整页 /login，这里只管退出 ---------- */

async function logout() {
  await auth.logout()
  await refresh()
}

onMounted(() => {
  checkBackend()
  refresh()
  // ?create=1：一条链接直接唤起新建抽屉。读完立刻抹掉参数，否则刷新一次弹一次。
  if (route.query.create) {
    showCreate.value = true
    router.replace({ name: 'home' })
  }
})
</script>

<template>
  <div class="home">
    <header class="homebar">
      <h1 class="homebar__brand"><Compass class="ic homebar__mark" :size="18" /> TourPlanOpt</h1>
      <div class="homebar__right">
        <button class="iconbtn" type="button" title="设置" aria-label="设置" @click="settings.show()">
          <Settings :size="15" />
        </button>
        <template v-if="auth.user">
          <span class="homebar__who">
            <span class="homebar__avatar" :title="auth.user.name">{{ auth.user.name.slice(0, 1) }}</span>
            {{ auth.user.name }}
          </span>
          <button class="iconbtn" type="button" title="退出登录" @click="logout"><X :size="15" /></button>
        </template>
        <RouterLink v-else class="btn btn--sm homebar__auth" :to="{ name: 'login' }">
          <LogIn class="ic" :size="13" /> 登录
        </RouterLink>
        <button class="btn btn--sm btn--primary" type="button" @click="showCreate = true">
          <Plus class="ic" :size="13" /> 新建行程
        </button>
      </div>
    </header>

    <div class="home__wrap">
      <main class="home__main">
        <div v-if="trips.length" class="tabs reveal reveal--fade" :style="{ '--base': '40ms' }">
          <SegmentedControl v-model="tabModel" :options="tabOptions" label="行程状态" />
        </div>

        <!-- 待收尾排在最上面：这是唯一一个「再不做就忘了」的动作，别的都能等。 -->
        <section v-if="wrapUps.length" class="board board--warn card reveal" :style="{ '--base': '120ms' }">
          <div class="board__head">
            <h2 class="board__title"><CheckCheck class="ic" :size="14" /> 已结束，待收尾</h2>
            <button v-if="wrapUps.length > 1" class="btn btn--sm" type="button" @click="finishAllWrapUps">全部标记完成</button>
          </div>
          <ul class="board__list">
            <li v-for="row in wrapUps" :key="row.trip.id" class="board__item">
              <button class="board__row" type="button" @click="go(row.trip.id)">
                <span class="board__name">{{ row.trip.title || '未命名行程' }}</span>
                <span class="tiny muted board__meta">{{ row.range }} · {{ row.trip.place_count }} 个地点</span>
              </button>
              <button class="btn btn--sm btn--ghost" type="button" @click="setStatus(row.trip.id, 'finished')">标记完成</button>
            </li>
          </ul>
        </section>

        <section v-if="upcoming.length" class="board card reveal" :style="{ '--base': '170ms' }">
          <div class="board__head">
            <h2 class="board__title"><CalendarClock class="ic" :size="14" /> 接下来要走</h2>
            <span class="tiny muted">另有 {{ upcomingRest.length }} 段在排队</span>
          </div>
          <ul class="board__list">
            <li v-for="row in upcoming" :key="row.trip.id" class="board__item">
              <button class="board__row" type="button" @click="go(row.trip.id)">
                <span class="board__count" :class="`board__count--${row.cd.tone}`">{{ row.cd.label }}</span>
                <span class="board__name">{{ row.trip.title || '未命名行程' }}</span>
                <span class="tiny muted board__meta">{{ row.range }} · {{ row.readiness }} · {{ row.money }}</span>
              </button>
              <ArrowRight class="ic board__go" :size="14" />
            </li>
          </ul>
        </section>

        <HomeHero
          v-if="hero"
          :trip="hero"
          class="reveal reveal--lg"
          :style="{ '--base': '60ms' }"
          @open="go(hero.id)"
          @finish="setStatus(hero.id, 'finished')"
        />

        <template v-else-if="!loading && !trips.length">
          <section class="welcome card reveal reveal--lg" :style="{ '--base': '60ms' }">
            <div class="welcome__text">
              <h1>把想去的地方，<br />变成走得完的行程</h1>
              <p class="muted">创建一个行程，把链接分享给同行的人。多人可同时添加地点，路线自动计算。</p>
              <button class="btn btn--primary" type="button" @click="showCreate = true">
                <Plus class="ic" :size="14" /> 新建第一段行程
              </button>
            </div>
            <svg class="welcome__art" viewBox="0 0 220 120" aria-hidden="true">
              <path
                d="M14 104C46 104 50 34 86 34s40 62 74 62 30-44 46-44"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
                stroke-linecap="round"
                stroke-dasharray="2 9"
              />
              <circle cx="14" cy="104" r="5" fill="currentColor" />
              <circle cx="160" cy="96" r="5" fill="currentColor" />
              <circle cx="206" cy="52" r="5" fill="currentColor" />
            </svg>
          </section>

          <section class="steps reveal reveal--fade" :style="{ '--base': '150ms' }">
            <div class="steps__item">
              <span class="steps__no">1</span>
              <div>
                <h3 class="steps__title">创建行程</h3>
                <p class="steps__desc">只需填写标题，城市与天数可后续补充。</p>
              </div>
            </div>
            <div class="steps__item">
              <span class="steps__no">2</span>
              <div>
                <h3 class="steps__title">分享链接</h3>
                <p class="steps__desc">同行者打开链接即可加入，无需注册账号。</p>
              </div>
            </div>
            <div class="steps__item">
              <span class="steps__no">3</span>
              <div>
                <h3 class="steps__title">协同排线</h3>
                <p class="steps__desc">地点汇总后生成时间线，顺序可一键优化。</p>
              </div>
            </div>
          </section>
        </template>

        <section v-if="trips.length" class="sec">
          <div v-if="grid.length" class="sec__head reveal reveal--fade" :style="{ '--base': '220ms' }">
            <h2>我的行程</h2>
            <span class="tiny muted">{{ grid.length }} 段</span>
          </div>
          <div v-if="grid.length" class="sec__grid">
            <TripCard
              v-for="(t, i) in grid"
              :key="t.id"
              :trip="t.summary"
              :trip-id="t.id"
              class="reveal"
              :style="{ '--i': i > 6 ? 6 : i, '--base': '260ms' }"
              @status="setStatus(t.id, $event)"
              @hide="removeFromHome(t.id)"
              @copy="copyLink(t.id)"
            />
            <button
              class="newcard reveal"
              type="button"
              :style="{ '--i': grid.length > 6 ? 7 : grid.length, '--base': '260ms' }"
              @click="showCreate = true"
            >
              <Plus class="ic" :size="18" />
              <span>再建一段行程</span>
            </button>
          </div>
          <p v-else-if="!loading && !inTab.length" class="tiny sec__empty">
            {{ tab === 'archived' ? '归档区暂无行程。行程结束后可先标记完成，再归档以保持首页简洁。' : '当前分类暂无行程。' }}
          </p>
        </section>

        <p v-if="summaryError" class="tiny home__note">{{ summaryError }}，行程内容加载失败，请稍后重试。</p>
      </main>

      <aside class="home__side">
        <section class="card side reveal" :style="{ '--base': '300ms' }">
          <h2 class="side__title"><Route class="ic" :size="14" /> 怎么用</h2>
          <ol class="side__steps">
            <li><Search class="ic" :size="13" /> 搜索地点，或从「发现」中添加；在地图上右键也可直接加入</li>
            <li><Zap class="ic" :size="13" /> 添加地点后自动生成时间线，点击优化可获得最短路线</li>
            <li><Link2 class="ic" :size="13" /> 同一条链接多人同时编辑，改动实时同步</li>
          </ol>
        </section>

        <section v-if="sideTrips.length" class="card side reveal" :style="{ '--base': '360ms' }">
          <h2 class="side__title">最近编辑</h2>
          <ul class="side__list">
            <li v-for="t in sideTrips" :key="t.id">
              <button class="side__row" type="button" @click="go(t.id)">
                <span class="side__row-title">{{ t.title || '未命名行程' }}</span>
                <span class="tiny muted">{{ formatAgo(t.last_seen) }}</span>
              </button>
            </li>
          </ul>
        </section>
      </aside>
    </div>

    <footer class="home__foot reveal reveal--fade" :style="{ '--base': '420ms' }">
      <span class="dot dot--pulse" :class="backend.ok ? 'dot--ok' : 'dot--warn'" />
      <span class="tiny muted">{{ backend.ok ? '本机服务正常' : '本机服务没有连接上' }}</span>
      <button v-if="!backend.ok" class="btn btn--sm btn--ghost" type="button" @click="checkBackend">重试</button>
      <details v-if="!backend.ok" class="home__foot-tech">
        <summary class="tiny muted">详情</summary>
        <p class="tiny home__foot-hint">
          在 backend/ 目录执行
          <code>uv run uvicorn app.main:app --reload</code>
        </p>
        <p class="tiny home__foot-hint mono">原始错误：{{ backend.detail }}</p>
      </details>
    </footer>

    <button class="fab reveal reveal--pop" type="button" :style="{ '--base': '520ms' }" @click="showCreate = true">
      <Plus class="ic" :size="16" /> 新建行程
    </button>

    <CreateTripDialog v-if="showCreate" @created="onTripCreated" @done="onCreated" @cancel="showCreate = false" />
  </div>
</template>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

/* ---------- 应用栏 ---------- */
.homebar {
  position: sticky;
  top: 0;
  z-index: var(--z-nav);
  display: flex;
  gap: 10px;
  align-items: center;
  flex: 0 0 var(--header-h);
  height: var(--header-h);
  padding: 0 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}

.homebar__brand {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  font-size: calc(15px * var(--fs-scale));
  font-weight: 700;
  letter-spacing: 0.01em;
}

.homebar__mark {
  color: var(--accent);
}

.homebar__right {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-left: auto;
}

/* 登录入口是个链接（跳到整页 /login），但读起来要跟旁边的按钮一样是贴纸：
   .btn 那套描边与字色是写给 button 的，链接得自己把下划线和链接蓝抹掉。 */
.homebar__auth {
  color: inherit;
  text-decoration: none;
}

.homebar__who {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
  color: var(--text-2);
}

.homebar__avatar {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  color: var(--accent-ink);
  font-weight: 700;
  background: var(--accent);
  border-radius: 50%;
}

/* ---------- 版式 ---------- */
.home__wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 312px;
  gap: 22px;
  width: 100%;
  max-width: 1240px;
  margin: 0 auto;
  padding: 22px 20px 24px;
}

.home__main {
  display: flex;
  flex-direction: column;
  gap: 22px;
  min-width: 0;
}

.home__side {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sec__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 10px;
}
.sec__head h2 {
  font-size: calc(15px * var(--fs-scale));
}
.sec__head .muted {
  margin-left: auto;
}

.sec__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(212px, 1fr));
  gap: 12px;
}

.sec__empty {
  padding: 22px 16px;
  margin: 0;
  color: var(--text-3);
  text-align: center;
  border: 1px dashed var(--hairline);
  border-radius: var(--radius);
}

/* ---------- 状态分档 + 出发看板 ---------- */
.tabs {
  max-width: 460px;
}

.board {
  padding: 12px 14px 6px;
}
.board__head {
  display: flex;
  gap: 8px;
  align-items: center;
}
.board__title {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text-2);
}
.board__head .muted {
  margin-left: auto;
}
.board--warn {
  background: linear-gradient(140deg, var(--warn-soft), var(--surface) 62%);
  border-color: var(--warn-border);
}
.board--warn .board__title .ic {
  color: var(--warn);
}

.board__list {
  display: flex;
  flex-direction: column;
  padding: 0;
  margin: 8px 0 0;
  list-style: none;
}
.board__item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  border-top: 1px dashed var(--border);
}
.board__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
  padding: 0;
  text-align: left;
  background: none;
  border: 0;
}
.board__name {
  overflow: hidden;
  font-size: calc(14px * var(--fs-scale));
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board__meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board__count {
  align-self: flex-start;
  padding: 1px 8px;
  margin-bottom: 2px;
  font-size: calc(11px * var(--fs-scale));
  font-weight: 600;
  color: var(--text-2);
  background: var(--surface-2);
  border-radius: var(--radius-pill);
}
/* 实心用 --ember-deep（深陶土），字用 --ember-deep-ink（白）——不能拿 --ember-ink，
   那是给亮陶土块配的近黑墨，压到深陶土上只剩 1.4:1。两个方向都算过：亮主题 6.5:1，
   深色主题下 --ember-deep 反成浅桃、ink 跟着翻回近黑，仍然 7:1 以上。 */
.board__count--soon {
  color: var(--ember-deep-ink);
  background: var(--ember-deep);
}
.board__go {
  flex: 0 0 auto;
  color: var(--text-3);
  opacity: 0;
  transition:
    opacity var(--dur) var(--ease-out),
    transform var(--dur) var(--ease-out);
}
.board__item:hover .board__go {
  opacity: 1;
  transform: translateX(3px);
}

/* ---------- 首次来的欢迎卡 ---------- */
.welcome {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 200px;
  gap: 16px;
  align-items: center;
  padding: 24px;
  background: linear-gradient(140deg, var(--ember-soft), var(--surface) 58%);
}

.welcome__text {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
}
.welcome__text h1 {
  font-size: clamp(22px, 3.4vw, 30px);
  line-height: 1.25;
}
.welcome__text p {
  margin: 0;
  max-width: 46ch;
}

.welcome__art {
  width: 100%;
  color: var(--ember);
  opacity: 0.55;
}

/* 夜航图是蓝灰的，暖棕块落在首屏会显脏：深色态欢迎卡跟着强调蓝走。 */
:global(html[data-theme='dark']) .welcome {
  background: linear-gradient(140deg, var(--accent-soft), var(--surface) 58%);
}
:global(html[data-theme='dark']) .welcome__art {
  color: var(--accent);
  opacity: 0.5;
}

.steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-top: 16px;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: color-mix(in srgb, var(--surface) 62%, transparent);
}
.steps__item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.steps__no {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-family: var(--mono);
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
}
.steps__title {
  margin: 0 0 3px;
  font-size: calc(14px * var(--fs-scale));
  font-weight: 600;
}
.steps__desc {
  margin: 0;
  font-size: calc(12.5px * var(--fs-scale));
  line-height: 1.55;
  color: var(--text-2);
}

/* ---------- 侧栏 ---------- */
.side {
  padding: 14px;
}
.side__title {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 10px;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text-2);
}
.side__steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 0;
  margin: 0;
  list-style: none;
  counter-reset: step;
}
.side__steps li {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text-2);
}
.side__steps .ic {
  margin-top: 3px;
  color: var(--ember-deep);
}

.side__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 0;
  list-style: none;
}
.side__row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  width: 100%;
  padding: 6px 8px;
  text-align: left;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  transition:
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}
.side__row:hover {
  background: var(--surface-2);
  border-color: var(--border);
  transform: translateX(2px);
}
.side__row-title {
  overflow: hidden;
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 新建卡 ---------- */
.newcard {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 168px;
  color: var(--text-2);
  background: transparent;
  border: 2px dashed var(--hairline);
  border-radius: var(--radius);
  transition:
    color var(--dur) var(--ease-out),
    border-color var(--dur) var(--ease-out),
    background var(--dur) var(--ease-out);
}
.newcard:hover {
  color: var(--accent-strong);
  background: var(--surface);
  border-color: var(--accent);
}
.newcard span {
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
}

/* ---------- 页脚状态行 ---------- */
.home__foot {
  display: flex;
  gap: 7px;
  align-items: center;
  padding: 10px 20px 18px;
  margin-top: auto;
  border-top: 1px solid var(--border-faint);
}
.home__foot-hint {
  margin: 2px 0 0;
  color: var(--text-3);
}
.home__foot-tech {
  flex: 1 0 100%;
}
.home__foot-tech summary {
  width: fit-content;
  color: var(--text-3);
  cursor: pointer;
  list-style: none;
}
.home__foot-tech summary::-webkit-details-marker {
  display: none;
}
.home__note {
  padding: 8px 10px;
  color: var(--warn);
  background: var(--warn-soft);
  border: 1px solid var(--warn-border);
  border-radius: var(--radius-sm);
}

/* ---------- FAB ---------- */
.fab {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: var(--z-bar);
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 11px 18px;
  color: var(--accent-ink);
  font-weight: 600;
  background: var(--accent);
  border: 0;
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-lg);
  transition:
    background var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}
.fab:hover {
  background: var(--accent-strong);
}
.fab:active {
  transform: scale(0.97);
}

/* 桌面右上角的栏里本来就有同一颗「新建行程」，再挂一枚常驻浮标只是把同一个动作说两遍，
   还常年占着右下角。手机上顶栏挤，才需要它。 */
@media (min-width: 861px) {
  .fab {
    display: none;
  }
}

@media (max-width: 1000px) {
  .home__wrap {
    grid-template-columns: minmax(0, 1fr);
    padding: 16px 14px 20px;
  }
  .welcome {
    grid-template-columns: minmax(0, 1fr);
  }
  .welcome__art {
    display: none;
  }
  .steps {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
}

/* 窄屏顶栏放不下两个按钮，文字让给图标，FAB 已经是主入口了。 */
@media (max-width: 620px) {
  .homebar {
    padding: 0 14px;
  }
  .homebar__right .btn--primary {
    display: none;
  }
}
</style>
