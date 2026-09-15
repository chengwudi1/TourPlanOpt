<script setup lang="ts">
/**
 * 虚拟精灵「游游」—— 对话式操作的入口（M27）。
 *
 * 它只做三件事：露出一个会动的角色、把未落地的条数标出来、在没人理它 25 秒之后探头
 * 提醒一句「还差什么」。所有解析与落地都在 `stores/assistant.ts` 与后端，这里没有一行
 * 写数据的代码 —— 这也是它敢常驻右下角的原因。
 *
 * 表情由 `data-mood` 驱动，全部是 CSS：不引动效库（`UI-REDESIGN.md §6`），
 * 描边一律走 `--ink`，画身色走 `--pet-*`（插画配色，深色态刻意不反色）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useAssistantStore } from '@/stores/assistant'
import { useTripStore } from '@/stores/trip'

const assistant = useAssistantStore()
const store = useTripStore()

const nudged = ref(false)
const speaking = ref(false)
let idleTimer: number | undefined
let talkTimer: number | undefined

/** 游游每说一句就合扇喙部 900ms：让「它在说话」是一件看得见的事，而不是气泡在闪。 */
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

watch(
  () => assistant.open,
  (isOpen) => {
    if (isOpen) {
      nudged.value = false
      window.clearTimeout(idleTimer)
    } else armIdleTimer()
  },
)

onMounted(armIdleTimer)
onBeforeUnmount(() => {
  window.clearTimeout(idleTimer)
  window.clearTimeout(talkTimer)
})

const mood = computed(() => (speaking.value ? 'talk' : assistant.mood))
</script>

<template>
  <Teleport to="body">
    <div class="sprite" :data-mood="mood">
      <p v-if="bubbleText" class="bubble">{{ bubbleText }}</p>
      <button
        class="sprite__avatar"
        type="button"
        :class="{ 'sprite__avatar--nudge': nudged && !assistant.open }"
        :aria-expanded="assistant.open"
        aria-label="唤起助手游游"
        @pointerdown="void assistant.loadStatus()"
        @click="assistant.toggle(); armIdleTimer()"
      >
        <span v-if="assistant.pendingCount" class="badge">{{ assistant.pendingCount }}</span>
        <svg class="pet" viewBox="0 0 100 100" aria-hidden="true">
          <ellipse class="shadow" cx="50" cy="94" rx="24" ry="4.5" fill="var(--ink)" />
          <g class="bob">
            <path
              class="tuft"
              d="M48 22 C 45 11 52 4 61 7 C 55 8 52 13 53 22Z"
              fill="var(--pet-shade)"
              stroke="var(--ink)"
              stroke-width="2.4"
              stroke-linejoin="round"
            />
            <path
              d="M50 20 C 72 20 86 40 86 60 C 86 81 70 92 50 92 C 30 92 14 81 14 60 C 14 40 28 20 50 20Z"
              fill="var(--pet-body)"
              stroke="var(--ink)"
              stroke-width="3"
            />
            <path
              d="M50 20 C 68 20 80 34 83 49 C 70 40 60 38 50 38 C 40 38 30 40 17 49 C 20 34 32 20 50 20Z"
              fill="var(--surface)"
              opacity=".34"
            />
            <path
              class="wing"
              d="M22 58 C 22 50 30 47 36 50 C 33 60 27 64 22 58Z"
              fill="var(--pet-shade)"
              stroke="var(--ink)"
              stroke-width="2.4"
            />
            <path class="brow brow-l" d="M30 41 L45 44.5" stroke="var(--ink)" stroke-width="3.4" stroke-linecap="round" />
            <path class="brow brow-r" d="M70 41 L55 44.5" stroke="var(--ink)" stroke-width="3.4" stroke-linecap="round" />
            <g class="eye">
              <ellipse cx="38" cy="52" rx="6.6" ry="7" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
              <circle cx="39.4" cy="53.4" r="3.1" fill="var(--ink)" />
              <circle cx="40.6" cy="51.6" r="1.1" fill="var(--surface)" />
            </g>
            <g class="eye">
              <ellipse cx="62" cy="52" rx="6.6" ry="7" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
              <circle cx="63.4" cy="53.4" r="3.1" fill="var(--ink)" />
              <circle cx="64.6" cy="51.6" r="1.1" fill="var(--surface)" />
            </g>
            <g class="beak">
              <path
                d="M42 63 Q50 58.5 58 63 Q50 67 42 63Z"
                fill="var(--pet-beak)"
                stroke="var(--ink)"
                stroke-width="2.2"
                stroke-linejoin="round"
              />
              <path
                class="beak-lo"
                d="M44 65 Q50 70 56 65 Q50 67.5 44 65Z"
                fill="var(--pet-beak-deep)"
                stroke="var(--ink)"
                stroke-width="1.8"
                stroke-linejoin="round"
              />
            </g>
            <circle cx="26" cy="63" r="5" fill="var(--pet-blush)" opacity=".45" />
            <circle cx="74" cy="63" r="5" fill="var(--pet-blush)" opacity=".45" />
            <path d="M46 84 L54 84 L50 90Z" fill="var(--pet-shade)" stroke="var(--ink)" stroke-width="2" />
          </g>
          <g class="think">
            <circle cx="76" cy="20" r="3.2" fill="var(--accent)" />
            <circle cx="85" cy="14" r="3.2" fill="var(--accent)" />
            <circle cx="93" cy="8" r="3.2" fill="var(--accent)" />
          </g>
        </svg>
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
  overflow: visible;
}

.pet .shadow {
  opacity: 0.16;
  transform-box: fill-box;
  transform-origin: center;
  animation: pet-shadow 3.4s ease-in-out infinite;
}

.pet .bob {
  transform-box: fill-box;
  transform-origin: center bottom;
  animation: pet-bob 3.4s ease-in-out infinite;
}

.pet .eye {
  transform-box: fill-box;
  transform-origin: center;
  animation: pet-blink 5.4s infinite;
}

.pet .tuft {
  transform-box: fill-box;
  transform-origin: bottom center;
  animation: pet-sway 2.6s ease-in-out infinite;
}

.pet .wing {
  transform-box: fill-box;
  transform-origin: top center;
  animation: pet-flap 3.4s ease-in-out infinite;
}

.pet .brow {
  transform-box: fill-box;
  transition: transform var(--dur) var(--ease-pop);
}

.pet .beak-lo {
  transform-box: fill-box;
  transform-origin: top center;
}

.pet .think {
  opacity: 0;
  transition: opacity var(--dur);
}

.pet .think circle {
  transform-box: fill-box;
}

@keyframes pet-bob {
  0%,
  100% {
    transform: translateY(0) scaleY(1);
  }
  50% {
    transform: translateY(-4px) scaleY(1.03);
  }
}

@keyframes pet-shadow {
  0%,
  100% {
    transform: scaleX(1);
    opacity: 0.16;
  }
  50% {
    transform: scaleX(0.82);
    opacity: 0.1;
  }
}

@keyframes pet-blink {
  0%,
  93%,
  100% {
    transform: scaleY(1);
  }
  96% {
    transform: scaleY(0.08);
  }
}

@keyframes pet-sway {
  0%,
  100% {
    transform: rotate(-7deg);
  }
  50% {
    transform: rotate(9deg);
  }
}

@keyframes pet-flap {
  0%,
  100% {
    transform: rotate(0);
  }
  50% {
    transform: rotate(-9deg);
  }
}

@keyframes pet-orb {
  0%,
  100% {
    opacity: 0.25;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-4px);
  }
}

@keyframes pet-say {
  0%,
  100% {
    transform: scaleY(0.35);
  }
  50% {
    transform: scaleY(1.5);
  }
}

@keyframes pet-jitter {
  0%,
  100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-2.5px);
  }
  75% {
    transform: translateX(2.5px);
  }
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

[data-mood='thinking'] .think {
  opacity: 1;
}

[data-mood='thinking'] .think circle {
  animation: pet-orb 1.1s ease-in-out infinite;
}

[data-mood='thinking'] .think circle:nth-child(2) {
  animation-delay: 0.18s;
}

[data-mood='thinking'] .think circle:nth-child(3) {
  animation-delay: 0.36s;
}

[data-mood='thinking'] .brow-l {
  transform: rotate(-16deg) translateY(-3px);
}

[data-mood='thinking'] .brow-r {
  transform: rotate(12deg) translateY(-2px);
}

[data-mood='talk'] .beak-lo {
  animation: pet-say 0.28s ease-in-out infinite;
}

[data-mood='happy'] .brow-l {
  transform: rotate(9deg) translateY(1px);
}

[data-mood='happy'] .brow-r {
  transform: rotate(-9deg) translateY(1px);
}

[data-mood='happy'] .bob {
  animation-duration: 0.9s;
}

[data-mood='asking'] .brow-l {
  transform: rotate(-10deg) translateY(-2px);
}

[data-mood='warn'] .brow-l {
  transform: rotate(-24deg) translateY(-4px);
}

[data-mood='warn'] .brow-r {
  transform: rotate(24deg) translateY(-4px);
}

[data-mood='warn'] .bob {
  animation: pet-jitter 0.42s ease-in-out infinite;
}

@media (max-width: 640px) {
  .sprite {
    /* bottom 只由上面那条 ≤860 的规则负责：这里再写一遍等于把精灵按回 dock 上。 */
    right: var(--s4);
  }

  .bubble {
    max-width: 210px;
  }
}
</style>
