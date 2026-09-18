<script setup lang="ts">
/**
 * 虚拟精灵 —— 对话式操作的入口（M27，形象可换 M29c）。
 *
 * 它只做三件事：露出一个会动的角色、把未落地的条数标出来、在没人理它 25 秒之后探头
 * 提醒一句「还差什么」。所有解析与落地都在 `stores/assistant.ts` 与后端，这里没有一行
 * 写数据的代码 —— 这也是它敢常驻右下角的原因。
 *
 * 画法与配色搬进了 `pet/PetArt.vue` + `pet/skins.ts`：这一个文件只管**位置**
 * （右下角、让位 dock、气泡、角标），不管长什么样。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import PetArt from '@/components/pet/PetArt.vue'
import { petSkin, petSkinVars } from '@/components/pet/skins'
import { useAssistantStore } from '@/stores/assistant'
import { useTripStore } from '@/stores/trip'

const assistant = useAssistantStore()
const store = useTripStore()

const nudged = ref(false)
const speaking = ref(false)
let idleTimer: number | undefined
let talkTimer: number | undefined

/** 每说一句就合嘴 900ms：让「它在说话」是一件看得见的事，而不是气泡在闪。 */
watch(
  () => assistant.lines.length,
  () => {
    if (assistant.lines[assistant.lines.length - 1]?.from !== 'pet') return
    speaking.value = true
    window.clearTimeout(talkTimer)
    talkTimer = window.setTimeout(() => {
      speaking.value = false
    }, 900)
  },
)

/** 空闲探头的内容复用首页看板那套判据：缺什么说什么，不编句子。 */
const nudgeText = computed(() => {
  if (!store.trip) return ''
  if (assistant.pendingCount) return `还有 ${assistant.pendingCount} 项待确认`
  if (!store.places.length) return '还没有安排地点，说一个地名就能开始'
  if (!store.checklist.length) return '出行清单还没有条目'
  const undone = store.checklist.filter((i) => !i.done).length
  if (undone) return `清单尚有 ${undone} 项待确认`
  return ''
})

const bubbleText = computed(() => {
  if (assistant.open) return ''
  if (assistant.pendingCount) return `还有 ${assistant.pendingCount} 项待确认`
  return nudged.value ? nudgeText.value : ''
})

function armIdleTimer(): void {
  window.clearTimeout(idleTimer)
  nudged.value = false
  idleTimer = window.setTimeout(() => {
    if (!assistant.open) nudged.value = true
  }, 25_000)
}

/** 滚动时把自己淡下去：手机上这颗常驻右下角，正好压在「第 N 天 · X 个地点」那一行的尾巴上，
 *  停手后 700ms 再回来。监听挂在 document 的捕获阶段，因为列表滚的是它自己的容器。 */
const tucked = ref(false)
let tuckTimer: number | undefined

function onScroll() {
  tucked.value = true
  window.clearTimeout(tuckTimer)
  tuckTimer = window.setTimeout(() => {
    tucked.value = false
  }, 700)
}

watch(
  () => assistant.open,
  (isOpen) => {
    if (isOpen) {
      nudged.value = false
      window.clearTimeout(idleTimer)
    } else armIdleTimer()
  },
)

onMounted(() => {
  armIdleTimer()
  document.addEventListener('scroll', onScroll, { passive: true, capture: true })
})
onBeforeUnmount(() => {
  document.removeEventListener('scroll', onScroll, true)
  window.clearTimeout(idleTimer)
  window.clearTimeout(talkTimer)
  window.clearTimeout(tuckTimer)
})

const mood = computed(() => (speaking.value ? 'talk' : assistant.mood))
</script>

<template>
  <Teleport to="body">
    <div class="sprite" :class="{ 'sprite--tucked': tucked }">
      <p v-if="bubbleText" class="bubble">{{ bubbleText }}</p>
      <button
        class="sprite__avatar"
        type="button"
        :class="{ 'sprite__avatar--nudge': nudged && !assistant.open }"
        :aria-expanded="assistant.open"
        :aria-label="`唤起助手${petSkin.name}`"
        @pointerdown="void assistant.loadStatus()"
        @click="assistant.toggle(); armIdleTimer()"
      >
        <span v-if="assistant.pendingCount" class="badge">{{ assistant.pendingCount }}</span>
        <PetArt class="pet" :skin="petSkin.id" :mood="mood" :style="petSkinVars(petSkin)" />
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.sprite {
  position: fixed;
  right: var(--s5);
  bottom: var(--s5);
  z-index: var(--z-sprite);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--s3);
  /* 容器是一个「气泡 + 头像」的外接矩形：它自己不该接点击，
     否则气泡左侧那片看不见的空档会把列表上的落点吃掉（S1）。 */
  pointer-events: none;
}

/* 手机上 dock 就在右下角（S1：avatar 与它重叠 3.7k px²，右边那颗按钮直接点不到）。
   让位高度与 dock 共用 --dock-h，安全区另加——两处必须同涨同落。 */
@media (max-width: 860px) {
  .sprite {
    bottom: calc(var(--dock-h) + env(safe-area-inset-bottom, 0px) + var(--s4));
    transition: opacity var(--dur) var(--ease-out), transform var(--dur) var(--ease-out);
  }

  /* 只在手机上收：桌面右下角压在地图上，不挡任何文字。 */
  .sprite--tucked {
    opacity: 0.3;
    transform: translateY(8px);
  }
}

.bubble {
  max-width: 290px;
  margin: 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--radius-lg) var(--radius-lg) 4px var(--radius-lg);
  background: var(--surface);
  border: 1.5px solid var(--ink);
  box-shadow: var(--shadow-md);
  font-size: var(--t-meta);
  line-height: var(--lh-body);
  color: var(--text-2);
  animation: pet-pop var(--dur-slow) var(--ease-pop);
}

.sprite__avatar {
  position: relative;
  /* 容器不接点击，这一颗接。 */
  pointer-events: auto;
  width: 88px;
  height: 88px;
  padding: 0;
  border-radius: 50%;
  background: var(--surface);
  border: 2px solid var(--ink);
  box-shadow: var(--shadow-lg);
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: transform var(--dur) var(--ease-pop), box-shadow var(--dur) var(--ease-out);
}

.sprite__avatar:hover {
  transform: translateY(-5px) scale(1.05);
}

.sprite__avatar:active {
  transform: scale(0.94);
  box-shadow: var(--shadow-sm);
}

.sprite__avatar:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}

.badge {
  position: absolute;
  top: -2px;
  right: -2px;
  min-width: 22px;
  height: 22px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  color: var(--accent-ink);
  font: 800 var(--t-micro)/20px var(--font);
  border: 2px solid var(--surface);
  animation: pet-pop var(--dur) var(--ease-pop);
}

/* 探头的时候绕一圈虚线：不动声色地在说「这里可以点」，不弹窗催人。 */
.sprite__avatar::after {
  content: '';
  position: absolute;
  inset: -9px;
  border-radius: 50%;
  border: 2px dashed var(--accent);
  opacity: 0;
  transition: opacity var(--dur);
}

.sprite__avatar--nudge::after {
  opacity: 0.55;
  animation: pet-spin 9s linear infinite;
}

.pet {
  width: 74px;
  height: 74px;
}

@keyframes pet-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes pet-pop {
  from {
    opacity: 0;
    transform: scale(0.6);
  }
}

@media (max-width: 640px) {
  .sprite {
    /* bottom 只由上面那条 ≤860 的规则负责：这里再写一遍等于把精灵按回 dock 上。 */
    right: var(--s4);
  }

  /* 88px 在手机上正好盖住一整行日期头（列表是通栏的，躲不开）。缩到 56px 后它还是一个
     清楚的入口，只是不再替列表内容占位；触控目标仍远高于 28px 的下限。 */
  .sprite__avatar {
    width: 56px;
    height: 56px;
  }

  .pet {
    width: 46px;
    height: 46px;
  }

  .bubble {
    max-width: 210px;
  }
}
</style>
