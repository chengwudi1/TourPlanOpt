<script setup lang="ts">
/**
 * 游游的对话面板（M27）。
 *
 * 这里没有解析，也没有一行写数据的代码：输入交给 `stores/assistant.ts` 去问后端，
 * 回来的每条候选指令渲染成一张确认卡，点了才走既有 store 方法 → 既有 WS op。
 *
 * 三条渲染纪律，都来自后端的接口约定：
 * 1. 卡上那句人话用服务端给的 `label`，前端不重拼一遍（两处拼必然会对不上）。
 * 2. `notes`（questions + warnings）逐条显示，一条都不藏。
 * 3. 没有「一键全部应用」。删除类天然要求手点，其它几条同样逐张确认，
 *    因为一批候选里混着只靠规则认出来的东西。
 */
import type { Component } from 'vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import type { ActionCard, AssistantAction } from '@/types/assistant'
import {
  ArrowRight,
  CalendarDays,
  Check,
  CheckCheck,
  CircleDashed,
  ListChecks,
  Lock,
  LockOpen,
  MapPin,
  Mic,
  MicOff,
  Navigation,
  Palette,
  Receipt,
  Route,
  Timer,
  Trash2,
  X,
} from '@/components/icons'
import PetArt from '@/components/pet/PetArt.vue'
import { PET_SKINS, petSkin, petSkinVars, setPetSkin } from '@/components/pet/skins'
import { useAssistantStore } from '@/stores/assistant'
import { useFeedbackStore } from '@/stores/feedback'
import { useSpeechInput, type SpeechError } from '@/composables/useSpeechInput'
import { useTripStore } from '@/stores/trip'
import { formatDuration } from '@/utils/time'
import { categoryLabel, formatMoney } from '@/utils/money'

const assistant = useAssistantStore()
const store = useTripStore()
const feedback = useFeedbackStore()

const input = ref('')
const logEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLInputElement | null>(null)

/**
 * 示例语料按当前能力给（O8）：只有一天时还演示「第2天加个…」，第一句就撞上反问，
 * 看着像没听懂；而「最后一天别排太满」这种说法本机词表接不住，只在接入云端模型时才摆出来。
 */
const SUGGESTIONS = computed(() => {
  // 示例里不能出现别的城市的地名：成都的行程演示「加个玄武湖」，第一观感就是没读懂这趟。
  // 每座城市都有博物馆，所以它既是真会去的地方，也在本机词表能接住的句式里。
  const city = store.trip?.city?.trim()
  // 「今天」在这趟旅行还没开始时根本不存在，示例就不能用它；点名当前那一天才不会撞反问。
  const dayWord =
    store.days.length >= 2 && store.currentDay
      ? `第${store.currentDay.day_index + 1}天`
      : '这天'
  const add =
    store.days.length >= 2 && store.currentDay
      ? `第${store.currentDay.day_index + 1}天加个${city ? `${city}博物馆` : '景点'}玩两小时`
      : `加个${city ? `${city}博物馆` : '景点'}玩两小时`
  const base = [add, '记得带雨伞和充电宝', '门票花了240', `优化一下${dayWord}的顺序`]
  return assistant.status?.llm_ready ? [...base, '最后一天别排太满'] : base
})

/**
 * 「规则 / 模型」「本机 / 云端」都是开发者词汇（M29b）。用户要读的是这一套现在听得懂
 * 多随意的话，所以名字按能力给：基础理解=要带明确句式，在线理解=可以说得含糊。
 *
 * 原来它是一枚带 title 的 `<span>`：状态贴图。没人能对着一枚徽章问「那我能说什么」，
 * 而 title 在触屏上根本不存在。现在它是一个按钮，点开就是这件事的答案。
 */
const engineLabel = computed(() => (assistant.status?.llm_ready ? '在线理解' : '基础理解'))

const engineLead = computed(() =>
  assistant.status?.llm_ready
    ? '已启用在线理解：说法可以含糊一些，例如「最后一天别排太满」。听不懂时会退回基础理解，行程不会被改动。'
    : '当前是基础理解：需要说清「哪天、哪个地点、多少钱」这类明确信息。理解这一步不出本机（检索地点仍会调用地图服务）。',
)

/** 面板头部两个可展开的说明位；同时只开一个，避免叠出第三种高度。 */
const about = ref<'' | 'engine' | 'skin'>('')

function toggleAbout(next: 'engine' | 'skin'): void {
  about.value = about.value === next ? '' : next
}

/** 送到云端的是什么。这一段必须照 `assistant/context.py` 说，不能含糊成「会保护隐私」。 */
const sentToLlm =
  '发送内容：这句话，加上行程的标题、城市、每天日期与地点名和时长、待办条目名。不含坐标、成员与任何密钥。'

const KIND_META: Record<AssistantAction['kind'], { icon: Component; op: string }> = {
  place_add: { icon: MapPin, op: 'place_add' },
  place_move: { icon: ArrowRight, op: 'place_move' },
  place_update: { icon: Timer, op: 'place_update' },
  place_delete: { icon: Trash2, op: 'place_delete' },
  place_lock: { icon: Lock, op: 'place_lock' },
  day_add: { icon: CalendarDays, op: 'day_add' },
  checklist_add: { icon: ListChecks, op: 'checklist_add' },
  checklist_update: { icon: CheckCheck, op: 'checklist_update' },
  checklist_delete: { icon: Trash2, op: 'checklist_delete' },
  expense_add: { icon: Receipt, op: 'expense_add' },
  trip_update: { icon: Navigation, op: 'trip_update' },
  run_optimize: { icon: Route, op: 'optimize' },
}

function meta(action: AssistantAction) {
  const base = KIND_META[action.kind]
  return action.kind === 'place_lock' && !action.locked ? { ...base, icon: LockOpen } : base
}

/** 一行补充信息。文案仍以服务端 `label` 为主，这里只补它没说的数。 */
function detail(action: AssistantAction): string {
  switch (action.kind) {
    case 'place_add':
      return `停留 ${formatDuration(action.duration_min)}${
        action.after_place_id ? ' · 排在指定地点之后' : ' · 排在末尾'
      }`
    case 'place_update':
      // 服务端 label 已经把改了什么写在标题里（「时长 2小时」「更新备注」），不再复述字段名。
      return ''
    case 'expense_add':
      return `${formatMoney(action.amount_cents)} · ${categoryLabel(action.category)}`
    case 'checklist_add':
      return action.texts.join('、')
    case 'day_add':
      return action.date ? `日期 ${action.date}` : '接在最后，日期自动往后排'
    case 'trip_update':
      return ''
    case 'place_lock':
      return action.clears_time ? '这一站重新参与优化，手填的时刻一并清掉' : ''
    default:
      return ''
  }
}

const destructive = (action: AssistantAction): boolean =>
  action.kind === 'place_delete' || action.kind === 'checklist_delete'

const stateText: Record<ActionCard['state'], string> = {
  pending: '',
  done: '已执行',
  skipped: '已忽略',
  failed: '执行失败',
}

/**
 * 语音输入（M29d / M29g）。话筒优先走后端转写（`status.speech_ready`），没配 ASR 时才退回
 * 浏览器自带的识别。浏览器那条不是这个功能的全部，只是它的兜底。
 *
 * 没有这个 API 时话筒仍然亮着、仍可点：点一下会换出一条说明。藏起来换来的不是「不打扰」，
 * 而是他再也不会回来找这个功能。
 */
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
} = useSpeechInput(input, {
  serverReady: () => !!assistant.status?.speech_ready,
  tripId: () => store.trip?.id ?? null,
  maxSeconds: () => assistant.status?.speech_max_seconds ?? 90,
})

/** 转写没回来之前不发这一句：那半截文字还在路上，发出去就是一句残话。 */
const canSend = computed(
  () => !!input.value.trim() && !assistant.busy && !micBusy.value && !!store.trip,
)

/** 署名跟着形象走：右下角叫团团、气泡上写「游」，会被读成两个功能。 */
const whoChar = computed(() => petSkin.value.name.charAt(0))

const micLabel = computed(() => {
  if (!micSupported.value) return '语音输入：当前浏览器不提供识别'
  if (micBusy.value) return '正在转写这段录音'
  return micOn.value ? (micMode.value === 'server' ? '停止录音并转写' : '停止听写') : '用语音说这一句'
})

/** 每一类错误后面那句「那现在怎么办」。network 单列：它不是权限问题，是出不了网。 */
const MIC_HINT: Record<SpeechError, string> = {
  '': '',
  denied: '在地址栏放行麦克风后可再点一次',
  'no-mic': '接上麦克风后再点一次',
  network: '挂上全局代理后重试，或直接键入',
  'no-speech': '说完再点一次，或直接键入',
  unsupported: '输入框仍然可以直接打字',
  failed: '输入框仍然可以用',
  server: '输入框仍然可以直接打字',
}

watch(micError, (code) => {
  if (!code) return
  feedback.show({
    message: micErrorText.value,
    kind: code === 'no-speech' ? 'info' : 'danger',
    // 后端那条路的 hint 是它自己算出来的（改哪一行、确认哪个端口），比这张表准。
    hint: micErrorHint.value || MIC_HINT[code],
  })
})

function submit(): void {
  const text = input.value
  if (!canSend.value) return
  // 正在听时先把话筒关掉：让识别尾巴留在框里，这一句就不带着半截话发出去。
  if (micOn.value) {
    micStop()
    return
  }
  input.value = ''
  void assistant.say(text)
}

async function scrollToEnd(): Promise<void> {
  await nextTick()
  logEl.value?.scrollTo({ top: logEl.value.scrollHeight, behavior: 'smooth' })
}

watch(() => assistant.lines.length, () => void scrollToEnd())
watch(
  () => assistant.open,
  (isOpen) => {
    if (!isOpen) {
      about.value = ''
      return
    }
    void scrollToEnd()
    window.setTimeout(() => inputEl.value?.focus(), 60)
  },
)

function onKeydown(e: KeyboardEvent): void {
  if (e.key !== 'Escape' || !assistant.open) return
  // 先收说明，再收面板：刚点开「理解方式」的人该有一下退回去的路。
  if (about.value) about.value = ''
  else assistant.toggle(false)
}

window.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <section v-show="assistant.open" class="apanel" role="dialog" aria-label="行程助手">
      <header class="apanel__hd">
        <div class="apanel__title">
          <h3>{{ petSkin.name }}</h3>
          <p class="tiny">说一句话，行程按你说的改</p>
        </div>
        <button
          class="apanel__chip tiny"
          type="button"
          :aria-expanded="about === 'engine'"
          @click="toggleAbout('engine')"
        >
          {{ engineLabel }}
        </button>
        <button
          class="apanel__chip apanel__chip--icon"
          type="button"
          aria-label="更换形象"
          :aria-expanded="about === 'skin'"
          @click="toggleAbout('skin')"
        >
          <Palette :size="14" />
        </button>
        <button class="apanel__x" type="button" aria-label="关闭" @click="assistant.toggle(false)">
          <X :size="14" />
        </button>
      </header>

      <div v-if="about === 'engine'" class="about" data-tech="1">
        <p class="about__lead">{{ engineLead }}</p>
        <dl class="about__rows">
          <div>
            <dt>理解方式</dt>
            <dd>{{ engineLabel }}</dd>
          </div>
          <template v-if="assistant.status?.llm_ready">
            <div>
              <dt>模型</dt>
              <dd class="mono">{{ assistant.status.model || '未命名' }}</dd>
            </div>
            <div>
              <dt>端点</dt>
              <dd class="mono">{{ assistant.status.endpoint || '未填' }}</dd>
            </div>
          </template>
          <div>
            <dt>单句上限</dt>
            <dd>{{ assistant.status?.max_chars ?? 600 }} 字</dd>
          </div>
          <div>
            <dt>语音转写</dt>
            <dd class="mono">
              {{ assistant.status?.speech_ready ? assistant.status.speech_endpoint : '浏览器识别' }}
            </dd>
          </div>
        </dl>
        <p v-if="assistant.status?.llm_ready" class="about__note tiny">{{ sentToLlm }}</p>
        <p v-else class="about__note tiny">
          配置 LLM_BASE_URL（本地 Ollama 免密钥）或 LLM_API_KEY 后自动转为在线理解。
        </p>
        <p v-if="!assistant.status?.speech_ready" class="about__note tiny">
          浏览器识别要把音频发到境外服务，端点改不了。配置 ASR_BASE_URL（本地 whisper.cpp
          或国内云的 OpenAI 兼容端点）后，录音改由服务端转写。
        </p>
        <p class="about__note tiny">
          两种理解都只产出待确认的操作，点「执行」才改动行程；删除类必须逐条点。
        </p>
      </div>

      <div v-else-if="about === 'skin'" class="about" data-tech="1">
        <p class="about__lead">
          换的是右下角这位以及面板上的署名。形象只记在本机，不写入行程、不同步给同伴。
        </p>
        <div class="skins">
          <button
            v-for="s in PET_SKINS"
            :key="s.id"
            class="skin"
            :class="{ 'skin--on': s.id === petSkin.id }"
            type="button"
            :aria-pressed="s.id === petSkin.id"
            @click="setPetSkin(s.id)"
          >
            <PetArt class="skin__pet" :skin="s.id" mood="happy" :style="petSkinVars(s)" />
            <span v-if="s.id === petSkin.id" class="skin__mark" aria-hidden="true">
              <Check :size="11" />
            </span>
            <strong>{{ s.name }}</strong>
            <span class="tiny muted">{{ s.note }}</span>
          </button>
        </div>
      </div>

      <div ref="logEl" class="apanel__log">
        <p v-if="!assistant.lines.length" class="msg msg--pet">
          <span class="who">{{ whoChar }}</span>
          <span class="txt">{{ assistant.greeting() }}</span>
        </p>

        <template v-for="line in assistant.lines" :key="line.id">
          <p class="msg" :class="line.from === 'me' ? 'msg--me' : 'msg--pet'">
            <span class="who">{{ line.from === 'me' ? '我' : whoChar }}</span>
            <span class="txt">{{ line.text }}</span>
          </p>

          <ul v-if="line.notes?.length" class="notes">
            <li v-for="(note, i) in line.notes" :key="i">{{ note }}</li>
          </ul>

          <div
            v-for="(card, i) in line.cards ?? []"
            :key="`${line.id}-${i}`"
            class="act"
            :class="[`act--${card.state}`, destructive(card.action) && 'act--danger']"
          >
            <div class="act__hd">
              <component :is="meta(card.action).icon" class="ic" :size="14" />
              <strong>{{ card.action.label }}</strong>
            </div>
            <p v-if="detail(card.action)" class="act__detail tiny">{{ detail(card.action) }}</p>
            <p v-if="destructive(card.action)" class="act__warn tiny">
              删除类不会自动执行，点「执行」才生效
            </p>
            <p v-if="card.error" class="act__warn tiny">{{ card.error }}</p>
            <details v-if="meta(card.action).op" class="act__tech">
              <summary class="tiny">详情</summary>
              <p class="tiny mono">指令 {{ meta(card.action).op }}</p>
            </details>
            <div class="act__ft">
              <span v-if="card.state !== 'pending'" class="tiny muted">{{ stateText[card.state] }}</span>
              <button
                v-if="card.state === 'pending' || card.state === 'failed'"
                class="btn btn--sm"
                :class="destructive(card.action) ? '' : 'btn--primary'"
                type="button"
                @click="void assistant.confirm(line.id, i)"
              >
                {{ card.state === 'failed' ? '重试' : '执行' }}
              </button>
              <button
                v-if="card.state === 'pending'"
                class="btn btn--sm btn--ghost"
                type="button"
                @click="assistant.dismiss(line.id, i)"
              >
                忽略
              </button>
            </div>
          </div>
        </template>

        <p v-if="assistant.busy" class="msg msg--pet">
          <span class="who">{{ whoChar }}</span>
          <span class="txt"><CircleDashed class="ic spin" :size="13" /> 正在解析</span>
        </p>
      </div>

      <div v-if="!assistant.lines.length && !micOn && !micBusy" class="hint">
        <button v-for="s in SUGGESTIONS" :key="s" type="button" @click="input = s">{{ s }}</button>
      </div>

      <footer class="apanel__in">
        <input
          ref="inputEl"
          v-model="input"
          type="text"
          :maxlength="600"
          :readonly="micOn || micBusy"
          :placeholder="
            micBusy
              ? '正在转写这段录音'
              : micOn
                ? micMode === 'server'
                  ? '正在录音，说完点话筒结束'
                  : '正在听写，说完点话筒结束'
                : `例如：${SUGGESTIONS[0]}`
          "
          @keydown.enter.prevent="submit"
        />
        <button
          class="apanel__mic"
          :class="{
            'apanel__mic--on': micOn,
            'apanel__mic--busy': micBusy,
            'apanel__mic--off': !micSupported,
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
          <MicOff v-else-if="micOn" :size="15" />
          <Mic v-else :size="15" />
        </button>
        <button class="btn btn--sm btn--primary" type="button" :disabled="!canSend" @click="submit">
          <ArrowRight :size="14" />
        </button>
      </footer>
    </section>
  </Teleport>
</template>

<style scoped>
.apanel {
  position: fixed;
  right: var(--s5);
  bottom: 132px;
  width: min(420px, calc(100vw - 32px));
  z-index: calc(var(--z-sprite) + 1);
  background: var(--surface);
  border: 2px solid var(--ink);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-pop);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.apanel__hd {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s3) var(--s4);
  background: var(--accent);
  color: var(--accent-ink);
}

.apanel__title {
  /* 把两枚说明按钮推到右边：靠 margin-left:auto 只推得动第一枚，第二枚会跟着漂。 */
  margin-right: auto;
}

.apanel__title h3 {
  margin: 0;
  font: 800 var(--t-h2)/1.2 var(--font-display);
  letter-spacing: var(--ls-tight);
}

.apanel__title p {
  margin: 3px 0 0;
  opacity: 0.85;
}

.apanel__chip {
  display: inline-grid;
  place-items: center;
  min-width: 28px;
  height: 28px;
  padding: 0 10px;
  border-radius: var(--radius-pill);
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  font-family: var(--font);
  opacity: 0.88;
  cursor: pointer;
  transition: opacity var(--dur-fast), background var(--dur-fast);
}

.apanel__chip--icon {
  padding: 0 7px;
}

.apanel__chip:hover {
  opacity: 1;
}

.apanel__chip[aria-expanded='true'] {
  background: var(--surface);
  border-color: var(--surface);
  color: var(--accent-strong);
  opacity: 1;
}

.apanel__x {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: inherit;
  background: transparent;
  border: 1.5px solid currentColor;
  cursor: pointer;
}

/* 说明区：点开哪一枚就答哪一枚，永远只有一块，高度不会叠两层。 */
.about {
  padding: var(--s3) var(--s4);
  background: var(--surface-2);
  border-bottom: 1.5px solid var(--ink);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  animation: apanel-slide var(--dur-entrance) var(--ease-out) backwards;
}

.about__lead {
  margin: 0;
  font-size: var(--t-meta);
  line-height: var(--lh-body);
  color: var(--text);
}

.about__rows {
  margin: 0;
  display: grid;
  gap: 3px;
}

.about__rows > div {
  display: flex;
  gap: var(--s3);
  font-size: var(--t-micro);
  line-height: 1.5;
}

.about__rows dt {
  flex: none;
  width: 68px;
  color: var(--text-2);
}

.about__rows dd {
  margin: 0;
  color: var(--text);
  word-break: break-all;
}

.about__note {
  margin: 0;
  color: var(--text-2);
  line-height: var(--lh-body);
}

.skins {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
}

.skin {
  /* 四个挤在一排，每个就小到看不出画的是什么——判断不了的选择不是选择，宁可换行。 */
  flex: 1 1 calc(50% - var(--s2));
  min-width: 132px;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: var(--s2) 4px var(--s3);
  border-radius: var(--radius);
  border: 1.5px solid var(--border);
  background: var(--surface);
  color: var(--text-2);
  font-family: var(--font);
  cursor: pointer;
  transition: border-color var(--dur-fast), transform var(--dur-fast);
}

.skin:hover {
  transform: translateY(-2px);
}

.skin:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.skin strong {
  font: 800 var(--t-meta)/1.4 var(--font);
  color: var(--text);
}

.skin--on {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1.5px var(--accent);
}

/* 「哪一张是当前形象」不能只靠一圈颜色说清：角上补一个勾，色觉之外还有一条线索。 */
.skin__mark {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--accent);
  color: var(--accent-ink);
}

.skin__pet {
  /* 和右下角那一颗（74px）几乎同尺寸：看着一张更小的图去挑一个更大的形象，挑不准。 */
  width: 68px;
  height: 68px;
}

.apanel__log {
  max-height: 46vh;
  overflow-y: auto;
  padding: var(--s3) var(--s4);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.msg {
  display: flex;
  gap: var(--s2);
  margin: 0;
  font-size: var(--t-meta);
  line-height: var(--lh-body);
  animation: apanel-slide var(--dur-entrance) var(--ease-out) backwards;
}

.msg--me {
  flex-direction: row-reverse;
}

.msg .who {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font: 800 var(--t-micro)/1 var(--font);
  background: var(--ramp-4-soft);
  color: var(--ramp-4-deep);
  border: 1.5px solid var(--ink);
}

.msg--me .who {
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.msg .txt {
  padding: 9px var(--s3);
  border-radius: var(--radius) var(--radius) var(--radius) 4px;
  background: var(--surface-2);
  border: 1px solid var(--border);
}

.msg--me .txt {
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.notes {
  margin: 0;
  padding: 8px 10px;
  list-style: none;
  border-radius: var(--radius-sm);
  background: var(--warn-soft);
  border: 1px dashed var(--warn-border);
  color: var(--text-2);
  font-size: var(--t-micro);
  line-height: var(--lh-body);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.notes li {
  padding-left: 12px;
  position: relative;
}

.notes li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--warn);
}

/* 行动确认卡：一句话被翻译成哪几条 op，说清楚了才动手。 */
.act {
  border: 1.5px solid var(--ink);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  animation: apanel-slide var(--dur-entrance) var(--ease-out) backwards;
}

.act__hd {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: 9px var(--s3);
  background: var(--surface-2);
  font-size: var(--t-micro);
}

.act__hd strong {
  font-weight: 800;
  font-size: var(--t-meta);
}

.act__tech {
  margin: 0 var(--s3) var(--s2);
}

.act__tech summary {
  width: fit-content;
  font-size: var(--t-micro);
  color: var(--text-faint);
  cursor: pointer;
}

.act__tech p {
  margin: 4px 0 0;
  color: var(--text-3);
}

.act__detail {
  margin: 0;
  padding: 0 var(--s3) var(--s2);
  color: var(--text-2);
}

.act__warn {
  margin: 0 var(--s3) var(--s2);
  padding: 7px 9px;
  border-radius: var(--radius-sm);
  background: var(--warn-soft);
  color: var(--text-2);
  border: 1px dashed var(--warn-border);
}

.act__ft {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border-top: 1px solid var(--border);
}

.act__ft .tiny {
  margin-right: auto;
}

.act--danger .act__hd {
  background: var(--danger-soft);
}

.act--danger .act__warn {
  background: var(--danger-soft);
  border-color: var(--danger-border);
}

/* 删除的执行按钮不上实心 danger：深色态 --danger 是中亮度的红，白字压不住。
   描边 + 红字在两种主题下都过线，而且「红字白底」本来就比「红色块」更像要出事的动作。 */
.act--danger .act__ft .btn {
  color: var(--danger);
  border-color: var(--danger);
}

.act--done,
.act--skipped {
  opacity: 0.66;
}

.act--done .act__ft {
  background: var(--ok-soft);
}

.act--failed {
  border-color: var(--danger);
}

.spin {
  animation: apanel-spin 1.1s linear infinite;
  transform-origin: center;
}

@keyframes apanel-spin {
  to {
    transform: rotate(360deg);
  }
}

.hint {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 var(--s4) var(--s3);
}

.hint button {
  padding: 5px 11px;
  border-radius: var(--radius-pill);
  font-size: var(--t-micro);
  color: var(--text-2);
  border: 1px dashed var(--hairline);
  background: var(--surface);
  cursor: pointer;
  transition: color var(--dur-fast), border-color var(--dur-fast), transform var(--dur-fast);
}

.hint button:hover {
  border-color: var(--accent);
  border-style: solid;
  color: var(--accent);
  transform: translateY(-1px);
}

.apanel__in {
  display: flex;
  gap: var(--s2);
  padding: var(--s3) var(--s4);
  border-top: 2px solid var(--ink);
  background: var(--surface-2);
}

.apanel__in input {
  flex: 1;
  min-width: 0;
  padding: 10px var(--s3);
  border-radius: var(--radius-pill);
  border: 1.5px solid var(--ink);
  background: var(--surface);
  font: inherit;
  font-size: var(--t-meta);
  color: var(--text);
}

.apanel__in input:focus {
  outline: none;
  box-shadow: 0 0 0 3px var(--accent-soft);
}

/* 听写期间这根输入框由识别结果写，不由人写——底色跟着换，才看得出不是自己打进去的。 */
.apanel__in input[readonly] {
  background: var(--surface-selected);
  border-style: dashed;
}

.apanel__mic {
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  border: 1.5px solid var(--ink);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
  transition: color var(--dur-fast), border-color var(--dur-fast);
}

.apanel__mic:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.apanel__mic:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.apanel__mic--on {
  background: var(--accent);
  border-color: var(--accent-strong);
  color: var(--accent-ink);
  animation: apanel-listen 1.5s ease-in-out infinite;
}

/* 转写中：录音已经交出去了，等的只是文字。禁用态不能长成「坏了」的样子。 */
.apanel__mic--busy {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent-strong);
  cursor: progress;
}

.apanel__mic--off {
  border-style: dashed;
  opacity: 0.5;
}

@keyframes apanel-listen {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--accent-soft);
  }
  50% {
    box-shadow: 0 0 0 6px transparent;
  }
}

@keyframes apanel-slide {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}

@media (max-width: 640px) {
  .apanel {
    right: var(--s3);
    left: var(--s3);
    bottom: 122px;
    width: auto;
  }
}
</style>
