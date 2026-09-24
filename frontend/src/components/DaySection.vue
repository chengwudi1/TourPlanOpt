<script setup lang="ts">
import { computed, ref } from 'vue'

import PlaceCard from '@/components/PlaceCard.vue'
import type { RailMark } from '@/components/TimeRail.vue'
import { BedDouble, Car, ChevronDown, Flag, Footprints, Navigation, Plus, Ruler, X, Zap } from '@/components/icons'
import { useDragSort } from '@/composables/useDragSort'
import { useFlipList } from '@/composables/useFlipList'
import { usePlaceDrag } from '@/composables/usePlaceDrag'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { OptimizeResult } from '@/stores/trip'
import type { Day, Place } from '@/types/domain'
import { formatDistance, haversineM } from '@/utils/coords'
import { formatDuration, formatMin } from '@/utils/time'

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
}>()

const store = useTripStore()
const socket = useSocketStore()

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

/** 天色：六档 ramp 按 day_index 轮着用，这一天的色带底、左脊、序号牌、纵向轨道共用
 *  这三条变量。颜色在这里是「第几天」的编码，所以取值只能是既有的 ramp 阶，
 *  不现场发明色相。`--ramp-N-ink`（实心块上的字色）目前没有消费者了：序号牌不再
 *  是色底白字，登记在 docs/MASTHEAD-AND-RIBBON.md 的例外清单里。 */
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

// -- 行内路线连接（RouteConnector）：时间来自排程，距离前端 haversine 现算。 ----------

const AMAP_URI_MODE = { driving: 'car', walking: 'walk', straight: 'car' } as const
const MODE_ICONS = { driving: Car, walking: Footprints, straight: Ruler } as const

const modeIcon = computed(() => MODE_ICONS[store.trip?.travel_mode ?? 'driving'])

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
  const mode = AMAP_URI_MODE[store.trip?.travel_mode ?? 'driving']
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
  const mode = AMAP_URI_MODE[store.trip?.travel_mode ?? 'driving']
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
      '--dc-soft': `var(--ramp-${ramp}-soft)`,
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
      <span class="daysec__badge">第 {{ day.day_index + 1 }} 天</span>
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
/* 选择器多带一枚 `.card`：全局那条 `.panel .card { box-shadow: var(--shadow-sm) }` 也是
   两类的权重，只写 `.daysec` 会被它压掉——左脊就再也画不出来。既然要压过它，外阴影
   跟着它降的那一档走：一列里同时摆三块色带，每张都拿 --shadow-md 就等于没有层次。 */
.daysec.card {
  /* 色带底：这一天的天色按 55% 混进卡片色。混色而不是直接铺 ramp-soft，是因为
     三块天同屏时纯色带会连成一张日历；混过一档之后它是「垫了一层颜色」，
     票券（地点卡）压在上面才分得清谁浮着。 */
  --band: color-mix(in oklab, var(--dc-soft) 55%, var(--surface));
  position: relative;
  /* clip 而不是 hidden：hidden 会造出一个滚动容器，卡片里那条 `position: sticky` 的吸底
     栏就会以这一层为参照——它永远不滚，于是吸底栏永远吸不住。clip 裁得一样干净，
     但不接管滚动，也不产生层叠上下文之外的新参照系。 */
  overflow: clip;
  background: var(--band);
  /* 交给地点卡的三枚变量（PlaceCard 只认变量，不认谁是宿主）：
     票券不描边、只靠一道近影浮起来；轨道上那枚站点圆的环要跟着色带走，
     否则它会在这块天色上开一个白洞。 */
  --place-edge: transparent;
  --place-shadow:
    0 1px 0 var(--hairline),
    0 2px 6px color-mix(in oklab, var(--ink) 8%, transparent);
  --place-ring: var(--band);
  /* 左脊：6px 实心天色。用 inset 阴影而不是 border-left——描边会跟着圆角拐，
     而这一条要贴着盒子内侧直直地拉到底。 */
  box-shadow:
    inset 6px 0 0 var(--dc),
    var(--shadow-sm);
}

.daysec--on {
  border-color: var(--accent);
}

.daysec__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 9px 10px;
  cursor: pointer;
  user-select: none;
  transition: background var(--dur-fast) var(--ease-out);
}

/* 悬停的洗色跟着色带走：直接铺 --surface-hover 会在天色上开一块灰矩形。 */
.daysec__head:hover {
  background: color-mix(in oklab, var(--dc) 9%, transparent);
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
  border-radius: 6px;
}

.daysec__arrow svg {
  transition: transform var(--dur) var(--ease-inout);
}

.daysec--open .daysec__arrow svg {
  transform: rotate(180deg);
}

/* 序号牌从「色底白字胶囊」改成一行展示字：天色已经由左脊和整块底色说了，胶囊再说一遍
   就是同一件事讲两次。19px 的展示字压 --dc-deep，比原来那枚 12px 小药丸好认。 */
.daysec__badge {
  flex: 0 0 auto;
  font-family: var(--font-display);
  font-size: calc(19px * var(--fs-scale));
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: var(--ls-tight);
  color: var(--dc-deep);
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
  background: var(--band);
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
  background: var(--surface-2);
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
   入口是那颗文字级按钮，不再是一枚实心 btn——一列里到处都在喊「点我」就等于没人喊。 */
.daysec__empty {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px dashed var(--border);
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
}
</style>
