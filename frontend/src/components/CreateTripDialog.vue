<script setup lang="ts">
import { computed, ref } from 'vue'

import AppModal from '@/components/AppModal.vue'
import SegmentedControl, { type SegOption } from '@/components/SegmentedControl.vue'
import { ArrowRight, Check, ChevronDown, Link2, LoaderCircle, MapPin } from '@/components/icons'
import type { TravelMode, TripCreateResult } from '@/types/domain'
import { ApiError, apiFetch, postJson } from '@/utils/api'
import { parseHHMM } from '@/utils/time'

/**
 * 创建行程＝一张只有标题必填的表单，成功页是它的第二个阶段。
 *
 * 抄 TREK 的是判据不是代码：能把一段行程建出来的信息只有名字，其余全是「先建后补」——
 * 城市空着推荐会按地点自己判断，日期空着就不排日期。所以这些字段一律不挡创建按钮，
 * 连标题留空都放过去（前端读作「未命名行程」）。一行小灰字把默认值说清楚，
 * 用户就不会对着四个空输入框猜「不填会怎样」。
 *
 * 建完停在成功页而不是直接跳走：分享链接是这个产品的差异化能力，它必须先被看见一次。
 * 跳转与「最近打开」记账仍归父组件，这里只 emit。
 */
const emit = defineEmits<{
  /** 服务端已经建出行程——父组件据此记账，即使用户还没进去。 */
  created: [tripId: string],
  done: [tripId: string],
  cancel: []
}>()

const MODES: readonly SegOption[] = [
  { value: 'driving', label: '驾车 / 打车', hint: '按真实路况计算' },
  { value: 'walking', label: '步行', hint: '适合城市漫步' },
  { value: 'straight', label: '直线', hint: '不消耗配额，即时返回' },
]

const MODE_TEXT: Record<string, string> = { driving: '驾车', walking: '步行', straight: '直线' }

const title = ref('')
const city = ref('')
const travelMode = ref<TravelMode>('driving')
const startDate = ref('')
const days = ref(1)
const dayStart = ref('')

const busy = ref(false)
const error = ref('')
const created = ref<TripCreateResult | null>(null)
const copied = ref(false)
const showOpen = ref(false)
const rawId = ref('')

/** 小灰字只在用户真的填可选项时改口，否则它就是那句最该被看到的默认值说明。 */
const hasOptions = computed(
  () => Boolean(city.value.trim() || startDate.value || dayStart.value) || days.value > 1,
)

/** 分享链接和裸 ID 都收：用户从微信里复制出来的往往是整条 URL。 */
const pastedId = computed(() => {
  const s = rawId.value.trim()
  const m = s.match(/\/trip\/([A-Za-z0-9]+)/)
  return (m ? m[1] : s).toUpperCase()
})

/** 年份只在该说的时候说：当年写成「10月1日」，跨年才补年份。 */
function fmtDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  const md = `${m}月${d}日`
  return y === new Date().getFullYear() ? md : `${y}年${md}`
}

function addDays(iso: string, n: number): string {
  const first = new Date(`${iso}T00:00:00`)
  if (Number.isNaN(first.getTime())) return ''
  first.setDate(first.getDate() + n)
  const mm = String(first.getMonth() + 1).padStart(2, '0')
  const dd = String(first.getDate()).padStart(2, '0')
  return `${first.getFullYear()}-${mm}-${dd}`
}

/** 成功页那句「建出了什么」——天数用服务端的 day_count，`days` 会被夹取。 */
const builtLine = computed(() => {
  const trip = created.value
  if (!trip) return ''
  const parts = []
  if (startDate.value) {
    const last = addDays(startDate.value, trip.day_count - 1)
    parts.push(last && last !== startDate.value
      ? `${fmtDay(startDate.value)} → ${fmtDay(last)}`
      : fmtDay(startDate.value))
  }
  parts.push(`${trip.day_count} 天`)
  if (city.value.trim()) parts.push(city.value.trim())
  parts.push(MODE_TEXT[travelMode.value] ?? travelMode.value)
  if (dayStart.value) parts.push(`每天 ${dayStart.value} 出发`)
  return parts.join(' · ')
})

async function submitCreate() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const data = await apiFetch<TripCreateResult>('/api/trips', postJson({
      title: title.value.trim(),
      city: city.value.trim(),
      travel_mode: travelMode.value,
      days: days.value,
      start_date: startDate.value || null,
      day_start_min: parseHHMM(dayStart.value),
    }))
    created.value = data
    emit('created', data.trip_id)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function copyLink() {
  if (!created.value) return
  const url = created.value.share_url || `${window.location.origin}/trip/${created.value.trip_id}`
  try {
    await navigator.clipboard.writeText(url)
    copied.value = true
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    window.prompt('请复制以下分享链接：', url)
  }
}

function enter() {
  if (created.value) emit('done', created.value.trip_id)
}

function openPasted() {
  if (pastedId.value) emit('done', pastedId.value)
}
</script>

<template>
  <AppModal
    :title="created ? '行程已创建' : '开始一段旅行'"
    :sub="created
      ? '将链接分享给同行者，对方打开即可共同编辑'
      : '仅需填写名称，其余信息可稍后补充'"
    variant="sheet"
    @close="emit('cancel')"
  >
    <!-- ---------- 成功态：分享卡 ---------- -->
    <div v-if="created" class="done">
      <h3 class="done__title">{{ title.trim() || '未命名行程' }}</h3>
      <p class="tiny muted done__meta">{{ builtLine }}</p>

      <div class="done__link">
        <span class="done__url">{{ created.share_url }}</span>
        <button class="iconbtn" type="button" title="复制链接" @click="copyLink">
          <Link2 class="ic" :size="15" />
        </button>
      </div>
      <p class="tiny muted done__hint">同一条链接多人同时编辑，改动实时同步。</p>
    </div>

    <!-- ---------- 表单态 ---------- -->
    <div v-else class="sheet">
      <form class="form" @submit.prevent="submitCreate">
        <div class="tf">
          <label class="tf__label" for="new-title">行程名称</label>
          <input
            id="new-title"
            v-model="title"
            class="tf__input"
            placeholder="五一 · 苏州三日游"
            autocomplete="off"
            maxlength="40"
          />
          <p class="tiny muted">可留空，创建后可重命名。</p>
        </div>

        <div class="opt">
          <div class="opt__head">
            <span class="tf__label">补充信息</span>
            <span class="tiny muted">全部可留空</span>
          </div>

          <div class="opt__row">
            <MapPin class="ic opt__icon" :size="14" />
            <label class="opt__label" for="new-city">城市</label>
            <input
              id="new-city"
              v-model="city"
              class="input opt__input"
              placeholder="推荐与搜索按该城市展开"
              autocomplete="off"
            />
          </div>

          <div class="opt__grid">
            <div class="opt__cell">
              <label class="opt__label" for="new-start">出发日期</label>
              <input id="new-start" v-model="startDate" class="input" type="date" />
            </div>
            <div class="opt__cell">
              <label class="opt__label" for="new-time">每天出发</label>
              <input id="new-time" v-model="dayStart" class="input" type="time" />
            </div>
            <div class="opt__cell">
              <span class="opt__label">行程天数</span>
              <div class="stepper">
                <button
                  class="stepper__btn"
                  type="button"
                  title="减少一天"
                  :disabled="days <= 1"
                  @click="days = Math.max(1, days - 1)"
                >
                  −
                </button>
                <output class="stepper__value" :aria-label="`共 ${days} 天`">{{ days }}</output>
                <button
                  class="stepper__btn"
                  type="button"
                  title="增加一天"
                  :disabled="days >= 30"
                  @click="days = Math.min(30, days + 1)"
                >
                  ＋
                </button>
              </div>
            </div>
          </div>

          <div class="opt__cell">
            <span class="opt__label">默认交通方式</span>
            <SegmentedControl v-model="travelMode" :options="MODES" label="默认交通方式" />
          </div>

          <p class="tiny muted opt__note">
            {{ hasOptions
              ? '建好之后这些都能在行程页改。'
              : '都留空也行：1 天、09:00 出发、按驾车算路线，日期不填就不给每天排日期。' }}
          </p>
        </div>

        <p v-if="error" class="tiny form__err">{{ error }}</p>

        <!-- 看不见的默认提交按钮：CTA 在 footer 里交不了单，而多输入框表单必须有 submit
             才会在回车时提交。不进 tab 序列，也不占屏幕。 -->
        <button class="sr-submit" type="submit" tabindex="-1" aria-hidden="true">创建行程</button>
      </form>

      <!-- 打开已有：从对等标签降级成一条底部细链接。放在 form 外面是必须的——
           嵌套 <form> 会被 HTML 解析器丢掉内层标签，而且粘贴框里按回车会顺手建出一段行程。 -->
      <div class="opener">
        <button class="opener__btn" type="button" :aria-expanded="showOpen" @click="showOpen = !showOpen">
          已有分享链接？打开现有行程
          <ChevronDown class="ic" :class="{ 'opener__btn--up': showOpen }" :size="14" />
        </button>
        <div v-if="showOpen" class="paste">
          <input
            v-model="rawId"
            class="input"
            placeholder="粘贴完整的分享链接"
            autocomplete="off"
            spellcheck="false"
            @keyup.enter="openPasted"
          />
          <button class="btn btn--primary paste__go" type="button" :disabled="!pastedId" @click="openPasted">
            打开 <ArrowRight class="ic" :size="14" />
          </button>
        </div>
      </div>
    </div>

    <template #footer>
      <template v-if="created">
        <button class="btn done__copy" type="button" @click="copyLink">
          <Check v-if="copied" class="ic" :size="14" />
          <Link2 v-else class="ic" :size="14" />
          {{ copied ? '链接已复制' : '复制链接' }}
        </button>
        <button class="btn btn--primary done__enter" type="button" @click="enter">进入行程</button>
      </template>
      <!-- 主 CTA 在 modal 的 footer 里，是这个 <form> 的兄弟节点，type=submit 交不出去，
           所以直接调函数；表单自己的 @submit 仍然负责输入框里按回车。 -->
      <button v-else class="btn btn--primary form__submit" type="button" :disabled="busy" @click="submitCreate">
        <LoaderCircle v-if="busy" class="ic form__spin" :size="14" />
        {{ busy ? '创建中…' : '创建行程' }}
      </button>
    </template>
  </AppModal>
</template>

<style scoped>
/* ---------- 表单 ---------- */
.sheet {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0 16px 20px;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tf {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 16px;
}

.tf__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}

/* 标题是这一屏唯一的主角：字号最大、去掉盒子边框，只留一条基线。
   四个长得一样的输入框会让「必填的那个」消失。 */
.tf__input {
  padding: 2px 0 9px;
  font-family: inherit;
  font-size: 19px;
  font-weight: 600;
  color: var(--text);
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--border-strong);
  border-radius: 0;
  transition: border-color var(--dur-fast) var(--ease-out);
}
.tf__input::placeholder {
  font-weight: 500;
  color: var(--text-3);
}
.tf__input:focus {
  border-bottom-color: var(--accent);
}

.opt {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  background: var(--surface-2);
  border: 1px solid var(--border-faint);
  border-radius: var(--radius);
}

.opt__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.opt__row {
  display: grid;
  grid-template-columns: auto auto 1fr;
  gap: 8px;
  align-items: center;
}

.opt__icon {
  color: var(--text-3);
}

.opt__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  white-space: nowrap;
}

.opt__input {
  min-width: 0;
}

.opt__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(116px, 1fr));
  gap: 10px;
}

.opt__cell {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.opt__grid .input,
.stepper {
  width: 100%;
}

.opt__note {
  margin: 0;
}

.stepper {
  display: grid;
  grid-template-columns: 30px 1fr 30px;
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
}

.stepper__btn {
  display: grid;
  place-items: center;
  height: 30px;
  font-size: 15px;
  color: var(--text-2);
  background: transparent;
  border: 0;
  border-radius: calc(var(--radius-sm) - 2px);
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur) var(--ease-pop);
}
.stepper__btn:hover:not(:disabled) {
  color: var(--accent-strong);
  background: var(--surface-2);
}
.stepper__btn:active:not(:disabled) {
  transform: scale(0.9);
}
.stepper__btn:disabled {
  color: var(--text-faint);
  cursor: not-allowed;
}

.stepper__value {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

/* 保留在渲染树里而不是 display:none——后者会让回车找不到提交按钮。 */
.sr-submit {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

.form__err {
  margin: 0;
  color: var(--danger);
}

.form__submit {
  flex: 1;
  justify-content: center;
}

.form__spin {
  animation: spin 900ms linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* ---------- 打开已有：降级成一条底部细链接 ---------- */
.opener {
  display: flex;
  flex-direction: column;
  gap: 8px;
  /* 发丝线要贴到抽屉两边，用出血的负 margin 抵消 .sheet 的左右 padding。 */
  margin-inline: -16px;
  padding: 12px 16px 4px;
  border-top: 1px solid var(--border-faint);
}

.opener__btn {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  align-self: flex-start;
  padding: 0;
  font-size: 12px;
  color: var(--text-2);
  background: transparent;
  border: 0;
  transition: color var(--dur-fast) var(--ease-out);
}
.opener__btn:hover {
  color: var(--accent-strong);
}
.opener__btn .ic {
  transition: transform var(--dur) var(--ease-out);
}
.opener__btn--up {
  transform: rotate(180deg);
}

.paste {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  /* 展开那一下从 4px 推出来：这是 accordion 不是翻页。 */
  --rise: 4px;
  animation: rise-in var(--dur-slow) var(--ease-out) backwards;
}

.paste__go {
  justify-content: center;
}

/* ---------- 成功态 ---------- */
.done {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 22px 16px 18px;
  /* 顶光：抽屉往上抬时像被光照到，不是凭空贴上来的一块平板。 */
  background: radial-gradient(120% 80% at 50% -10%, var(--surface-selected), transparent 62%);
}

.done__title {
  font-size: 21px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--text);
  word-break: break-word;
}

.done__meta {
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.done__link {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px;
  align-items: center;
  margin-top: 6px;
  padding: 4px 4px 4px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.done__url {
  overflow: hidden;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.done__hint {
  margin: 0;
}

.done__copy,
.done__enter {
  flex: 1;
  justify-content: center;
}
</style>
