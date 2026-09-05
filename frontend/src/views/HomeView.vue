<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

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

onMounted(checkBackend)

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
        <h1>TourPlanOpt</h1>
        <p class="muted">几个人一起把行程排好 —— 同时编辑，自动算路。</p>
      </header>

      <div class="banner" :class="backend.ok ? 'banner--ok' : 'banner--warn'">
        <span class="dot" :class="backend.ok ? 'dot--ok' : 'dot--warn'" />
        <div class="banner__body">
          <div class="banner__title">{{ backend.label }}</div>
          <div class="banner__hint tiny">{{ backend.detail }}</div>
        </div>
        <button class="btn btn--sm btn--ghost" type="button" @click="checkBackend">重试</button>
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
  font-size: 22px;
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
