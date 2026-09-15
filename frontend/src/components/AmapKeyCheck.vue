<script setup lang="ts">
/**
 * 高德 Key 的诊断面板（M1 建的，M26d 收敛）。
 *
 * 状态不在这里，在 `composables/useAmapHealth.ts`：健康时这个组件一个字都不渲染，
 * 健康度由顶栏那颗小点代劳，面板只在「有问题且没被忽略」或用户主动展开时出现。
 *
 * 两半都要报：后端只能验 Web服务 Key（那个 Key 从不出现在前端），浏览器只能验 JS Key
 * （域名白名单在浏览器侧才生效）。
 */
import { onMounted } from 'vue'

import { useAmapHealth } from '@/composables/useAmapHealth'

const {
  health,
  showPanel,
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
} = useAmapHealth()

onMounted(() => {
  // 一个会话只探一次：去重在那个 composable 里，这里每次挂载都会调用。
  void probe()
})

async function recheck() {
  expanded.value = true
  await probe(true)
}
</script>

<template>
  <div v-if="showPanel" class="keycheck" :class="{ 'keycheck--quiet': health === 'good' }">
    <div v-if="health === 'good'" class="banner banner--ok">
      <span class="dot dot--ok" />
      <div class="banner__body">
        <div class="banner__title">高德 Key 自检通过</div>
        <div class="banner__hint tiny">
          Web服务 Key 探活 {{ webKey?.latency_ms }}ms · JS API {{ status }}
        </div>
      </div>
      <button class="btn btn--sm btn--ghost" type="button" @click="toggle">收起</button>
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

      <div v-if="loading" class="banner banner--warn">
        <div class="banner__body">
          <div class="banner__title">正在检查高德 Key…</div>
        </div>
      </div>

      <div class="keycheck__actions">
        <button class="btn btn--sm" type="button" :disabled="loading" @click="recheck">
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
          v-if="!loading"
          class="btn btn--sm btn--ghost"
          type="button"
          @click="dismiss"
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

/* 健康时的展开只是「看一眼数」，不该抢走问题现场的视觉权重。 */
.keycheck--quiet {
  padding: 6px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border-faint);
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
