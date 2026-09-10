<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppModal from '@/components/AppModal.vue'
import CreateTripDialog from '@/components/CreateTripDialog.vue'
import HomeHero from '@/components/HomeHero.vue'
import TripCard from '@/components/TripCard.vue'
import { Compass, Link2, LogIn, Plus, Route, Search, X, Zap } from '@/components/icons'
import { pruneRecentTrips, readRecentTrips, recordRecentTrip, type RecentTrip } from '@/composables/useRecentTrips'
import { useAuthStore, type MyTrip } from '@/stores/auth'
import type { TripSummary } from '@/types/domain'
import { apiFetch } from '@/utils/api'
import { formatAgo } from '@/utils/time'

/**
 * 首页＝仪表盘：最近一段旅行的封面卡 + 我的行程网格。
 *
 * 数据来源是两份的：本地「最近打开」索引（匿名用户唯一有的东西，见 [[useRecentTrips]]）
 * 与服务端 `GET /api/auth/trips`（登录后才有）。两者按 id 合并，取更近的 openedAt，
 * 内容一律走只读的 `GET /api/trips/summary` 一次批量拿——不能用 `GET /api/trips/{id}`，
 * 那个接口在登录态下会顺手记一次「打开过」，把刚排好的顺序刷掉。
 */
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

interface HomeTrip {
  id: string
  openedAt: string
  summary: TripSummary | null
}

const recent = ref<RecentTrip[]>([])
const serverTrips = ref<MyTrip[]>([])
const summaries = ref<Record<string, TripSummary>>({})
const summaryError = ref('')
const loading = ref(true)

const showCreate = ref(false)
const showAuth = ref(false)

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
  return Array.from(openedAt.entries())
    .map(([id, at]) => ({ id, openedAt: at, summary: summaries.value[id] ?? null }))
    .sort((a, b) => (a.openedAt < b.openedAt ? 1 : -1))
})

const trips = computed(() => merged.value)
/**
 * 门面优先给「有内容」的行程：一条 0 地点的未命名行程刚建就排最新，若直接当 hero，
 * 首页第一眼看去是一张空卡。全是空行程时才退回最新那条，新手至少还有个 CTA 可点。
 */
const hero = computed<TripSummary | null>(() => {
  const ready = merged.value.flatMap((t) => (t.summary ? [t.summary] : []))
  return ready.find((s) => s.place_count > 0) ?? ready[0] ?? null
})
const grid = computed(() => merged.value.filter((t) => t.summary?.id !== hero.value?.id))

async function hydrate() {
  const ids = [...new Set([...recent.value.map((r) => r.id), ...serverTrips.value.map((t) => t.id)])]
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
  await auth.load()
  serverTrips.value = auth.user ? await auth.myTrips().catch(() => [] as MyTrip[]) : []
  await hydrate()
  loading.value = false
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

/* ---------- 账号（可选）：顶栏一个入口，表单只在这里出现一次 ---------- */

const authMode = ref<'login' | 'register'>('login')
const authName = ref('')
const authPassword = ref('')

async function submitAuth() {
  try {
    if (authMode.value === 'login') await auth.login(authName.value.trim(), authPassword.value)
    else await auth.register(authName.value.trim(), authPassword.value)
    authPassword.value = ''
    showAuth.value = false
    await refresh()
  } catch {
    /* auth.error 已经带着服务端的话，交给对话框里那行红字 */
  }
}

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
        <template v-if="auth.user">
          <span class="homebar__who">
            <span class="homebar__avatar" :title="auth.user.name">{{ auth.user.name.slice(0, 1) }}</span>
            {{ auth.user.name }}
          </span>
          <button class="iconbtn" type="button" title="退出登录" @click="logout"><X :size="15" /></button>
        </template>
        <button v-else class="btn btn--sm" type="button" @click="showAuth = true">
          <LogIn class="ic" :size="13" /> 登录
        </button>
        <button class="btn btn--sm btn--primary" type="button" @click="showCreate = true">
          <Plus class="ic" :size="13" /> 新建行程
        </button>
      </div>
    </header>

    <div class="home__wrap">
      <main class="home__main">
        <HomeHero
          v-if="hero"
          :trip="hero"
          class="reveal reveal--lg"
          :style="{ '--base': '60ms' }"
          @open="go(hero.id)"
        />

        <section v-else-if="!loading" class="welcome card reveal reveal--lg" :style="{ '--base': '60ms' }">
          <div class="welcome__text">
            <h1>把想去的地方，<br />变成走得完的行程</h1>
            <p class="muted">建一个行程，把链接发给朋友。大家同时往里丢地点，路线我们算。</p>
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

        <section v-if="trips.length" class="sec">
          <div class="sec__head reveal reveal--fade" :style="{ '--base': '220ms' }">
            <h2>我的行程</h2>
            <span class="tiny muted">{{ trips.length }} 段</span>
          </div>
          <div class="sec__grid">
            <TripCard
              v-for="(t, i) in grid"
              :key="t.id"
              :trip="t.summary"
              :trip-id="t.id"
              class="reveal"
              :style="{ '--i': i > 6 ? 6 : i, '--base': '260ms' }"
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
        </section>

        <p v-if="summaryError" class="tiny home__note">{{ summaryError }}——行程内容没取到，稍后重试。</p>
      </main>

      <aside class="home__side">
        <section class="card side reveal" :style="{ '--base': '300ms' }">
          <h2 class="side__title"><Route class="ic" :size="14" /> 怎么用</h2>
          <ol class="side__steps">
            <li><Search class="ic" :size="13" /> 搜地点，或从「发现」里挑，地图右键也能直接加</li>
            <li><Zap class="ic" :size="13" /> 丢进来就自动排时间线，点优化换最短路线</li>
            <li><Link2 class="ic" :size="13" /> 同一条链接多人同时编辑，改动实时同步</li>
          </ol>
        </section>

        <section v-if="auth.user && serverTrips.length" class="card side reveal" :style="{ '--base': '360ms' }">
          <h2 class="side__title">最近编辑</h2>
          <ul class="side__list">
            <li v-for="t in serverTrips.slice(0, 4)" :key="t.id">
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
      <span class="tiny muted">{{ backend.ok ? `服务正常 ${backend.detail}` : '后端未连接' }}</span>
      <button v-if="!backend.ok" class="btn btn--sm btn--ghost" type="button" @click="checkBackend">重试</button>
      <span v-if="!backend.ok" class="tiny muted home__foot-hint">
        在 backend/ 下运行 uv run uvicorn app.main:app --reload
      </span>
    </footer>

    <button class="fab reveal reveal--pop" type="button" :style="{ '--base': '520ms' }" @click="showCreate = true">
      <Plus class="ic" :size="16" /> 新建行程
    </button>

    <CreateTripDialog v-if="showCreate" @created="onTripCreated" @done="onCreated" @cancel="showCreate = false" />

    <AppModal v-if="showAuth" title="登录或注册" sub="不登录也能建行程；登录是为了留下「我的行程」" @close="showAuth = false">
      <form class="authdlg" @submit.prevent="submitAuth">
        <div class="authdlg__tabs">
          <button
            class="authdlg__tab"
            :class="{ 'authdlg__tab--on': authMode === 'login' }"
            type="button"
            @click="authMode = 'login'"
          >
            登录
          </button>
          <button
            class="authdlg__tab"
            :class="{ 'authdlg__tab--on': authMode === 'register' }"
            type="button"
            @click="authMode = 'register'"
          >
            注册
          </button>
        </div>
        <input v-model="authName" class="input" type="text" maxlength="20" placeholder="昵称" autocomplete="username" />
        <input
          v-model="authPassword"
          class="input"
          type="password"
          :placeholder="authMode === 'register' ? '密码（至少 6 位）' : '密码'"
          autocomplete="current-password"
        />
        <p v-if="auth.error" class="tiny authdlg__err">{{ auth.error.message }}</p>
        <button class="btn btn--primary" type="submit" :disabled="auth.busy || !authName.trim() || authPassword.length < 6">
          {{ auth.busy ? '请稍候…' : authMode === 'login' ? '登录' : '注册并登录' }}
        </button>
      </form>
    </AppModal>
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
  font-size: 15px;
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

.homebar__who {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  font-size: 13px;
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
  font-size: 15px;
}
.sec__head .muted {
  margin-left: auto;
}

.sec__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(212px, 1fr));
  gap: 12px;
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

/* ---------- 侧栏 ---------- */
.side {
  padding: 14px;
}
.side__title {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 10px;
  font-size: 13px;
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
  font-size: 13px;
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
  font-size: 13px;
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
  border: 2px dashed var(--border-strong);
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
  font-size: 13px;
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
  color: var(--text-3);
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
  border-radius: 999px;
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
