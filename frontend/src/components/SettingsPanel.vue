<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppModal from '@/components/AppModal.vue'
import SegmentedControl, { type SegOption } from '@/components/SegmentedControl.vue'
import { LogIn, RotateCcw, User } from '@/components/icons'
import { readHiddenTrips, unhideAllTrips, unhideTrip } from '@/composables/useRecentTrips'
import { useAuthStore } from '@/stores/auth'
import { useFeedbackStore } from '@/stores/feedback'
import { useSettingsStore } from '@/stores/settings'
import { useTripStore } from '@/stores/trip'
import type { TripSummary } from '@/types/domain'
import { ApiError, apiFetch } from '@/utils/api'
import type { BasemapPref, FontSizePref, MotionPref, ThemePref } from '@/utils/prefs'
import { isTravelMode, TRAVEL_MODE_OPTIONS } from '@/utils/tripmodes'
import { MIN_PER_DAY, formatMin, parseHHMM } from '@/utils/time'

/**
 * 设置面板：界面偏好（主题、字号、动效、底图、精灵）+ 这一趟行程的两项共享设置。
 *
 * 三条边界决定了这个文件里为什么同时住着两类完全不同的开关：
 *
 * 1. **只有「这趟行程」那一节会发 op**，其余五项是本机 localStorage 加账号上的一份副本，
 *    绝不进 trips、绝不进 op。所以那一节的标题自己写明「同行可见」，否则一个面板里混着
 *    两种作用域，用户只能靠猜——猜错的代价是以为翻了深色会把同伴的界面也翻掉。
 * 2. **改动即时生效，没有「保存」按钮**。偏好这东西点一下就看见结果，留一个保存键只会让
 *    人怀疑没生效；写回账号是 settings store 里那趟防抖 300ms 的后台事，不打扰这里。
 * 3. **面板挂在 App.vue，开合归 settings store**：首页齿轮和行程页「更多」是两个入口、
 *    一个实例。行程那一节按路由出现，在没有行程的地方它无从渲染。
 *
 * 分段控件的 `v-model` 走 string（见 SegmentedControl），所以每一档都有一个本地 computed
 * 负责把取值断回联合类型：这一处断言是整条链上唯一的收口点。
 */
const route = useRoute()
const settings = useSettingsStore()
const auth = useAuthStore()
const tripStore = useTripStore()
const feedback = useFeedbackStore()

const THEME_OPTIONS: readonly SegOption[] = [
  { value: 'auto', label: '跟随系统' },
  { value: 'light', label: '亮色' },
  { value: 'dark', label: '暗色' },
]
const FONT_OPTIONS: readonly SegOption[] = [
  { value: 'md', label: '标准' },
  { value: 'lg', label: '大' },
  { value: 'xl', label: '特大' },
]
const MOTION_OPTIONS: readonly SegOption[] = [
  { value: 'auto', label: '完整' },
  { value: 'reduce', label: '减少', hint: '关掉过渡与动画' },
]
const BASEMAP_OPTIONS: readonly SegOption[] = [
  { value: 'auto', label: '跟随主题' },
  { value: 'light', label: '始终亮色', hint: '深色下仍用浅色地图' },
]
const PET_OPTIONS: readonly SegOption[] = [
  { value: 'on', label: '显示' },
  { value: 'off', label: '隐藏', hint: '仍可从「更多」菜单唤起' },
]

const themeModel = computed<ThemePref>({
  get: () => settings.prefs.theme,
  set: (value) => settings.set('theme', value),
})
const fontModel = computed<FontSizePref>({
  get: () => settings.prefs.font_size,
  set: (value) => settings.set('font_size', value),
})
const motionModel = computed<MotionPref>({
  get: () => settings.prefs.motion,
  set: (value) => settings.set('motion', value),
})
const basemapModel = computed<BasemapPref>({
  get: () => settings.prefs.basemap,
  set: (value) => settings.set('basemap', value),
})
const petModel = computed<string>({
  get: () => (settings.prefs.pet_visible ? 'on' : 'off'),
  set: (value) => settings.set('pet_visible', value === 'on'),
})

/** 选了「跟随系统」时要说出跟出来的结果：否则这一档到底是什么，屏幕上没有任何地方回答。 */
const resolvedHint = computed(() =>
  settings.prefs.theme === 'auto'
    ? `跟随系统：当前为${settings.theme === 'dark' ? '深色' : '浅色'}`
    : '',
)

/* ---------- 这趟行程（唯一发 op 的一节） ---------- */

const trip = computed(() => tripStore.trip)
const showTrip = computed(() => route.name === 'trip' && !!trip.value)
const travelMode = computed<string>(() => trip.value?.travel_mode ?? 'driving')

/** `<input type="time">` 只认补零的 HH:MM，而 formatMin 对跨日的分钟会前缀「次日」。 */
function toHHMM(minutes: number): string {
  return formatMin(((minutes % MIN_PER_DAY) + MIN_PER_DAY) % MIN_PER_DAY)
}

const dayStart = ref(toHHMM(540))

// 打开面板时从行程现值灌一次草稿，而不是常驻绑定：行程会被同行人改动，而退场动画期间
// 面板还挂着，绑定值会显示一个刚刚被改掉的旧时刻。也只认 change（失焦或回车），不认
// input——每敲一个字符发一条 op，同伴那边就是每秒一次时间跳动。
watch(
  () => [settings.open, trip.value?.id, trip.value?.day_start_min] as const,
  () => {
    if (settings.open && trip.value) dayStart.value = toHHMM(trip.value.day_start_min)
  },
  { immediate: true },
)

function setTravelMode(value: string) {
  if (!isTravelMode(value) || !trip.value) return
  if (trip.value.travel_mode === value) return
  tripStore.updateTripFields({ travel_mode: value })
}

function commitDayStart() {
  const minutes = parseHHMM(dayStart.value)
  if (minutes === null) {
    dayStart.value = toHHMM(trip.value?.day_start_min ?? 540)
    return
  }
  if (trip.value) {
    if (trip.value.day_start_min === minutes) return
    tripStore.updateTripFields({ day_start_min: minutes })
  }
  feedback.show({ message: `每天从 ${dayStart.value} 开始排第一站` })
}

/* ---------- 首页：已从首页移除的行程 ---------- */

const onHome = computed(() => route.name === 'home')
const hiddenIds = ref<string[]>([])
const hiddenSummaries = ref<Record<string, TripSummary>>({})
const hiddenRestored = ref(0)

/** 批量摘要接口一次只回 24 条（服务端 SUMMARY_MAX_IDS），列得出名字的就这么多。 */
const HIDDEN_LIST_MAX = 24

const hiddenRows = computed(() =>
  hiddenIds.value.slice(0, HIDDEN_LIST_MAX).map((id) => {
    const s = hiddenSummaries.value[id]
    return { id, name: s?.title || '未命名行程', city: s?.city ?? '' }
  }),
)
const hiddenOverflow = computed(() => Math.max(0, hiddenIds.value.length - HIDDEN_LIST_MAX))

async function loadHidden() {
  const ids = readHiddenTrips()
  hiddenIds.value = ids
  if (!ids.length) return
  try {
    const res = await apiFetch<{ trips: TripSummary[] }>(
      `/api/trips/summary?ids=${encodeURIComponent(ids.slice(0, HIDDEN_LIST_MAX).join(','))}`,
    )
    const next: Record<string, TripSummary> = {}
    for (const s of res.trips) next[s.id] = s
    hiddenSummaries.value = next
  } catch (err) {
    // 读不到标题就当这一节不存在：宁可整节不出现，也不摆一串行程 ID 让用户猜哪条是哪条。
    if (!(err instanceof ApiError)) console.warn('[settings] 已移除行程的摘要没读到', err)
    hiddenIds.value = []
  }
}

function restore(id: string) {
  unhideTrip(id)
  hiddenIds.value = readHiddenTrips()
}

function restoreAll() {
  hiddenRestored.value = unhideAllTrips()
  hiddenIds.value = []
}

async function logout() {
  // 不关面板：退出后这一节自己换成「未登录」那段说明，用户看得见自己刚刚做了什么。
  await auth.logout()
}

function closed() {
  hiddenRestored.value = 0
  settings.hide()
}

// 面板挂在 App.vue 上，换页不会把它卸掉：从「登录账号（可选）」跳走时必须由这里关掉，
// 否则退场的是遮罩，登录页顶上还压着一层设置抽屉。
//
// 盯的是 path 而不是 fullPath：行程页把当前页签写进 ?pane=（TripView 那个 immediate 的
// replace 甚至会补掉一个空参数），按 fullPath 判就把「刚点开设面板」当成换页关掉——
// 症状是从「更多」菜单点设置毫无反应，首页齿轮却好的。
watch(() => route.path, () => settings.hide())

watch(
  () => settings.open,
  (open) => {
    if (!open) return
    hiddenRestored.value = 0
    if (onHome.value) void loadHidden()
    else hiddenIds.value = []
  },
)
</script>

<template>
  <AppModal
    v-if="settings.open"
    :autofocus="false"
    title="设置"
    sub="界面偏好只改这台设备；标了「同行可见」的那一节会同步给同行人。"
    @close="closed"
  >
    <div class="set">
      <section class="set__sec">
        <h3 class="set__h">界面</h3>
        <p class="set__note tiny muted">
          深色模式换掉全站配色。字号与动效同时作用于弹层、卡片与地图标注。
        </p>
        <div class="field">
          <span class="set__label">主题</span>
          <SegmentedControl v-model="themeModel" :options="THEME_OPTIONS" label="主题" />
          <p v-if="resolvedHint" class="tiny muted">{{ resolvedHint }}</p>
        </div>
        <div class="field">
          <span class="set__label">字号</span>
          <SegmentedControl v-model="fontModel" :options="FONT_OPTIONS" label="字号" />
        </div>
        <div class="field">
          <span class="set__label">动效</span>
          <SegmentedControl v-model="motionModel" :options="MOTION_OPTIONS" label="动效" />
        </div>
        <div class="field">
          <span class="set__label">地图底图</span>
          <SegmentedControl v-model="basemapModel" :options="BASEMAP_OPTIONS" label="地图底图" />
        </div>
      </section>

      <section class="set__sec">
        <h3 class="set__h">行程助手</h3>
        <div class="field">
          <span class="set__label">右下角精灵</span>
          <SegmentedControl v-model="petModel" :options="PET_OPTIONS" label="右下角精灵" />
        </div>
      </section>

      <section v-if="showTrip" class="set__sec">
        <h3 class="set__h set__h--shared">这趟行程 · 同行可见</h3>
        <p class="set__note tiny muted">
          这两项属于行程本身，改动会同步给所有同行人。每日开始只定第一站的开排时间，后面各站按路程与停留顺延。
        </p>
        <div class="field">
          <span class="set__label">默认交通方式</span>
          <SegmentedControl
            :model-value="travelMode"
            :options="TRAVEL_MODE_OPTIONS"
            label="默认交通方式"
            @update:model-value="setTravelMode"
          />
        </div>
        <div class="field">
          <label class="set__label" for="set-daystart">每日开始</label>
          <input
            id="set-daystart"
            v-model="dayStart"
            class="input set__time"
            type="time"
            step="300"
            @change="commitDayStart"
          />
        </div>
      </section>

      <section v-if="onHome && hiddenIds.length" class="set__sec">
        <h3 class="set__h">已从首页移除</h3>
        <p class="set__note tiny muted">
          「从首页移除」只是这台设备上的过滤，行程本身与同行者的列表都不受影响。恢复后重新出现在首页。
        </p>
        <ul class="set__list">
          <li v-for="row in hiddenRows" :key="row.id" class="set__row">
            <span class="set__name">
              {{ row.name }}<span v-if="row.city" class="tiny muted"> · {{ row.city }}</span>
            </span>
            <button class="btn btn--sm" type="button" @click="restore(row.id)">
              <RotateCcw class="ic" :size="13" /> 恢复
            </button>
          </li>
        </ul>
        <p v-if="hiddenOverflow" class="tiny muted">另有 {{ hiddenOverflow }} 条未列出，可一次全部恢复。</p>
        <div class="set__acts">
          <button class="btn btn--sm" type="button" @click="restoreAll">全部恢复</button>
          <span v-if="hiddenRestored" class="tiny muted" role="status">已恢复 {{ hiddenRestored }} 条行程</span>
        </div>
      </section>

      <section class="set__sec">
        <h3 class="set__h">账号</h3>
        <template v-if="auth.user">
          <p class="set__note tiny muted">
            已登录 {{ auth.user.name }}。界面偏好随账号保存，换一台设备登录仍是这一套。
          </p>
          <div class="set__acts">
            <button class="btn btn--sm" type="button" @click="logout">
              <User class="ic" :size="13" /> 退出登录
            </button>
          </div>
        </template>
        <template v-else>
          <p class="set__note tiny muted">
            未登录也能用：界面偏好只存在这台设备的浏览器里。登录后偏好跟着账号走，顺手留下「我的活动」。
          </p>
          <div class="set__acts">
            <RouterLink class="btn btn--sm" :to="{ name: 'login' }">
              <LogIn class="ic" :size="13" /> 登录账号（可选）
            </RouterLink>
          </div>
        </template>
      </section>
    </div>
  </AppModal>
</template>

<style scoped>
.set {
  display: flex;
  flex-direction: column;
  gap: var(--s5);
  padding: var(--s4) var(--s4) var(--s5);
}

.set__sec {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.set__h {
  font-size: var(--t-micro);
  font-weight: 700;
  letter-spacing: var(--ls-label);
  color: var(--text-3);
}

/* 这一节的每一项都会广播，标题就得比别的重一档：扫一眼标题决定敢不敢点。 */
.set__h--shared {
  color: var(--warn);
}

.set__label {
  font-size: calc(12px * var(--fs-scale));
  font-weight: 700;
  letter-spacing: var(--ls-label);
  color: var(--text-2);
}

.set__note {
  line-height: 1.6;
}

.set__time {
  width: 148px;
  font-size: calc(14px * var(--fs-scale));
}

.set__list {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  list-style: none;
}

.set__row {
  display: flex;
  gap: var(--s3);
  align-items: center;
  justify-content: space-between;
  padding: var(--s2) var(--s3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.set__name {
  min-width: 0;
  overflow: hidden;
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.set__acts {
  display: flex;
  gap: var(--s3);
  align-items: center;
}

/* ---------- 手机：宿主已是底部抽屉，内部别再按宽屏尺度排 ---------- */
@media (max-width: 860px) {
  .set {
    gap: var(--s4);
    padding: var(--s3) var(--s4) var(--s4);
  }
  .set__row {
    min-height: 44px;
  }
}
</style>
