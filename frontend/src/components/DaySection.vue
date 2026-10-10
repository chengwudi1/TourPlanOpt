<script setup lang="ts">
import { computed, ref } from 'vue'

import PlaceCard from '@/components/PlaceCard.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import type { RailMark } from '@/components/TimeRail.vue'
import {
  BedDouble,
  Car,
  ChevronDown,
  Flag,
  Footprints,
  Navigation,
  Plus,
  Ruler,
  X,
  Zap,
} from '@/components/icons'
import { useDragSort } from '@/composables/useDragSort'
import { useFlipList } from '@/composables/useFlipList'
import { usePlaceDrag } from '@/composables/usePlaceDrag'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { OptimizeResult } from '@/stores/trip'
import { useWeatherStore } from '@/stores/weather'
import type { Day, Place } from '@/types/domain'
import { formatDistance, haversineM } from '@/utils/coords'
import { formatDuration, formatMin } from '@/utils/time'
import {
  DAY_MODE_FOLLOW,
  DAY_TRAVEL_MODE_OPTIONS,
  effectiveTravelMode,
  isDayModeChoice,
} from '@/utils/tripmodes'
import type { DayModeChoice } from '@/utils/tripmodes'
import {
  castOnDate,
  dateSlash,
  dateWithWeekday,
  tempRange,
  weatherIcon,
  weatherNote,
  weatherSentence,
} from '@/utils/weather'

/**
 * TREK 式「一天一个可折叠区块」。选中态（加地点/优化的目标）与展开态分离：
 * 点天头 = 选中（父组件负责顺带展开），点箭头 = 折叠/展开；展开态由父组件持久化。
 * 区块内部自成一体：bookend、地点行 + 路线连接行、控件排挂，全部读同一个 store。
 */
const props = defineProps<{
  day: Day
  expanded: boolean
}>()

const emit = defineEmits<{
  select: []
  toggle: []
  rename: []
  remove: []
  menu: [place: Place, pos: { x: number; y: number }]
  addHere: []
  /** 天头那句「先填写目的地城市」的落点：城市编辑的键在海报头那儿，这里只负责把人送过去。 */
  setCity: []
}>()

const store = useTripStore()
const socket = useSocketStore()
const weather = useWeatherStore()

const places = computed(() =>
  store.places
    .filter((p) => p.day_id === props.day.id)
    .slice()
    .sort((a, b) => a.sort_index - b.sort_index),
)

const timeline = computed(() => store.timelines[props.day.id] ?? null)
const warnings = computed(() => timeline.value?.warnings ?? [])
const result = computed(() =>
  store.optimizeResult?.day_id === props.day.id ? (store.optimizeResult as OptimizeResult) : null,
)
const dragger = computed(() => store.draggersByDay.get(props.day.id) ?? null)
const isSelected = computed(() => store.currentDayId === props.day.id)
const startPlace = computed(() => places.value.find((p) => p.id === props.day.start_place_id) ?? null)
const endPlace = computed(() => places.value.find((p) => p.id === props.day.end_place_id) ?? null)

/** 天色：六档 ramp 按 day_index 轮着用，这一天的左脊、站点圆、纵向轨道、天头悬停洗色
 *  共用这三条变量。颜色在这里是「第几天」的编码，所以取值只能是既有的 ramp 阶，
 *  不现场发明色相。天头那枚序号方片是例外：F 方案里它统一走珊瑚橙（--accent），
 *  不读天色，「第几天」的色号编码仍由这两条变量承担。`--ramp-N-ink`（实心块上的字色）
 *  目前没有消费者，登记在 docs/MASTHEAD-AND-RIBBON.md 的例外清单里。 */
const ramp = computed(() => (props.day.day_index % 6) + 1)

/** 天头概要：`N 个地点 · 09:00–21:40`。收束时刻先取服务端排程的 end_min（它把当天每一
 * 站都算进去了），再退回终点锚到达、最后一站推导值。直接用行数据会在「刚加的地点还没
 * 排程」时把 null 当 0，得出 09:00–01:00 这种荒谬区间。 */
const rangeText = computed(() => {
  const list = places.value
  const start = list[0]?.start_min
  if (start === null || start === undefined) return ''
  const scheduled = list.filter((p) => p.start_min !== null)
  const last = scheduled[scheduled.length - 1]
  const end =
    endPlace.value?.arrive_min ??
    timeline.value?.end_min ??
    (last ? (last.start_min ?? 0) + last.duration_min : null)
  return end === null || end === undefined ? '' : `${formatMin(start)}–${formatMin(end)}`
})

// -- 这一天是几号、天气怎么样 ------------------------------------------------------------

/** 高德的预报一次给 4 天，按日期查而不是按「第几天」查：第 3 天可能根本没有日期，
 *  而有日期的第 3 天也可能已经过去了。查不到就是不画，不猜。 */
const city = computed(() => store.trip?.city ?? '')
const cast = computed(() => castOnDate(weather.castsOf(city.value), props.day.date))
const wxIcon = computed(() => weatherIcon(cast.value))
const wxSentence = computed(() => weatherSentence(cast.value, props.day.date))
const wxTemp = computed(() => tempRange(cast.value))
/** 天头那一枚只放得下「9/28 ☁ 21~29°」，整句（含风向）与没天气的原因都挂在这里。 */
const wxTitle = computed(() => wxSentence.value || dateWithWeekday(props.day.date))
const wxNote = computed(() =>
  weatherNote(weather.castsOf(city.value), props.day.date, {
    cityFilled: !!city.value.trim(),
    reason: weather.reasonOf(city.value),
  }),
)

function setDayDate(event: Event) {
  const raw = (event.target as HTMLInputElement).value
  // 空值是一句真话：清空就是「这一天没定日期」，不是 1970-01-01。
  store.updateDay(props.day.id, { date: raw || null })
}

// -- 行内路线连接（RouteConnector）：时间来自排程，距离前端 haversine 现算。 ----------

const AMAP_URI_MODE = { driving: 'car', walking: 'walk', straight: 'car' } as const
const MODE_ICONS = { driving: Car, walking: Footprints, straight: Ruler } as const

/** 这一天实际生效的交通方式。后端排程与优化按 `day.travel_mode or trip.travel_mode`
 *  取值，界面上原先只读行程那一级——于是图标可能正在说一件算路没做的事。 */
const dayMode = computed(() => effectiveTravelMode(props.day, store.trip))
const modeIcon = computed(() => MODE_ICONS[dayMode.value])

/** 选择器显示的是「有没有单独设过」，不是生效值：跟随与「恰好和默认一样」必须分得开。 */
const dayModeChoice = computed<DayModeChoice>(() => props.day.travel_mode ?? DAY_MODE_FOLLOW)

function setDayMode(value: string) {
  if (!isDayModeChoice(value)) return
  const next = value === DAY_MODE_FOLLOW ? null : value
  if ((props.day.travel_mode ?? null) === next) return
  // travel_mode 在后端的 _TIMELINE_DAY_FIELDS 里：这一笔发出去会自动重排当天时刻并广播，
  // 这里不需要自己再调一次排程。
  store.updateDay(props.day.id, { travel_mode: next })
}

function legText(place: Place, index: number): string {
  const parts: string[] = []
  if (place.travel_min_before !== null) parts.push(`约 ${formatDuration(place.travel_min_before)}`)
  else parts.push('路程未知')
  if (index > 0) {
    const prev = places.value[index - 1]
    parts.push(formatDistance(haversineM(prev, place)))
  }
  return parts.join(' · ')
}

/** 高德 URI 全天导航：URI API 最多一个途经点，恰好 3 站时才带 via。 */
function dayNavUrl(): string | null {
  const list = places.value
  if (list.length < 2) return null
  const mode = AMAP_URI_MODE[dayMode.value]
  const common = `mode=${mode}&src=tourplanopt&coordinate=gaode&callnative=0`
  const point = (p: { lng: number; lat: number; name: string }) =>
    `${p.lng},${p.lat},${encodeURIComponent(p.name)}`
  const from = point(list[0])
  const to = point(list[list.length - 1])
  const via = list.length === 3 ? `&via=${point(list[1])}` : ''
  return `https://uri.amap.com/navigation?from=${from}&to=${to}${via}&${common}`
}

/** 卡片↔卡片导航链接（URI API：前一站 → 这一站）。 */
function navHref(place: Place, index: number): string | null {
  const mode = AMAP_URI_MODE[dayMode.value]
  const common = `mode=${mode}&src=tourplanopt&coordinate=gaode&callnative=0`
  const to = `${place.lng},${place.lat},${encodeURIComponent(place.name)}`
  if (index === 0) return `https://uri.amap.com/navigation?to=${to}&${common}`
  const prev = places.value[index - 1]
  const from = `${prev.lng},${prev.lat},${encodeURIComponent(prev.name)}`
  return `https://uri.amap.com/navigation?from=${from}&to=${to}&${common}`
}

/** 时刻轨道上的参照点：同一天里已经排定时刻的其它站。挑时刻要能指着别的站说
 * 「在它后面一小时」，所以基准只能是看得见的站，不能是一个算出来的抽象位置。 */
function railMarks(exceptId: string): RailMark[] {
  return places.value
    .filter((p) => p.id !== exceptId && p.start_min !== null)
    .map((p) => ({ name: p.name, min: p.start_min as number }))
}

// -- 拖拽：本天内排序 + 跨天搬走；拖动状态作为 presence 广播给同房间。 ------------------

const listEl = ref<HTMLElement | null>(null)
const { dragFromDay, dragOverDay, endPlaceDrag } = usePlaceDrag()

/** 指针悬在本天、且不是本天自己拿起来的：这时松手就会把一个地点送进来。 */
const isDropTarget = computed(
  () => dragOverDay.value === props.day.id && dragFromDay.value !== props.day.id,
)

useDragSort(
  listEl,
  (orderedIds) => store.reorderDay(props.day.id, orderedIds),
  (dragging) => {
    if (dragging) dragFromDay.value = props.day.id
    else endPlaceDrag()
    socket.sendPresence(props.day.id, store.selectedPlaceId, dragging ? props.day.id : null)
  },
  {
    group: 'trip-places',
    // 整行是把手之后，行内那三颗按钮与就地改名的输入框必须从拖拽源里摘出去：手一抖就把
    // 卡片拖走了，而这一次点击（或选字）也不会发生。编辑面板不在正文行里，本来就不参与。
    filter: 'button, input, textarea, select',
    onTransfer: (placeId, _fromDay, toDay, beforeId) =>
      store.movePlaceToDay(placeId, toDay, beforeId),
    onHoverList: (dayId) => {
      dragOverDay.value = dayId
    },
  },
)
// 一键优化、别人把地点挪走、跨天移动之后，行与行之间要看得见位移而不是瞬间换位。
useFlipList(
  listEl,
  () => places.value.map((p) => p.id),
)

// -- 控件排挂 -------------------------------------------------------------------------

const precise = ref(false)

function runOptimize() {
  void store.optimize(precise.value ? 'amap' : 'haversine', props.day.id)
}
</script>

<template>
  <section
    class="daysec card"
    :class="{ 'daysec--open': expanded, 'daysec--on': isSelected }"
    :style="{
      '--dc': `var(--ramp-${ramp})`,
      '--dc-deep': `var(--ramp-${ramp}-deep)`,
    }"
  >
    <header
      class="daysec__head"
      :title="isSelected ? '双击重命名这一天' : '点击选中这一天'"
      @click="emit('select')"
      @dblclick.prevent="emit('rename')"
    >
      <button
        class="daysec__arrow tap-pad"
        type="button"
        :aria-label="expanded ? '折叠这一天' : '展开这一天'"
        @click.stop="emit('toggle')"
      >
        <ChevronDown :size="15" />
      </button>
      <span class="daysec__num" aria-hidden="true">{{ day.day_index + 1 }}</span>
      <span class="daysec__badge">第 {{ day.day_index + 1 }} 天</span>
      <!-- 有日期才说话。没日期的天在海报头的倒计时里已经是「日期未定」了，这里再挂一个
           空胶囊等于把同一件缺失渲染两次。 -->
      <span v-if="day.date" class="daysec__when tiny" :title="wxTitle">
        <span class="daysec__whendate">{{ dateSlash(day.date) }}</span>
        <template v-if="cast">
          <component :is="wxIcon" class="daysec__wxicon" :size="12" />
          <span>{{ wxTemp }}</span>
        </template>
      </span>
      <span class="daysec__title">
        <template v-if="day.title">{{ day.title }}</template>
        <span v-if="warnings.length && !expanded" class="daysec__warnbit" title="这一天有排程提醒">
          !
        </span>
      </span>
      <span class="daysec__meta tiny">
        {{ places.length }} 个地点<template v-if="rangeText"> · {{ rangeText }}</template>
      </span>

      <span v-if="dragger" class="daysec__drag tiny" :style="{ borderColor: dragger.color }">
        {{ dragger.name }} 正在调整顺序…
      </span>
      <button
        v-else-if="!places.length && store.days.length > 1"
        class="daysec__del"
        type="button"
        title="删除该空白天"
        @click.stop="emit('remove')"
      >
        <X :size="12" />
      </button>
    </header>

    <div class="daysec__fold" :class="{ 'is-open': expanded }">
      <div class="daysec__foldclip">
        <div class="daysec__body">
          <div class="daysec__when-row">
            <span class="daysec__rowlabel tiny">日期</span>
            <!-- 日期是天气的钥匙：没日期的天既排不进倒计时也查不到预报，所以这一格
                 在展开态里常驻，而不是只给「有日期的天」看一眼。 -->
            <input
              class="input daysec__dateinput"
              type="date"
              :value="day.date ?? ''"
              :aria-label="`第 ${day.day_index + 1} 天的日期`"
              @change="setDayDate"
            />
            <span v-if="cast" class="daysec__wxline tiny">{{ wxSentence }}</span>
            <span v-else-if="wxNote.text" class="daysec__wxnote tiny muted">
              {{ wxNote.text }}
              <button
                v-if="wxNote.action === 'city'"
                class="daysec__wxact"
                type="button"
                @click="emit('setCity')"
              >
                填写城市
              </button>
              <button
                v-else-if="wxNote.action === 'retry'"
                class="daysec__wxact"
                type="button"
                @click="weather.refresh(city)"
              >
                重试
              </button>
            </span>
          </div>

          <div class="daysec__mode">
            <span class="daysec__modelabel tiny">交通方式</span>
            <SegmentedControl
              :model-value="dayModeChoice"
              :options="DAY_TRAVEL_MODE_OPTIONS"
              label="这一天的交通方式"
              @update:model-value="setDayMode"
            />
            <span v-if="dayModeChoice === DAY_MODE_FOLLOW" class="daysec__modehint tiny muted">
              跟随设置里的默认交通方式
            </span>
          </div>

          <div v-if="startPlace" class="bookend tiny">
            <Flag class="ic" :size="12" />
            从「{{ startPlace.name }}」出发
          </div>

          <ul
            ref="listEl"
            class="daysec__list"
            :class="{ 'daysec__list--locked': !!dragger, 'daysec__list--over': isDropTarget }"
            :data-day-id="day.id"
          >
            <template v-for="(place, index) in places" :key="place.id">
              <li v-if="index > 0" class="leg" :data-flip-key="`leg:${place.id}`">
                <span class="leg__chip">
                  <component :is="modeIcon" class="leg__icon" :size="11" />
                  <span class="leg__text tiny">{{ legText(place, index) }}</span>
                </span>
              </li>
              <PlaceCard
                :place="place"
                :index="index"
                :active="place.id === store.selectedPlaceId"
                :editing-by="store.viewersByPlace.get(place.id) ?? null"
                :nav-href="navHref(place, index)"
                :creator-color="store.creatorColorOf(place.added_by)"
                :marks="railMarks(place.id)"
                @select="store.selectPlace(place.id)"
                @close="store.selectPlace(null)"
                @remove="store.deletePlaceWithUndo(place.id)"
                @lock="(p, locked) => store.setPlaceLocked(p.id, locked)"
                @patch="(p, patch) => store.updatePlace(p.id, patch)"
                @menu="(p, pos) => emit('menu', p, pos)"
              />
            </template>
            <!-- 空天也得是个接收方：这一行不能换成 v-else 的 <p>，否则 listEl 不存在，
                 别天的地点就拖不进来了。里面那颗按钮才是「往这一天加地点」的入口。 -->
            <li v-if="!places.length" class="daysec__empty tiny">
              <template v-if="isDropTarget">松手即移到本天</template>
              <button v-else class="daysec__add" type="button" @click.stop="emit('addHere')">
                <Plus class="ic" :size="13" /> 添加地点到这一天
              </button>
              <span v-if="!isDropTarget" class="daysec__emptyhint">
                也可在地图空白处右键或长按直接添加该位置
              </span>
            </li>
          </ul>

          <div v-if="endPlace" class="bookend bookend--end tiny">
            <BedDouble class="ic" :size="12" />
            <template v-if="endPlace.arrive_min !== null">预计 {{ formatMin(endPlace.arrive_min) }} 到「{{ endPlace.name }}」</template>
            <template v-else>到「{{ endPlace.name }}」收尾</template>
          </div>

          <div v-if="warnings.length" class="daysec__warnings tiny">
            <div v-for="(w, i) in warnings" :key="i">{{ w }}</div>
          </div>

          <div v-if="places.length >= 2" class="daysec__actions">
            <template v-if="result">
              <span class="tiny muted daysec__summary">
                优化后全程交通 {{ formatDuration(result.summary.after_min) }}
                <template v-if="result.summary.saved_min > 0">
                  · 节省 {{ formatDuration(result.summary.saved_min) }}
                </template>
                <template v-if="!result.exact">（近似结果）</template>
              </span>
              <button class="btn btn--sm" type="button" @click="store.undoOptimize()">撤销优化</button>
              <button class="btn btn--sm btn--ghost" type="button" @click="store.dismissOptimizeResult()">
                知道了
              </button>
            </template>
            <template v-else>
              <button
                class="btn btn--primary btn--sm"
                type="button"
                :disabled="store.optimizing || places.length < 3"
                @click="runOptimize"
              >
                <Zap class="ic" :size="13" /> {{ store.optimizing ? '优化中…' : '一键优化顺序' }}
              </button>
              <label class="daysec__precise tiny muted" title="按高德真实路况算站间距离，会占用地图服务的调用额度；默认按直线距离，即时完成、不占额度">
                <input v-model="precise" type="checkbox" :disabled="store.optimizing" />
                精确优化
              </label>
              <a
                v-if="dayNavUrl()"
                class="btn btn--sm btn--ghost"
                :href="dayNavUrl()!"
                target="_blank"
                rel="noopener"
                title="在高德地图中打开该路线"
              >
                <Navigation class="ic" :size="13" /> 全天导航
              </a>
            </template>
          </div>
          <p v-if="places.length >= 2 && places.length < 3 && !result" class="tiny muted daysec__hint">
            需至少 3 个地点方可优化
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 天层是一页纸，不是一张色带：底色走中性的 `--paper`，不带外阴影。三级层次在这儿
   只靠一个事实分工——天是纸，地点卡是浮在纸上的票（--ticket + 一道近影）。整块铺天色
   时这两级是同一件事，三块天同屏就连成一张日历；身份只剩左边那条 4px 书脊。
   选择器多带一枚 `.card` 是为了压过全局 `.panel .card { box-shadow: var(--shadow-sm) }`——
   这一层要的就是「不投影」。 */
.daysec.card {
  position: relative;
  /* clip 而不是 hidden：hidden 会造出一个滚动容器，卡片里那条 `position: sticky` 的吸底
     栏就会以这一层为参照——它永远不滚，于是吸底栏永远吸不住。clip 裁得一样干净，
     但不接管滚动，也不产生层叠上下文之外的新参照系。 */
  overflow: clip;
  background: var(--paper);
  /* 交给地点卡的变量（PlaceCard 只认变量，不认谁是宿主）：站点圆的环要贴着天纸的
     底色走，否则它会在纸上开一个白洞。 */
  --place-ring: var(--paper);
  /* 书脊：4px 深天色。用 inset 阴影而不是 border-left——描边会跟着圆角拐，
     而这一条要贴着盒子内侧直直地拉到底。用 --dc-deep 而不是 --dc：
     浅天色离开色带垫底之后，在中性纸上根本认不出是一条脊。 */
  box-shadow: inset 4px 0 0 var(--dc-deep);
}

/* 选中态的珊瑚描边多带一枚 .card：全局 `.panel .card` 现在也写 border-color，
   两边同为 (0,2,0) 时谁赢只取决于样式注入顺序，这里不赌。 */
.daysec.card.daysec--on {
  border-color: var(--accent);
}

.daysec__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 9px 10px;
  cursor: pointer;
  user-select: none;
  transition: background var(--dur-fast) var(--ease);
}

/* 悬停的洗色仍跟着这一天的色走：中性纸上铺 --surface-hover 是一块没有身份的灰矩形，
   而这一行正是那一天的身份所在。 */
.daysec__head:hover {
  background: color-mix(in oklab, var(--dc) 9%, transparent);
}

/* 吸顶行：滚到一天中间时，「第 N 天 + 日期/天气」留在顶上，长列表里不会看着看着
   忘了自己在哪天。只在 ≥768px 开——窄屏一屏就是一天，吸顶只是多占一行。
   天纸改成半透明之后，头部再跟纸同色就压不住下穿的内容（0.72 的白底下正文在爬）；
   它比纸瓷实一档（--glass-dense），再借一层毛玻璃把下穿的字糊掉——顺带留下
   「吸附着的一块磨砂」的质感。这一条 blur 不属于大面积禁区：它不是内容区，
   是吸附时才浮起的一条约 40px 高的栏，量级与页签条、吸底 dock 同族。
   书脊与下发丝线用 inset 阴影重画：头部自己的底会盖住天层那条 4px 脊，
   重画之后吸附时脊跟着头部一起留着，看着像是同一条。 */
@media (min-width: 768px) {
  .daysec__head {
    position: sticky;
    /* 滚动容器自己带一圈 --pane-pad 内圈，而 sticky 钉的是「视口扣掉 padding」那条线：
       top: 0 会把吸顶行停在圈里侧，上沿漏出一条刚滚过的内容。按同一个数往回抬才贴住边线。 */
    top: calc(var(--pane-pad) * -1);
    z-index: 4;
    background: var(--glass-dense);
    -webkit-backdrop-filter: var(--glass-blur);
    backdrop-filter: var(--glass-blur);
    box-shadow:
      inset 4px 0 0 var(--dc-deep),
      0 1px 0 var(--border-faint);
  }
}

.daysec__arrow {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: var(--text-3);
  background: none;
  border: 0;
  border-radius: var(--radius-xs);
}

.daysec__arrow svg {
  transition: transform var(--dur) var(--ease-inout);
}

.daysec--open .daysec__arrow svg {
  transform: rotate(180deg);
}

/* 序号方片（F 的 .daynum）：珊瑚橙底 + 白字，30px、10px 圆角。天头从此有一个
   统一的锚点色——它也是这一行里唯一允许压重色的元素。 */
.daysec__num {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 30px;
  height: 30px;
  font-family: var(--font-display);
  font-size: calc(13.5px * var(--fs-scale));
  font-weight: 800;
  line-height: 1;
  color: var(--accent-ink);
  background: var(--accent);
  border-radius: var(--radius-xs);
}

/* 「第 n 天」按 mock 的 h4：17px 展示字压正文色。它不读天色——编号已经由方片
   和这一句各说一遍，颜色再掺进来就是第三遍。 */
.daysec__badge {
  flex: 0 0 auto;
  font-family: var(--font-display);
  font-size: calc(17px * var(--fs-scale));
  font-weight: 800;
  line-height: 1.1;
  letter-spacing: var(--ls-tight);
  color: var(--text);
}

.daysec__title {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daysec__warnbit {
  display: inline-grid;
  place-items: center;
  width: 14px;
  height: 14px;
  font-size: calc(10px * var(--fs-scale));
  font-weight: 700;
  color: var(--warn);
  background: var(--warn-soft);
  border: 1px solid var(--warn-border);
  border-radius: 50%;
}

.daysec__meta {
  flex: 1 1 auto;
  overflow: hidden;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daysec__drag {
  flex: 0 0 auto;
  padding: 2px 8px;
  border: 1px dashed var(--hairline);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  animation: daysec-drag-in var(--dur) var(--ease-out);
}

@keyframes daysec-drag-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
}

.daysec__del {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--text-3);
  background: none;
  border: 0;
  border-radius: 50%;
}

.daysec__del:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

/* 展开/收起＝grid-template-rows 在 0fr↔1fr 之间插值。
   reduced-motion 下全局规则把 transition 关掉，直接开合，不需要 JS 参与。 */
.daysec__fold {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur-slow) var(--ease-inout);
}

.daysec__fold.is-open {
  grid-template-rows: 1fr;
}

/* 裁剪交给这层不带 padding 与边框的壳：0fr 时它的盒高才是真的 0。让带留白和分隔线的 body
   自己缩，它最低也有 13px，收起态就是一条永远消不掉的残影。
   同样是 clip：这一层要是滚动容器，卡片里的吸底栏就以它为参照，而它的高度由内容撑开、
   永远不滚——出口就会跟着面板一起滚出屏幕，正是这一轮要修掉的那件事。 */
.daysec__foldclip {
  min-height: 0;
  overflow: clip;
  visibility: hidden;
  transition: visibility 0s linear var(--dur-slow);
}

/* 收起时内容只是被裁掉、没被移出焦点链：延迟到动画结束再隐，展开则立刻可见。 */
.daysec__fold.is-open .daysec__foldclip {
  visibility: visible;
  transition-delay: 0s;
}

.daysec__body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px 10px 10px;
  border-top: 1px solid var(--border-faint);
}

/* 展开时内容分两拍落位：书挡先到、列表跟上，折叠动画就不再是一整块被"掀开"。
   只动 opacity 不动 transform——列表容器上挂着 sortablejs，祖先有位移会让它在拖拽
   起点量到错的几何；动画用 backwards，结束后不留合成层也不留层叠上下文。 */
.daysec__fold.is-open .daysec__body > * {
  animation: fade-in var(--dur-slow) var(--ease-out) backwards;
}
.daysec__fold.is-open .daysec__body > *:nth-child(2) {
  animation-delay: calc(var(--stagger) * 1);
}
.daysec__fold.is-open .daysec__body > *:nth-child(n + 3) {
  animation-delay: calc(var(--stagger) * 2);
}

.bookend {
  position: relative;
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 4px 2px 4px 20px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

/* 起点是空心环，站点是实心圆：两种东西在轨道上必须能靠形状分开。
   left: 3px 把它压在同一道中线上（凹槽 20px、节点 10px 宽）。 */
.bookend::before {
  position: absolute;
  top: 50%;
  left: 3px;
  width: 10px;
  height: 10px;
  content: "";
  background: var(--paper);
  border: 2px solid var(--dc);
  border-radius: 50%;
  transform: translateY(-50%);
}

.bookend--end {
  justify-content: flex-end;
}

/* 终点那一枚右对齐，左侧凹槽里不该再留它的节点。 */
.bookend--end::before {
  display: none;
}

/* 天头那一枚：`9/28 ☁ 21~29°`。整句（含风向）在 title 里——窄栏放不下，也不该放下：
   这一枚回答的是「那天要不要带伞」，不是天气详情。F 的胶囊语言：半透明白底 +
   一道白描边（玻璃上的元素靠白边勾轮廓，不靠加深底色），与 .when 出身无关。 */
.daysec__when {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 4px;
  align-items: center;
  padding: 1px 7px;
  color: var(--text-2);
  background: color-mix(in srgb, var(--glass) 80%, transparent);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-pill);
  font-variant-numeric: tabular-nums;
}

.daysec__whendate {
  font-weight: 600;
  color: var(--dc-deep);
}

.daysec__wxicon {
  flex: 0 0 auto;
}

/* 日期与天气共用展开态的第一行。左缩进对齐下面那条 20px 轨道凹槽，否则这一行会比
   下面的站整体左移一截（交通方式那一行同一条规矩）。 */
.daysec__when-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  align-items: center;
  padding: 2px 2px 0 20px;
}

.daysec__rowlabel {
  color: var(--text-3);
}

.daysec__dateinput {
  /* 原生 date 要按「年月日」三段的实际内容给宽，窄了会把年份裁掉一半；
     高度跟着分段控件那一档走，一行里两个控件不该一高一矮。 */
  width: 148px;
  min-height: 28px;
  padding: 3px 8px;
  font-size: calc(12px * var(--fs-scale));
  font-variant-numeric: tabular-nums;
}

.daysec__wxline {
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

.daysec__wxnote {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

/* 「为什么没有天气」后面必须跟一个走得通的键，光解释没用。 */
.daysec__wxact {
  min-height: 24px;
  padding: 2px 9px;
  color: var(--accent-strong);
  background: var(--accent-soft);
  border: 0;
  border-radius: var(--radius-pill);
  cursor: pointer;
}

.daysec__wxact:hover {
  text-decoration: underline;
}

/* 天级交通方式与起点锚共用那条 20px 轨道凹槽基线，否则它会比下面的站整体左移一截。
   整行可折：窄栏里四档控件优先保住，提示文字自己折到下一行去。 */
.daysec__mode {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  align-items: center;
  padding: 2px 2px 6px 20px;
}

.daysec__modelabel {
  color: var(--text-3);
}

.daysec__mode :deep(.seg) {
  /* 分段控件默认是 1fr 等宽，作为弹性项会被内容撑开——这里要的是「贴着标签的一小条」，
     不是横跨整行的工具条。 */
  flex: 0 1 auto;
}

.daysec__list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* 左凹槽只让给轨道：线路画在这 20px 里，卡片本身不跟着缩，
     否则侧栏 400px 的卡片会被再啃掉一截正文。 */
  padding: 0 0 0 20px;
  margin: 0;
  list-style: none;
}

/* 正被拖进来的那一天：虚线 + 浅色垫，落在列表本身，行不跟着位移。 */
.daysec__list--over {
  outline: 2px dashed var(--accent);
  outline-offset: 3px;
  background: var(--accent-soft);
  border-radius: var(--radius-sm);
}

.daysec__list--locked {
  opacity: 0.6;
  pointer-events: none;
}

/* 站与站之间这一段就是轨道本身：线段落在左凹槽的中线上，
   虚线往下流——行程的方向在这儿是「向下」，不是原来的「向右」。 */
.leg {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 21px;
  list-style: none;
}

.leg::before {
  position: absolute;
  top: -4px;
  bottom: -4px;
  left: -13px;
  width: 2px;
  content: "";
  background-image: repeating-linear-gradient(180deg, var(--dc) 0 5px, transparent 5px 10px);
  animation: leg-flow 1.3s linear infinite;
}

/* 一根短横线把胶囊引到轨道上，读数才不会像飘在空里。 */
.leg::after {
  position: absolute;
  top: 50%;
  left: -12px;
  width: 10px;
  height: 1px;
  content: "";
  background: var(--hairline);
  transform: translateY(-50%);
}

@keyframes leg-flow {
  to {
    background-position-y: 10px;
  }
}

.leg__chip {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 1px 7px;
  /* 半透明纸上的小胶囊不能再垫实色 --surface-2：那是一块不透明的浅灰底，
     会像在纸上开了一个洞。改用正文色的 7% 洗色，亮暗两题都成立。 */
  background: color-mix(in srgb, var(--text) 7%, transparent);
  border-radius: var(--radius-pill);
}

.leg__icon {
  flex: 0 0 auto;
  color: var(--dc-deep);
}

.leg__text {
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

/* 空着的一天用虚线读出来：实心描边是「有内容的卡」，虚线是「这里还等东西」。
   底与描边走 mock 的 .addbox 语言：更淡的一层玻璃 + 2px 虚线。
   入口是那颗文字级按钮，不再是一枚实心 btn——一列里到处都在喊「点我」就等于没人喊。 */
.daysec__empty {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  color: var(--text-2);
  background: color-mix(in srgb, var(--glass) 52%, transparent);
  border: 2px dashed var(--hairline);
  border-radius: var(--radius-sm);
}

.daysec__add {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  align-self: flex-start;
  min-height: 26px;
  padding: 0;
  color: var(--accent-strong);
  background: none;
  border: 0;
  cursor: pointer;
}

.daysec__add:hover {
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 3px;
}

.daysec__emptyhint {
  color: var(--text-3);
}

.daysec__warnings {
  padding: 7px 10px;
  color: var(--warn);
  background: var(--warn-soft);
  border-radius: var(--radius-sm);
}

.daysec__actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  padding-top: 2px;
}

.daysec__summary {
  flex: 1 1 auto;
  min-width: 0;
  font-variant-numeric: tabular-nums;
}

.daysec__precise {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  /* 落点是整条 label（里面那颗原生复选框只有 13×13）。 */
  min-height: 28px;
}

.daysec__hint {
  margin: -4px 0 0;
}

.daysec__actions a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

/* 窄屏那条侧栏就是整屏宽，6px 的实心脊会开始吃正文的起视位置；天色本身已经在
   序号牌和底色上说过一遍了，这里只把脊收到 4px。 */
@media (max-width: 860px) {
  .daysec.card {
    box-shadow:
      inset 4px 0 0 var(--dc),
      var(--shadow-sm);
  }

  /* 触屏落点：这三颗都是 20~26px 的裸图标/文字钮，拇指按不准（O6 同族）。 */
  .daysec__del {
    width: 30px;
    height: 30px;
  }

  .daysec__add {
    min-height: 34px;
  }

  .daysec__precise {
    min-height: 34px;
  }

  /* 日期与「填写城市/重试」都落在触屏上：24~28px 的落点拇指按不准（O6 同族）。 */
  .daysec__dateinput,
  .daysec__wxact {
    min-height: 34px;
  }
}
</style>
