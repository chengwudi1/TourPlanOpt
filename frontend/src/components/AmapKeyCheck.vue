<script setup lang="ts">
/**
 * 地图服务的诊断面板（M1 建的，M26d 收敛，评审 M30 把口径改成「先人话、细节进详情」）。
 *
 * 状态不在这里，在 `composables/useAmapHealth.ts`：健康时这个组件一个字都不渲染，
 * 健康度由顶栏那颗小点代劳，面板只在「有问题且没被忽略」或用户主动展开时出现。
 *
 * 两半都要报：后端只能验服务侧 Key（那个 Key 从不出现在前端），浏览器只能验页面侧 Key
 * （域名白名单在浏览器侧才生效）。界面上按「本机服务 / 网页里」称呼这两半。
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
        <div class="banner__title">地图服务正常</div>
        <details class="banner__tech">
          <summary class="tiny">详情</summary>
          <p class="banner__techline tiny mono">
            后端耗时 {{ webKey?.latency_ms }}ms · 页面侧状态 {{ status }}
          </p>
        </details>
      </div>
      <button class="btn btn--sm btn--ghost" type="button" @click="toggle">收起</button>
    </div>

    <template v-else>
      <div v-if="backendError" class="banner banner--danger">
        <span class="dot dot--danger" />
        <div class="banner__body">
          <div class="banner__title">问不到地图服务的状态</div>
          <div class="banner__hint tiny">本机服务没有应答。等它启动完成后再点一次「重新检查」。</div>
          <details class="banner__tech">
            <summary class="tiny">详情</summary>
            <p class="banner__techline tiny mono">{{ backendError }}</p>
          </details>
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
            {{ webKey.present ? '本机服务的地图访问用不了' : '本机服务还没配上地图访问' }}
          </div>
          <div class="banner__hint tiny">
            {{ webKey.present ? '按详情里的说明核对一次，再点「重新检查」。' : '填好后点「重新检查」。' }}
          </div>
          <details class="banner__tech">
            <summary class="tiny">详情</summary>
            <p class="banner__techline tiny">{{ webKey.hint }}</p>
            <p class="banner__techline tiny mono">{{ webKey.name }}：{{ webKey.detail }}</p>
            <p v-if="webKey.infocode" class="banner__techline tiny mono">
              返回码 {{ webKey.infocode }}
            </p>
          </details>
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
            {{ jsKey.present ? '网页里的地图访问用不了' : '网页还没配上地图访问' }}
          </div>
          <div class="banner__hint tiny">
            {{ jsKey.present ? '按详情里的说明核对一次，再点「重新检查」。' : '填好后点「重新检查」。' }}
          </div>
          <details class="banner__tech">
            <summary class="tiny">详情</summary>
            <p class="banner__techline tiny">{{ jsKey.hint }}</p>
            <p class="banner__techline tiny mono">{{ jsKey.name }}：{{ jsKey.detail }}</p>
          </details>
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
          <details v-if="d.hint" class="banner__tech">
            <summary class="tiny">详情</summary>
            <p class="banner__techline tiny">{{ d.hint }}</p>
          </details>
        </div>
      </div>

      <div v-if="loading" class="banner banner--warn">
        <div class="banner__body">
          <div class="banner__title">正在检查地图服务…</div>
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

      <details class="keycheck__more">
        <summary class="tiny">地图访问是怎么配的</summary>
        <p class="keycheck__note tiny muted">
          地图服务需要<strong>两个不同类型</strong>的 Key：<code>AMAP_WEB_KEY</code> 选「Web服务」，
          <code>AMAP_JS_KEY</code> + <code>AMAP_JS_SCODE</code> 选「Web端(JS API)」。
          两者不能互换 —— 填反了后端会报 <code>10009</code>，前端会报
          <code>INVALID_USER_SCODE</code>。都写在 <code>backend/.env</code>。
        </p>
      </details>
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

/* 细节折叠：收起时只是横幅底下一行安静灰字，不许抢横幅标题的注意力。 */
.banner__tech {
  margin-top: 3px;
}

.keycheck__more {
  margin-top: 2px;
}

.banner__tech summary,
.keycheck__more summary {
  width: fit-content;
  color: var(--text-faint);
  cursor: pointer;
  list-style: none;
}

.banner__tech summary::-webkit-details-marker,
.keycheck__more summary::-webkit-details-marker {
  display: none;
}

.banner__techline {
  margin: 2px 0 0;
  overflow-wrap: anywhere;
  color: var(--text-3);
}
</style>
