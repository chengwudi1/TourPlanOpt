<script setup lang="ts">
/**
 * 站内公告条：未登录行程的保留期（政策与判据在 backend/app/retention.py，天数在
 * settings.guest_trip_retention_days——改那个数必须回来改这里的文案，两处说的是同一件事）。
 *
 * 首屏出现、可关掉，关掉记在本机：公告不是弹窗，读过一次的人不该每次回首页都被拦一下。
 * 只存「点过」这个事实，不存时间。**这条公告的内容一改（政策、天数、措辞里的承诺），
 * KEY 里的版本号就要 +1**，否则旧设备上的键会永远压着新文案，等于没发。
 *
 * 样式全部自带，不去借全局 .card：登录页的 scoped `.card` 会连子组件根节点一起命中，
 * 借类名会被那一页的票券样式抓走（宽 430、票根虚线全跟着来）。宽度交给宿主定：
 * 首页主列自己是 flex column，登录页把它放进与票据同宽的容器里。
 */
import { ref } from 'vue'

import { Timer, X } from '@/components/icons'

const KEY = 'tourplanopt.notice.guest-retention.v1'

function wasDismissed(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    // 隐私模式下读会抛：读不出来就按「没关过」处理，公告照常显示。
    return false
  }
}

const off = ref(wasDismissed())

function dismiss() {
  off.value = true
  try {
    localStorage.setItem(KEY, '1')
  } catch {
    // 存不下就只是下次回来还会出现，不该让关闭这个动作本身出问题。
  }
}
</script>

<template>
  <aside v-if="!off" class="notice" role="note" aria-label="公告">
    <Timer class="ic notice__mark" :size="15" aria-hidden="true" />
    <p class="notice__body">
      <b class="notice__title">未登录行程的保留期</b>
      未登录时创建的行程，超过 7 天无人打开或改动会自动清理；在登录状态下打开一次，该行程即长期保留。
    </p>
    <button
      class="notice__close tap-pad"
      type="button"
      title="不再提示"
      aria-label="不再提示这条公告"
      @click="dismiss"
    >
      <X :size="14" />
    </button>
  </aside>
</template>

<style scoped>
.notice {
  display: flex;
  gap: var(--s3);
  align-items: flex-start;
  padding: 9px 8px 9px 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  box-shadow: var(--shadow-sm);
}

.notice__mark {
  margin-top: 3px;
  color: var(--accent);
}

.notice__body {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  color: var(--text-2);
  font-size: var(--t-meta);
  line-height: var(--lh-body);
}

.notice__title {
  margin-right: 6px;
  color: var(--text);
  font-weight: 600;
}

.notice__close {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: var(--radius-xs);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

.notice__close:hover {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--text);
}
</style>
