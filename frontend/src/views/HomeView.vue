<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore, type MyTrip } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const title = ref('')
const city = ref('')
const travelMode = ref<'driving' | 'walking' | 'straight'>('driving')
const creating = ref(false)
const createError = ref('')

const backend = ref<{ ok: boolean; label: string; detail: string }>({
  ok: false,
  label: '检查中',
  detail: '正在连接后端…',
})

async function checkBackend() {
  try {
    const res = await fetch('/api/health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    backend.value = {
      ok: true,
      label: '后端已连接',
      detail: `v${data.version} · 已运行 ${Math.round(data.uptime_s)}s`,
    }
  } catch (err) {
    backend.value = {
      ok: false,
      label: '后端未连接',
      detail: `请先在 backend/ 下运行：uv run uvicorn app.main:app --reload（${String(err)}）`,
    }
  }
}

// -- account: optional; an account unlocks the 我的行程 history -------------------

const authMode = ref<'login' | 'register'>('login')
const authName = ref('')
const authPassword = ref('')
const myTrips = ref<MyTrip[] | null>(null)
const tripsLoading = ref(false)

async function submitAuth() {
  try {
    if (authMode.value === 'login') await auth.login(authName.value.trim(), authPassword.value)
    else await auth.register(authName.value.trim(), authPassword.value)
    authPassword.value = ''
    await loadMyTrips()
  } catch {
    // auth.error already carries the server message for the banner
  }
}

async function logout() {
  await auth.logout()
  myTrips.value = null
}

async function loadMyTrips() {
  if (!auth.user) return
  tripsLoading.value = true
  try {
    myTrips.value = await auth.myTrips()
  } catch {
    myTrips.value = null
  } finally {
    tripsLoading.value = false
  }
}

function fmtSeen(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}月${d.getDate()}日`
}

onMounted(async () => {
  checkBackend()
  await auth.load()
  await loadMyTrips()
})

async function createTrip() {
  creating.value = true
  createError.value = ''
  try {
    const res = await fetch('/api/trips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title.value.trim() || undefined,
        city: city.value.trim() || undefined,
        travel_mode: travelMode.value,
      }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`)
    const data = await res.json()
    router.push({ name: 'trip', params: { tripId: data.trip_id } })
  } catch (err) {
    createError.value = String(err)
  } finally {
    creating.value = false
  }
}

function openExisting() {
  const id = window.prompt('输入行程 ID（分享链接 /trip/ 后面那串）')
  if (id && id.trim()) {
    router.push({ name: 'trip', params: { tripId: id.trim() } })
  }
}
</script>

<template>
  <div class="home">
    <div class="home__card card">
      <header class="home__head">
        <h1>把想去的地方，<br />变成走得完的行程</h1>
        <p class="muted">建一个行程，把链接发给朋友。大家同时往里丢地点，路线我们算。</p>
      </header>

      <div class="banner" :class="backend.ok ? 'banner--ok' : 'banner--warn'">
        <span class="dot" :class="backend.ok ? 'dot--ok' : 'dot--warn'" />
        <div class="banner__body">
          <div class="banner__title">{{ backend.label }}</div>
          <div class="banner__hint tiny">{{ backend.detail }}</div>
        </div>
        <button class="btn btn--sm btn--ghost" type="button" @click="checkBackend">重试</button>
      </div>

      <!-- 登录 / 注册：可选。登录后这里变成「我的行程」历史足迹。 -->
      <div v-if="!auth.user" class="card authcard">
        <div class="authcard__tabs">
          <button
            class="authcard__tab"
            :class="{ 'authcard__tab--on': authMode === 'login' }"
            type="button"
            @click="authMode = 'login'"
          >
            登录
          </button>
          <button
            class="authcard__tab"
            :class="{ 'authcard__tab--on': authMode === 'register' }"
            type="button"
            @click="authMode = 'register'"
          >
            注册
          </button>
        </div>
        <form class="authcard__form" @submit.prevent="submitAuth">
          <input
            v-model="authName"
            class="input"
            type="text"
            maxlength="20"
            placeholder="昵称"
            autocomplete="username"
          />
          <input
            v-model="authPassword"
            class="input"
            type="password"
            :placeholder="authMode === 'register' ? '密码（至少 6 位）' : '密码'"
            autocomplete="current-password"
          />
          <button class="btn btn--primary" type="submit" :disabled="auth.busy || !authName.trim() || authPassword.length < 6">
            {{ auth.busy ? '请稍候…' : authMode === 'login' ? '登录' : '注册并登录' }}
          </button>
        </form>
        <div v-if="auth.error" class="tiny authcard__err">{{ auth.error.message }}</div>
        <p class="tiny muted">不登录也能用：直接建行程，把链接发给朋友就行。登录是为了留下「我的行程」。</p>
      </div>

      <div v-else class="authcard authcard--in">
        <div class="authcard__me">
          <span class="authcard__avatar" :title="auth.user.name">{{ auth.user.name.slice(0, 1) }}</span>
          <strong>{{ auth.user.name }}</strong>
          <button class="btn btn--sm btn--ghost" type="button" @click="logout">退出</button>
        </div>
        <div v-if="tripsLoading" class="tiny muted">正在读取我的行程…</div>
        <ul v-else-if="myTrips && myTrips.length" class="mytrips">
          <li v-for="t in myTrips" :key="t.id">
            <button class="mytrips__item" type="button" @click="router.push({ name: 'trip', params: { tripId: t.id } })">
              <span class="mytrips__title">{{ t.title || '未命名行程' }}</span>
              <span class="tiny muted">
                {{ [t.city, t.place_count + ' 地点', '最近 ' + fmtSeen(t.last_seen)].filter(Boolean).join(' · ') }}
              </span>
            </button>
          </li>
        </ul>
        <p v-else class="tiny muted">还没有历史行程——建一个，之后都在这里。</p>
      </div>

      <form class="home__form" @submit.prevent="createTrip">
        <div class="field">
          <label for="title">行程名称</label>
          <input
            id="title"
            v-model="title"
            class="input"
            placeholder="例如：五一 · 苏州三日游"
            autocomplete="off"
          />
        </div>

        <div class="field">
          <label for="city">目的地城市</label>
          <input
            id="city"
            v-model="city"
            class="input"
            placeholder="例如：苏州（用于限定地点搜索范围）"
            autocomplete="off"
          />
        </div>

        <div class="field">
          <label for="mode">默认交通方式</label>
          <select id="mode" v-model="travelMode" class="input">
            <option value="driving">驾车 / 打车</option>
            <option value="walking">步行</option>
            <option value="straight">直线（不看路况）</option>
          </select>
        </div>

        <div v-if="createError" class="banner banner--danger">
          <div class="banner__body">
            <div class="banner__title">创建失败</div>
            <div class="banner__hint tiny mono">{{ createError }}</div>
          </div>
        </div>

        <div class="home__actions">
          <button class="btn btn--primary" type="submit" :disabled="creating || !backend.ok">
            {{ creating ? '创建中…' : '创建行程' }}
          </button>
          <button class="btn" type="button" @click="openExisting">打开已有行程</button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.authcard {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
}

.authcard__tabs {
  display: flex;
  gap: 4px;
}

.authcard__tab {
  flex: 1;
  padding: 7px 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}

.authcard__tab--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.authcard__form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.authcard__err {
  color: var(--danger);
}

.authcard--in {
  gap: 12px;
}

.authcard__me {
  display: flex;
  gap: 10px;
  align-items: center;
}

.authcard__me .btn {
  margin-left: auto;
}

.authcard__avatar {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  color: #fff;
  font-weight: 700;
  background: var(--accent);
  border-radius: 50%;
}

.mytrips {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.mytrips__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 10px 12px;
  text-align: left;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.mytrips__item:hover {
  border-color: var(--accent);
}

.mytrips__title {
  font-weight: 600;
}

.home {
  display: grid;
  place-items: center;
  height: 100%;
  padding: 24px;
}
.home__card {
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  max-width: 440px;
  padding: 26px;
}
.home__head h1 {
  font-size: 24px;
  line-height: 1.25;
  letter-spacing: 0.01em;
}
.home__head p {
  margin: 4px 0 0;
}
.home__form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.home__actions {
  display: flex;
  gap: 10px;
}
</style>
