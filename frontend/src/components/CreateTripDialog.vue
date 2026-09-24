<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import AppModal from '@/components/AppModal.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { ArrowRight, Check, ChevronDown, ImagePlus, Link2, LoaderCircle, MapPin } from '@/components/icons'
import { useCopy } from '@/composables/useCopy'
import type { TravelMode, Trip, TripCreateResult } from '@/types/domain'
import { ApiError, apiFetch, postJson } from '@/utils/api'
import { shrinkToCover } from '@/utils/coverImage'
import { TRAVEL_MODE_OPTIONS, TRAVEL_MODE_TEXT } from '@/utils/tripmodes'
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

const title = ref('')
const city = ref('')
const travelMode = ref<TravelMode>('driving')
const startDate = ref('')
const days = ref(1)

/** 服务端 schema 的默认值（`day_start_min = 540`）。空着的 `<input type="time">` 会渲染成
 *  一串占位破折号，看着像乱码，而它想表达的本来就是「用默认值」——所以把默认值显式写出来。 */
const DEFAULT_DAY_START = '09:00'
const dayStart = ref(DEFAULT_DAY_START)

/** 时间框被手动清空（按 Delete）后同样只剩破折号，失焦时补回默认值。留空与 09:00 建出的行程本来就一模一样。 */
function normalizeDayStart() {
  if (!dayStart.value) dayStart.value = DEFAULT_DAY_START
}

const busy = ref(false)
const error = ref('')
const created = ref<TripCreateResult | null>(null)
const copied = ref(false)
const copy = useCopy()
const showOpen = ref(false)
const rawId = ref('')

/** 小灰字只在用户真的填可选项时改口，否则它就是那句最该被看到的默认值说明。
 *  时间框预填了默认值，所以判据是「偏离默认」而不是「非空」。 */
const hasOptions = computed(
  () =>
    Boolean(city.value.trim() || startDate.value)
    || days.value > 1
    || dayStart.value !== DEFAULT_DAY_START
    || coverBlob.value !== null,
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
  parts.push(TRAVEL_MODE_TEXT[travelMode.value])
  if (dayStart.value) parts.push(`每天 ${dayStart.value} 开始`)
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
    // 补传不挡成功页：分享链接才是这一屏的主角，海报慢一拍或干脆失败都不该拖住它。
    void uploadCreatedCover(data.trip_id)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function copyLink() {
  if (!created.value) return
  const url = created.value.share_url || `${window.location.origin}/trip/${created.value.trip_id}`
  const ok = await copy(url, {
    receipt: '分享链接已复制，同行者打开即可共同编辑',
    fallbackTitle: '分享链接',
  })
  if (!ok) return
  copied.value = true
  setTimeout(() => (copied.value = false), 1600)
}

function enter() {
  if (created.value) emit('done', created.value.trip_id)
}

function openPasted() {
  if (pastedId.value) emit('done', pastedId.value)
}

/* ---------- 海报位（决策 9：先缩先预览，建完立刻补传） ---------- */

const coverInput = ref<HTMLInputElement | null>(null)
const coverBlob = ref<Blob | null>(null)
const coverPreview = ref('')
/** 补传的结果只在成功页那一段说话：表单页那一刻已经换掉了。 */
const coverNotice = ref('')

function setCover(blob: Blob) {
  if (coverPreview.value) URL.revokeObjectURL(coverPreview.value)
  coverBlob.value = blob
  coverPreview.value = URL.createObjectURL(blob)
  coverNotice.value = ''
}

function clearCover() {
  if (coverPreview.value) URL.revokeObjectURL(coverPreview.value)
  coverPreview.value = ''
  coverBlob.value = null
  coverNotice.value = ''
}

function onCoverPickFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 连选同一张文件时浏览器不会报 change，所以取完就把 value 清掉。
  input.value = ''
  if (!file) return
  void shrinkCover(file)
}

async function shrinkCover(file: File) {
  try {
    setCover(await shrinkToCover(file))
  } catch {
    // 预缩这一步整个坏掉也不该吞掉这张图：原样留着，后端的 4 MB 硬闸会给出人话。
    setCover(file)
  }
}

/** 补传失败不改成功页的主线：行程已经建出来了，海报随时能在行程页重来一次。 */
async function uploadCreatedCover(tripId: string) {
  const blob = coverBlob.value
  if (!blob) return
  const body = new FormData()
  body.append('file', blob)
  try {
    await apiFetch<Trip>(`/api/trips/${tripId}/cover`, { method: 'POST', body })
    coverNotice.value = '封面已附上。'
  } catch (err) {
    coverNotice.value =
      err instanceof ApiError
        ? `封面没有传上去：${err.message}。可在行程页「更多」里的「封面」那一行重来。`
        : '封面没有传上去，可在行程页「更多」里的「封面」那一行重来。'
  }
}

onBeforeUnmount(() => {
  if (coverPreview.value) URL.revokeObjectURL(coverPreview.value)
})
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
      <p v-if="coverNotice" class="tiny muted done__cover" role="status">{{ coverNotice }}</p>
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
              <label class="opt__label" for="new-time">每日开始</label>
              <input
                id="new-time"
                v-model="dayStart"
                class="input"
                type="time"
                aria-describedby="new-time-hint"
                @blur="normalizeDayStart"
              />
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

          <p id="new-time-hint" class="tiny muted opt__hint">
            出发日期决定这些天排在几号；每日开始指第一站开排的时间点，后面各站按路程与停留顺延。
          </p>

          <div class="opt__cell">
            <span class="opt__label">默认交通方式</span>
            <SegmentedControl v-model="travelMode" :options="TRAVEL_MODE_OPTIONS" label="默认交通方式" />
          </div>

          <!-- 只给本机上传这一档：创建那一刻行程里还没有地点图片，城市图片也不再是自动档，
               摆两档空的在这里只会让人以为「选了没反应」。 -->
          <div class="poster">
            <span class="opt__label">行程封面</span>
            <div class="poster__row">
              <button
                class="poster__slot"
                :class="{ 'poster__slot--filled': coverPreview }"
                type="button"
                :aria-label="coverPreview ? '更换封面图片' : '添加封面图片'"
                @click="coverInput?.click()"
              >
                <img v-if="coverPreview" :src="coverPreview" alt="" />
                <span v-else class="poster__empty">
                  <ImagePlus class="ic" :size="15" /> 添加封面
                </span>
              </button>
              <div class="poster__side">
                <p class="tiny muted">
                  不添加则先用内置的默认封面，行程页里随时可以换。支持 JPEG、PNG 与 WebP，单张不超过 4 MB。
                </p>
                <button v-if="coverBlob" class="btn btn--sm" type="button" @click="clearCover">
                  移除封面
                </button>
              </div>
            </div>
            <input
              ref="coverInput"
              class="poster__file"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              @change="onCoverPickFile"
            />
          </div>

          <p class="tiny muted opt__note">
            {{ hasOptions
              ? '行程名称、城市与每天的安排都能在行程页改；交通方式与每日开始在「设置」里改，也能跟助手说一声。'
              : '都留空也行：1 天、每天 09:00 开始、按驾车算路线，日期不填就不给每天排日期。' }}
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
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
  color: var(--text-2);
}

/* 标题是这一屏唯一的主角：字号最大、去掉盒子边框，只留一条基线。
   四个长得一样的输入框会让「必填的那个」消失。 */
.tf__input {
  padding: 2px 0 9px;
  font-family: inherit;
  font-size: calc(19px * var(--fs-scale));
  font-weight: 600;
  color: var(--text);
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--hairline);
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
  font-size: calc(12px * var(--fs-scale));
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

.opt__hint {
  /* 这句是解释上面那一行三格的，得贴回字段下面：负 margin 抵掉 .opt 的 gap。 */
  margin: -7px 0 0;
}

.opt__note {
  margin: 0;
}

.stepper {
  display: grid;
  grid-template-columns: 30px 1fr 30px;
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--ink);
  border-radius: var(--radius-sm);
}

.stepper__btn {
  display: grid;
  place-items: center;
  height: 30px;
  font-size: calc(15px * var(--fs-scale));
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
  font-size: calc(14px * var(--fs-scale));
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

/* ---------- 海报位 ---------- */
.poster {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.poster__row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.poster__slot {
  position: relative;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 132px;
  aspect-ratio: 16 / 10;
  overflow: hidden;
  color: var(--text-2);
  cursor: pointer;
  background: var(--surface);
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  transition:
    border-color var(--dur-fast) var(--ease-out),
    transform var(--dur) var(--ease-pop);
}

.poster__slot:hover {
  border-color: var(--accent);
  transform: translateY(-1px) scale(1.015);
}

.poster__slot--filled {
  border-style: solid;
}

.poster__slot img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.poster__empty {
  display: flex;
  gap: 5px;
  align-items: center;
  font-size: calc(12px * var(--fs-scale));
  font-weight: 600;
}

.poster__side {
  display: flex;
  flex-direction: column;
  gap: 7px;
  align-items: flex-start;
  min-width: 0;
}

.poster__side p {
  margin: 0;
  line-height: 1.55;
}

/* 保留在渲染树里：`display: none` 的 file input 收不到程序塞进去的 files。 */
.poster__file {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
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
  font-size: calc(12px * var(--fs-scale));
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
  font-size: calc(21px * var(--fs-scale));
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
  font-size: calc(12px * var(--fs-scale));
  color: var(--text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.done__hint,
.done__cover {
  margin: 0;
}

.done__copy,
.done__enter {
  flex: 1;
  justify-content: center;
}
</style>
