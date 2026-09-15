<script setup lang="ts">
import { computed, ref } from 'vue'

import { ChevronDown, CircleDashed } from '@/components/icons'
import { useTripStore } from '@/stores/trip'
import { formatMin } from '@/utils/time'

/**
 * 协同台账：一行「刚刚谁改了什么」，点开是最近 24 条。
 *
 * 数据源是 `store.opLog`，也就是服务端广播过来的过去式结果，不是本地乐观写。所以这一行
 * 说的永远是「已经落库的事」，两端看到的顺序也一致（seq 单点递增）。
 *
 * 收起是默认态：新开一页时台账是空的，它就不该占位。有人动了东西它才出现，安静一阵
 * 之后（换页、重连）自然消失——协同工具里常驻的「活动流」最后都会变成壁纸。
 *
 * 展开用 grid-template-rows 的 0fr→1fr 而不是 v-if：这一段没有可测的固定高度，
 * 靠 JS 量一遍再翻只会跟 reduced-motion 打架（动画被全局掐掉时元素还得照样可见）。
 *
 * 换摘要那一下只用 :key 换节点，不套 <Transition>：后台标签页里过渡会被节流到停摆，
 * out-in 得等离场结束才插新节点，那一行就会一直读着上一条改动——台账报错比不动更糟。
 */
const store = useTripStore()

const open = ref(false)

const log = computed(() => store.opLog)
const latest = computed(() => log.value[0] ?? null)

/** origin 是 client_id，能查到人就给名字；服务端自己发起的那类（优化排程）没有发起人。 */
function actor(clientId: string) {
  if (!clientId) return { name: '系统排程', color: 'var(--text-faint)' }
  const p = store.participants.find((row) => row.client_id === clientId)
  return { name: p?.name || '同伴', color: p?.color || 'var(--accent)' }
}

function hhmm(ts: number) {
  const d = new Date(ts)
  return formatMin(d.getHours() * 60 + d.getMinutes())
}

const who = computed(() => actor(latest.value?.origin ?? ''))
</script>

<template>
  <div v-if="latest" class="ot" :class="{ 'ot--open': open }">
    <button
      class="ot__bar"
      type="button"
      :aria-expanded="open"
      :title="open ? '收起改动记录' : '展开最近改动'"
      @click="open = !open"
    >
      <CircleDashed class="ot__ic" :size="13" />
      <span :key="latest.seq" class="ot__line">
        <b class="ot__who" :style="{ color: who.color }">{{ who.name }}</b>
        <i class="ot__dot" :style="{ background: who.color }" />
        <span class="ot__text">{{ latest.text }}</span>
        <span class="ot__at tiny">{{ hhmm(latest.ts) }}</span>
      </span>
      <ChevronDown class="ot__caret" :size="13" />
    </button>

    <div class="ot__body">
      <div class="ot__clip">
        <ol class="ot__list">
          <li v-for="e in log" :key="e.seq" class="ot__row">
            <span class="ot__seq tiny">#{{ e.seq }}</span>
            <i class="ot__dot" :style="{ background: actor(e.origin).color }" />
            <span class="ot__text">{{ e.text }}</span>
            <span class="ot__by tiny">{{ actor(e.origin).name }}</span>
            <span class="ot__at tiny">{{ hhmm(e.ts) }}</span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ot {
  flex: 0 0 auto;
  border-bottom: 1px solid var(--border);
}

.ot__bar {
  display: flex;
  gap: 7px;
  align-items: center;
  width: 100%;
  padding: 6px 10px;
  font: inherit;
  text-align: left;
  color: var(--text-2);
  background: var(--surface-2);
  border: 0;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out);
}

.ot__bar:hover {
  background: var(--surface-3);
}

.ot__ic {
  flex: 0 0 auto;
  color: var(--accent);
}

.ot--open .ot__ic {
  animation: ot-spin 3.2s linear infinite;
}

@keyframes ot-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 一行装得下就只有一行：改动摘要长短不一，撑高顶栏比省略号更难读。
   新的一条从下面推进——台账在顶部，读数也就从下往上抬。
   不写 fill-mode：基态就是不透明，动画被 reduced-motion 掐掉时读数不受影响。 */
.ot__line {
  display: flex;
  flex: 1 1 auto;
  gap: 5px;
  align-items: baseline;
  min-width: 0;
  animation: ot-roll var(--dur-fast) var(--ease-out);
}

.ot__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot__who {
  flex: 0 0 auto;
  font-weight: 600;
}

.ot__dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
  transform: translateY(-1px);
}

.ot__at {
  flex: 0 0 auto;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: var(--text-3);
}

.ot__caret {
  flex: 0 0 auto;
  transition: transform var(--dur) var(--ease-out);
}

.ot--open .ot__caret {
  transform: rotate(180deg);
}

.ot__body {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur) var(--ease-out);
}

.ot--open .ot__body {
  grid-template-rows: 1fr;
}

.ot__clip {
  overflow: hidden;
  /* 0fr 只是把它剪掉，读屏照样念第二遍：收起时整段移出可访问性树。
     visibility 参与过渡，所以淡出那一下内容还在，收尾才真的藏掉。 */
  visibility: hidden;
  transition: visibility var(--dur) var(--ease-out);
}

.ot--open .ot__clip {
  visibility: visible;
}

.ot__list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 220px;
  padding: 4px 10px 8px;
  margin: 0;
  overflow-y: auto;
  list-style: none;
}

.ot__row {
  display: flex;
  gap: 6px;
  align-items: baseline;
  padding: 2px 0;
}

.ot__seq {
  flex: 0 0 auto;
  min-width: 26px;
  font-variant-numeric: tabular-nums;
  color: var(--text-faint);
}

.ot__by {
  flex: 0 0 auto;
  color: var(--text-3);
}

@keyframes ot-roll {
  from {
    opacity: 0;
    transform: translateY(7px);
  }
}
</style>
