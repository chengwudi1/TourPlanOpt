<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  ArrowDown,
  ArrowRight,
  CircleDashed,
  MapPin,
  Mic,
  MicOff,
  RotateCcw,
  CalendarDays,
  X,
} from '@/components/icons'
import { useSpeechInput, type SpeechError } from '@/composables/useSpeechInput'
import { getClientId } from '@/composables/useClientIdentity'
import { useNarrowView } from '@/composables/useNarrowView'
import { useReduceMotion } from '@/composables/useReduceMotion'
import { useAssistantStore } from '@/stores/assistant'
import { useFeedbackStore } from '@/stores/feedback'
import { MESSAGE_TEXT_MAX, useTripStore } from '@/stores/trip'
import type { Message } from '@/types/domain'

/**
 * 同行聊天的内容件。宽屏是左栏第四页，窄屏是顶栏气泡开出的底部抽屉——两份宿主共用这一份
 * 界面，所以组件自己不碰路由、也不决定自己开不开：`active` 由宿主报进来。
 *
 * 没有内建的「谁在线」：房间即边界，进得来就读得到。
 */
const props = defineProps<{ active: boolean }>()

/** 点气泡上那行「关于：某一站」：宿主决定是切天、选卡还是连带关掉抽屉。 */
const emit = defineEmits<{ openRef: [{ placeId?: string; dayId?: string }] }>()

const store = useTripStore()
const assistant = useAssistantStore()
const feedback = useFeedbackStore()
const narrow = useNarrowView()
const reduceMotion = useReduceMotion()
const selfId = getClientId()

// -- 渲染流 ------------------------------------------------------------------------------

/** 同人连说三分钟内合成一组：一句一行地把同一个人的碎话摆成一片，比逐条署名安静。 */
const GROUP_MS = 3 * 60 * 1000
/** 空档超过二十分钟插一条时间分隔。不按日历天切——夜里两点说的那句不该被劈成两页。 */
const SEP_MS = 20 * 60 * 1000

interface Anchor {
  label: string
  live: boolean
  placeId: string
  dayId: string
}

interface Group {
  kind: 'group'
  key: string
  mine: boolean
  name: string
  color: string
  time: string
  lastTime: number
  items: (Message & { anchor: Anchor | null })[]
}

interface Sep {
  kind: 'sep'
  key: string
  label: string
}

function timeMs(iso: string): number {
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? 0 : t
}

function hhmm(d: Date): string {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

/** 分隔行只有跨出今天才带日期：同一天的对话里反复读「9月19日」是噪音。 */
function stampOf(iso: string): string {
  const t = timeMs(iso)
  if (!t) return ''
  const d = new Date(t)
  const now = new Date()
  if (sameDay(d, now)) return hhmm(d)
  if (sameDay(d, new Date(now.getTime() - 24 * 60 * 60 * 1000))) return `昨天 ${hhmm(d)}`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hhmm(d)}`
}

/**
 * 锚点只存 id，名字现读。认不出来就是那一站已经被删了：降级成一行不可点的说明，
 * 而不是「(未知地点)」——读者要知道的是这句话当初挂着谁，不是这里缺了什么。
 */
function anchorOf(message: Message): Anchor | null {
  if (message.ref_place_id) {
    const place = store.places.find((p) => p.id === message.ref_place_id)
    return {
      label: place ? place.name : '这一站已不在行程里',
      live: !!place,
      placeId: message.ref_place_id,
      dayId: '',
    }
  }
  if (message.ref_day_id) {
    const day = store.days.find((d) => d.id === message.ref_day_id)
    return {
      label: day ? `第 ${day.day_index + 1} 天${day.title ? ` · ${day.title}` : ''}` : '这一天已不在行程里',
      live: !!day,
      placeId: '',
      dayId: message.ref_day_id,
    }
  }
  return null
}

const flow = computed<(Group | Sep)[]>(() => {
  const rows = [...store.messages].sort((a, b) => a.pos - b.pos)
  const items: (Group | Sep)[] = []
  let group: Group | null = null
  let lastTime = 0
  for (const row of rows) {
    const mine = row.client_id === selfId
    const { name, color } = store.authorOf(row.client_id)
    const t = timeMs(row.created_at)
    if (!lastTime || t - lastTime > SEP_MS) {
      items.push({ kind: 'sep', key: `s-${row.id}`, label: stampOf(row.created_at) })
      group = null
    }
    if (group && group.mine === mine && t - group.lastTime <= GROUP_MS) {
      group.lastTime = t
      group.items.push({ ...row, anchor: anchorOf(row) })
    } else {
      group = {
        kind: 'group',
        key: row.id,
        mine,
        name,
        color,
        time: hhmm(new Date(t || Date.now())),
        lastTime: t,
        items: [{ ...row, anchor: anchorOf(row) }],
      }
      items.push(group)
    }
    lastTime = t
  }
  return items
})

// -- 滚动 -------------------------------------------------------------------------------

const flowEl = ref<HTMLElement | null>(null)
const pendingNew = ref(0)
/** 离底 48px 以内算「在看末尾」：行高一变化就差几像素，卡着精确值会莫名不跟。 */
function atEnd(): boolean {
  const el = flowEl.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 48
}

function scrollToEnd(smooth = true) {
  const el = flowEl.value
  if (!el) return
  el.scrollTo({
    top: el.scrollHeight,
    behavior: smooth && !reduceMotion.value ? 'smooth' : 'auto',
  })
  pendingNew.value = 0
}

function onScroll() {
  if (atEnd()) pendingNew.value = 0
}

watch(
  () => store.messages.length,
  (n, o) => {
    if (n <= o) return
    if (atEnd() || !props.active) void nextTick(() => scrollToEnd())
    else pendingNew.value += n - o
  },
)

// -- 输入 --------------------------------------------------------------------------------

const draft = ref('')
const draftEl = ref<HTMLTextAreaElement | null>(null)
const anchor = ref<{ placeId: string; dayId: string } | null>(null)

const anchorLabel = computed(() => {
  if (!anchor.value) return ''
  const place = store.places.find((p) => p.id === anchor.value?.placeId)
  if (place) return place.name
  const day = store.days.find((d) => d.id === anchor.value?.dayId)
  return day ? `第 ${day.day_index + 1} 天` : ''
})

/** 上限由 textarea 的 maxlength 守住，这里只在接近满格时把字数说出口。 */
const showCount = computed(() => draft.value.length > MESSAGE_TEXT_MAX - 60)

const canSend = computed(
  () => !!draft.value.trim() && !micBusy.value && !!store.trip,
)

function grow() {
  const el = draftEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 132)}px`
}

watch(draft, () => void nextTick(grow))

/**
 * Enter 发送、Shift+Enter 换行；窄屏例外——软键盘那颗回车键上写着「换行」，
 * 按它发送会让人在打字时把半截话发出去，所以那一侧只靠旁边的按钮。
 *
 * `isComposing` 不是可选项：中文输入法拿回车确认候选词，不认这一条会把「这家」
 * 这种还没上屏的词直接发出去。
 */
function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return
  if (narrow.value) return
  e.preventDefault()
  send()
}

function send() {
  const text = draft.value
  if (!canSend.value) return
  if (micOn.value) micStop()
  const ok = store.sendMessage({
    text,
    ref_place_id: anchor.value?.placeId ?? '',
    ref_day_id: anchor.value?.dayId ?? '',
  })
  if (!ok) return
  draft.value = ''
  anchor.value = null
  void nextTick(() => {
    grow()
    scrollToEnd(false)
  })
}

/** 被服务端挡回来的那一句原样回到输入框：话是他打的，丢一次就要重来一遍。 */
watch(
  () => store.chatBounced,
  (bounced) => {
    if (!bounced) return
    store.chatBounced = null
    if (!props.active) return
    draft.value = bounced.text
    anchor.value = bounced.ref_place_id
      ? { placeId: bounced.ref_place_id, dayId: '' }
      : bounced.ref_day_id
        ? { placeId: '', dayId: bounced.ref_day_id }
        : null
    void nextTick(() => {
      grow()
      draftEl.value?.focus()
    })
  },
)

/** 卡片菜单上那句「说一句」：把这一站挂到输入框，并把光标送到话上。 */
watch(
  () => store.chatAsk,
  (ask) => {
    if (!ask) return
    anchor.value = { placeId: ask.placeId, dayId: '' }
    if (!props.active) return
    void nextTick(() => draftEl.value?.focus())
  },
)

// -- 语音转文字（发出去的是文字，音频不存）------------------------------------------------

const {
  supported: micSupported,
  listening: micOn,
  busy: micBusy,
  mode: micMode,
  error: micError,
  errorText: micErrorText,
  errorHint: micErrorHint,
  stop: micStop,
  toggle: micToggle,
} = useSpeechInput(draft, {
  serverReady: () => !!assistant.status?.speech_ready,
  tripId: () => store.trip?.id ?? null,
  maxSeconds: () => assistant.status?.speech_max_seconds ?? 90,
})

const micLabel = computed(() => {
  if (!micSupported.value) return '语音转文字：当前浏览器不提供识别'
  if (micBusy.value) return '正在转写这段录音'
  return micOn.value
    ? micMode.value === 'server'
      ? '停止录音并转写'
      : '停止听写'
    : '用语音说这一句'
})

watch(micError, (code: SpeechError) => {
  if (!code) return
  feedback.show({
    message: micErrorText.value,
    kind: code === 'no-speech' ? 'info' : 'danger',
    hint: micErrorHint.value || '输入框仍然可以直接打字',
  })
})

// -- 开合 -------------------------------------------------------------------------------

watch(
  () => props.active,
  (on) => {
    store.setChatInView(on)
    if (on) void nextTick(() => scrollToEnd(false))
  },
  { immediate: true },
)

onMounted(() => {
  // speech_ready 决定话筒走后端转写还是浏览器识别：不打开助手面板时也得先问一次。
  void assistant.loadStatus()
  void nextTick(() => scrollToEnd(false))
})

onBeforeUnmount(() => store.setChatInView(false))

function withdraw(message: Message) {
  store.deleteMessageWithUndo(message.id)
}

function openAnchor(a: Anchor) {
  if (!a.live) return
  if (a.placeId) emit('openRef', { placeId: a.placeId })
  else if (a.dayId) emit('openRef', { dayId: a.dayId })
}
</script>

<template>
  <section class="chat">
    <div ref="flowEl" class="chat__flow" @scroll.passive="onScroll">
      <p v-if="!store.messages.length" class="chat__empty">
        还没有人说第一句。
        <span class="tiny muted"
          >在地点卡右上角的菜单里选「说一句」，这句话会挂在那一站上。</span
        >
      </p>

      <template v-for="item in flow" :key="item.key">
        <div v-if="item.kind === 'sep'" class="chat__sep" aria-hidden="true">
          <span class="tiny mono">{{ item.label }}</span>
        </div>

        <div v-else class="chat__group" :class="{ 'chat__group--mine': item.mine }">
          <div class="chat__head">
            <template v-if="item.mine">
              <span class="tiny muted chat__time">{{ item.time }}</span>
              <span class="chat__who">我</span>
            </template>
            <template v-else>
              <i class="chat__dot" :style="{ background: item.color }" aria-hidden="true" />
              <span class="chat__name">{{ item.name }}</span>
              <span class="tiny muted chat__time">{{ item.time }}</span>
            </template>
          </div>
          <div
            v-for="m in item.items"
            :key="m.id"
            class="chat__line"
            :class="{ 'chat__line--mine': item.mine }"
          >
            <div class="chat__bubble" :class="{ 'chat__bubble--mine': item.mine }">
              <button
                v-if="m.anchor"
                class="chat__ref"
                :class="{ 'chat__ref--dead': !m.anchor.live }"
                type="button"
                :disabled="!m.anchor.live"
                :title="m.anchor.live ? '在行程里找到这一站' : '这一站已从行程里删除'"
                @click="openAnchor(m.anchor)"
              >
                <CalendarDays v-if="!m.anchor.placeId" class="ic" :size="11" />
                <MapPin v-else class="ic" :size="11" />
                {{ m.anchor.label }}
              </button>
              <p class="chat__text">{{ m.text }}</p>
            </div>
            <button
              v-if="item.mine && !m.id.startsWith('tmp-')"
              class="chat__drop"
              type="button"
              title="撤回这句"
              @click="withdraw(m)"
            >
              <RotateCcw class="ic" :size="13" />
            </button>
          </div>
        </div>
      </template>
    </div>

    <button
      v-if="pendingNew"
      class="chat__jump"
      type="button"
      @click="scrollToEnd()"
    >
      <ArrowDown class="ic" :size="12" /> {{ pendingNew }} 句没看
    </button>

    <div class="chat__in">
      <button
        v-if="anchor"
        class="chat__anchor"
        type="button"
        title="取消这句话的挂靠"
        @click="anchor = null"
      >
        <MapPin class="ic" :size="11" /> 关于：{{ anchorLabel }}
        <X class="ic" :size="11" />
      </button>
      <div class="chat__row">
        <textarea
          ref="draftEl"
          v-model="draft"
          class="input chat__input"
          rows="1"
          :maxlength="MESSAGE_TEXT_MAX"
          :readonly="micOn || micBusy"
          :placeholder="
            micBusy
              ? '正在转写这段录音'
              : micOn
                ? '正在录音，说完点话筒结束'
                : '说一句，同伴的屏幕上会同时出现'
          "
          @keydown="onKeydown"
        />
        <button
          class="chat__mic"
          :class="{
            'chat__mic--on': micOn,
            'chat__mic--busy': micBusy,
            'chat__mic--off': !micSupported,
          }"
          type="button"
          :disabled="micBusy"
          :aria-busy="micBusy"
          :aria-pressed="micOn"
          :aria-label="micLabel"
          :title="micLabel"
          @click="micToggle()"
        >
          <CircleDashed v-if="micBusy" class="ic spin" :size="15" />
          <MicOff v-else-if="micOn" class="ic" :size="15" />
          <Mic v-else class="ic" :size="15" />
        </button>
        <button
          class="btn btn--sm btn--primary chat__go"
          type="button"
          :disabled="!canSend"
          title="发送"
          @click="send"
        >
          <ArrowRight class="ic" :size="14" />
        </button>
      </div>
      <p v-if="showCount" class="tiny muted chat__count mono">
        {{ draft.length }}/{{ MESSAGE_TEXT_MAX }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  /* 两处缺一不可：min-height 让内层滚动区拿得到真实高度，position 给「几句没看」浮层当参照。 */
  min-height: 0;
  position: relative;
}

.chat__flow {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: var(--s2);
  min-height: 0;
  padding: var(--s2) 2px var(--s3) 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.chat__empty {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: auto 0;
  font-size: calc(14px * var(--fs-scale));
  color: var(--text-2);
  text-align: center;
}

.chat__sep {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: var(--s1) 0;
  color: var(--text-3);
}

.chat__sep::before,
.chat__sep::after {
  flex: 1;
  height: 1px;
  content: '';
  background: var(--border-faint);
}

.chat__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.chat__group--mine {
  align-items: flex-end;
}

.chat__head {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: 0 4px;
}

.chat__group--mine .chat__head {
  flex-direction: row-reverse;
}

.chat__name {
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  color: var(--text-2);
}

.chat__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.chat__line {
  display: flex;
  gap: 4px;
  align-items: flex-end;
}

.chat__line--mine {
  flex-direction: row-reverse;
}

.chat__bubble {
  max-width: min(80%, 460px);
  padding: 6px 10px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 12px 12px 12px 3px;
}

.chat__bubble--mine {
  color: var(--accent-ink);
  background: var(--accent);
  border-color: transparent;
  border-radius: 12px 12px 3px 12px;
}

.chat__text {
  margin: 0;
  font-size: calc(14px * var(--fs-scale));
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.chat__ref {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  max-width: 100%;
  padding: 0;
  margin-bottom: 2px;
  font: inherit;
  font-size: calc(12px * var(--fs-scale));
  color: inherit;
  text-align: left;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
  opacity: 0.78;
}

.chat__bubble--mine .chat__ref {
  text-decoration: underline;
}

.chat__ref:not(.chat__ref--dead):hover {
  opacity: 1;
}

.chat__ref--dead {
  cursor: default;
  font-style: italic;
  opacity: 0.6;
}

.chat__drop {
  flex: 0 0 auto;
  padding: 3px;
  color: var(--text-3);
  opacity: 0;
}

.chat__line:hover .chat__drop,
.chat__line:focus-within .chat__drop {
  opacity: 1;
}

/* 触屏没有 hover：撤回键常驻，否则发错的那句在手机上收不回来。 */
@media (hover: none) {
  .chat__drop {
    opacity: 1;
  }
}

.chat__jump {
  position: absolute;
  right: 8px;
  bottom: 74px;
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 4px 10px;
  font-size: calc(12px * var(--fs-scale));
  color: var(--accent-strong);
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  animation: chat-jump var(--dur) var(--ease-pop);
}

@keyframes chat-jump {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
}

.chat__in {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 4px;
  padding-top: var(--s2);
  border-top: 1px solid var(--border-faint);
}

.chat__anchor {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  align-self: flex-start;
  max-width: 100%;
  padding: 2px 8px;
  font-size: calc(12px * var(--fs-scale));
  color: var(--accent-strong);
  background: var(--accent-soft);
  border: 1px solid var(--accent);
  border-radius: var(--radius-pill);
  cursor: pointer;
}

.chat__row {
  display: flex;
  gap: 6px;
  align-items: flex-end;
}

.chat__input {
  flex: 1 1 auto;
  min-width: 0;
  max-height: 132px;
  padding: 7px 10px;
  line-height: 1.45;
  resize: none;
}

.chat__count {
  margin: 0;
  text-align: right;
}

.chat__mic {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 50%;
  cursor: pointer;
  transition:
    color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.chat__mic:hover {
  color: var(--accent-strong);
  border-color: var(--accent);
}

.chat__mic--on {
  color: var(--accent-ink);
  background: var(--accent);
  border-color: var(--accent);
}

.chat__mic--busy {
  color: var(--accent-strong);
  border-color: var(--accent);
}

/* `spin` 是 AssistantPanel 的 scoped 类，这里另起一份：转圈那一档只服务「还在转写」。 */
.chat__mic .spin {
  animation: chat-spin 1.1s linear infinite;
}

@keyframes chat-spin {
  to {
    transform: rotate(360deg);
  }
}

.chat__mic--off {
  color: var(--text-3);
  cursor: not-allowed;
  opacity: 0.6;
}

/* 窄屏这一份开在底部抽屉里，落点尺寸按手指来（O6）。 */
@media (max-width: 860px) {
  .chat__bubble {
    max-width: 84%;
  }
  /* 触屏档输入字号抬到 16px 后输入条变高，74 会压住 textarea——跟着抬。 */
  .chat__jump {
    bottom: 88px;
  }
  .chat__mic {
    width: 34px;
    height: 34px;
  }
}
</style>
