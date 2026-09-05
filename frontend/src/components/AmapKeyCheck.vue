<script setup lang="ts">
/**
 * The M1 diagnostic banner. Built once, kept forever.
 *
 * It renders BOTH halves of the key check, because the backend cannot validate a
 * JS API key (that is checked in-browser against a domain whitelist) and the
 * browser cannot validate the Web服务 key (it never sees it).
 */
import { computed, onMounted, ref } from 'vue'

import { ensureAmap, useAmap } from '@/composables/useAmap'

interface KeyCheck {
  name: string
  ok: boolean
  present: boolean
  detail: string
  hint: string
  infocode?: string
  latency_ms?: number
}

const { status, diagnostics } = useAmap()

const backendLoading = ref(true)
const backendError = ref('')
const webKey = ref<KeyCheck | null>(null)
const jsKey = ref<KeyCheck | null>(null)

const dismissed = ref(false)

// The backend already reports "AMAP_JS_KEY is not configured". Suppressing the
// browser's duplicate of that same diagnosis keeps the panel to one line per
// real problem, while still surfacing what only the browser can detect: load
// failures, INVALID_USER_SCODE, and the domain-whitelist warning.
const frontendDiagnostics = computed(() => {
  const backendCoveredMissingKey = jsKey.value !== null && !jsKey.value.present
  return diagnostics.value.filter(
    (d) => !(backendCoveredMissingKey && d.title.includes('AMAP_JS_KEY'))
  )
})

async function checkBackend() {
  backendLoading.value = true
  backendError.value = ''
  try {
    // Costs exactly one Amap call, so this is on-demand only — never on a timer.
    const res = await fetch('/api/amap/health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    webKey.value = data.web_key
    jsKey.value = data.js_key
  } catch (err) {
    backendError.value = `${err}（后端没起来？在 backend/ 下运行 uv run uvicorn app.main:app --reload）`
  } finally {
    backendLoading.value = false
  }
}

async function recheckAll() {
  dismissed.value = false
  await Promise.all([checkBackend(), ensureAmap().catch(() => undefined)])
}

onMounted(() => {
  void checkBackend()
  void ensureAmap().catch(() => undefined)
})

const allGood = () =>
  !backendLoading.value &&
  !backendError.value &&
  webKey.value?.ok === true &&
  jsKey.value?.ok === true &&
  status.value === 'ready' &&
  frontendDiagnostics.value.every((d) => d.level !== 'danger')
</script>

<template>
  <div v-if="!dismissed" class="keycheck">
    <div v-if="allGood()" class="banner banner--ok">
      <span class="dot dot--ok" />
      <div class="banner__body">
        <div class="banner__title">高德 Key 自检通过</div>
        <div class="banner__hint tiny">
          Web服务 Key 探活 {{ webKey?.latency_ms }}ms · JS API {{ status }}
        </div>
      </div>
      <button class="btn btn--sm btn--ghost" type="button" @click="dismissed = true">收起</button>
    </div>

    <template v-else>
      <div v-if="backendError" class="banner banner--danger">
        <span class="dot dot--danger" />
        <div class="banner__body">
          <div class="banner__title">无法读取后端自检结果</div>
          <div class="banner__hint tiny mono">{{ backendError }}</div>
        </div>
      </div>

      <div
        v-if="webKey && !webKey.ok"
        class="banner"
        :class="webKey.present ? 'banner--danger' : 'banner--warn'"
      >
        <span class="dot dot--danger" />
        <div class="banner__body">
          <div class="banner__title">
            后端 <code>{{ webKey.name }}</code>：{{ webKey.detail }}
          </div>
          <div v-if="webKey.infocode" class="banner__hint tiny mono">
            infocode {{ webKey.infocode }}
          </div>
          <div class="banner__hint">{{ webKey.hint }}</div>
        </div>
      </div>

      <div
        v-if="jsKey && !jsKey.ok"
        class="banner"
        :class="jsKey.present ? 'banner--danger' : 'banner--warn'"
      >
        <span class="dot dot--danger" />
        <div class="banner__body">
          <div class="banner__title">
            前端 <code>{{ jsKey.name }}</code>：{{ jsKey.detail }}
          </div>
          <div class="banner__hint">{{ jsKey.hint }}</div>
        </div>
      </div>

      <div
        v-for="(d, i) in frontendDiagnostics"
        :key="i"
        class="banner"
        :class="{
          'banner--ok': d.level === 'ok',
          'banner--warn': d.level === 'warn',
          'banner--danger': d.level === 'danger',
        }"
      >
        <span
          class="dot"
          :class="{
            'dot--ok': d.level === 'ok',
            'dot--warn': d.level === 'warn',
            'dot--danger': d.level === 'danger',
          }"
        />
        <div class="banner__body">
          <div class="banner__title">{{ d.title }}</div>
          <div v-if="d.hint" class="banner__hint">{{ d.hint }}</div>
        </div>
      </div>

      <div v-if="backendLoading" class="banner banner--warn">
        <div class="banner__body">
          <div class="banner__title">正在检查高德 Key…</div>
        </div>
      </div>

      <div class="keycheck__actions">
        <button class="btn btn--sm" type="button" :disabled="backendLoading" @click="recheckAll">
          重新检查
        </button>
        <a
          class="btn btn--sm btn--ghost"
          href="https://console.amap.com/dev/key/app"
          target="_blank"
          rel="noreferrer"
        >
          去高德控制台
        </a>
        <button
          v-if="!backendLoading"
          class="btn btn--sm btn--ghost"
          type="button"
          @click="dismissed = true"
        >
          先不管，继续
        </button>
      </div>

      <p class="keycheck__note tiny muted">
        高德需要<strong>两个不同类型</strong>的 Key：<code>AMAP_WEB_KEY</code> 选「Web服务」，
        <code>AMAP_JS_KEY</code> + <code>AMAP_JS_SCODE</code> 选「Web端(JS API)」。
        两者不能互换 —— 填反了后端会报 <code>10009</code>，前端会报
        <code>INVALID_USER_SCODE</code>。都写在 <code>backend/.env</code>。
      </p>
    </template>
  </div>
</template>

<style scoped>
.keycheck {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}
.keycheck__actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.keycheck__note {
  margin: 0;
}
</style>
