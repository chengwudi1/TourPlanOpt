<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AmapKeyCheck from '@/components/AmapKeyCheck.vue'
import AppModal from '@/components/AppModal.vue'
import AssistantPanel from '@/components/AssistantPanel.vue'
import ChecklistPanel from '@/components/ChecklistPanel.vue'
import CoverPicker from '@/components/CoverPicker.vue'
import DaySection from '@/components/DaySection.vue'
import ExpensePanel from '@/components/ExpensePanel.vue'
import JoinGate from '@/components/JoinGate.vue'
import MapPanel from '@/components/MapPanel.vue'
import MessagePanel from '@/components/MessagePanel.vue'
import OpTicker from '@/components/OpTicker.vue'
import PlaceSearch from '@/components/PlaceSearch.vue'
import StashPanel from '@/components/StashPanel.vue'
import Sprite from '@/components/Sprite.vue'
import RecommendPanel from '@/components/RecommendPanel.vue'
import TripHeader from '@/components/TripHeader.vue'
import TripMasthead from '@/components/TripMasthead.vue'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BedDouble,
  ChevronDown,
  Compass,
  ExternalLink,
  Flag,
  List,
  Lock,
  LockOpen,
  Map as MapIcon,
  MessageSquare,
  Package,
  Pencil,
  Plus,
  Receipt,
} from '@/components/icons'
import { useCityCoverCandidates } from '@/composables/useCityCover'
import { getClientId, setClientName } from '@/composables/useClientIdentity'
import { useCopy } from '@/composables/useCopy'
import { useNarrowView } from '@/composables/useNarrowView'
import { recordRecentTrip } from '@/composables/useRecentTrips'
import { useAuthStore } from '@/stores/auth'
import { useDialogStore } from '@/stores/dialog'
import { useFeedbackStore } from '@/stores/feedback'
import { useSettingsStore } from '@/stores/settings'
import { useSocketStore } from '@/stores/socket'
import { useTripStore } from '@/stores/trip'
import type { Place, Poi } from '@/types/domain'
import { ApiError, apiFetch } from '@/utils/api'
import { anchorMenu, type MenuPosition } from '@/utils/anchorMenu'
import { formatMoney } from '@/utils/money'
import { formatMin } from '@/utils/time'

const props = defineProps<{ tripId: string }>()

const store = useTripStore()
const socket = useSocketStore()
const auth = useAuthStore()
const settings = useSettingsStore()
const feedback = useFeedbackStore()
const dialog = useDialogStore()
const copy = useCopy()

/** Per-tab: sessionStorage identity means a reload keeps your name, a new tab asks
 * again -- exactly the granularity the collaboration semantics need. */
const joined = ref(Boolean(sessionStorage.getItem('tourplanopt.joined')))

const selfId = getClientId()

/** Mobile: the two panes become full-screen contexts; this picks which one shows. */
const mobileView = ref<'list' | 'map'>('list')

/**
 * 窄屏的地点编辑器开在底部抽屉里（PlaceCard 的 `active && narrow`），一开就盖住屏幕下面
 * 72dvh。地图按半个抽屉的高度把自己往上抬：`panTo` 只会把这一站放到全屏正中，而正中
 * 恰恰在抽屉背后——改了停留时长、点了优化，屏幕上什么都不会动。
 */
const narrow = useNarrowView()
const sheetOpen = computed(() => narrow.value && !!store.selectedPlaceId)

/**
 * 整栏空白时上屏的那一句：REST 没读到（loadError）优先，它带着具体原因；
 * 读到过、但连接被服务端判了死刑（同伴把行程删了）走 socket 的 fatalError。
 * 两条都走同一个兜底块——一栏什么都没有而不说话，用户只能猜。
 */
const loadFail = computed(() => store.loadError ?? socket.fatalError)

/**
 * 海报头（D1）：往下滚进列表就把封面收起来，滚回顶再放开。
 *
 * 两个门限不相等是有意的：收起与展开写成同一个值，就会在临界点上每滚一格抖一次；
 * 12 那条留出的是「明确回到了顶上」的距离。监听挂在宿主的 `.panel__scroll` 上而不是
 * 组件里——滚动条是这一层的东西，内容件不该反过来偷看自己摆在哪个容器里。
 *
 * 收起门限从 72 抬到 148：照片的视差要读出「列表在封面底下滚过」需要一段行程，
 * 72 那一级封面先折了，视差就永远没被看见过——「像贴进来的一张截图」病的这一半在这。
 * 滚动偏移一并交给组件（`mastScrollY`）：位移写在照片上，容器又不在照片这一层，
 * 除了宿主没人知道已经滚过多少。
 *
 * **收起不许动读数**（M34、M35）。海报头不在滚动容器里面，收起它等于把视口抬高整整
 * 一截封面，于是可滚范围同量缩小，浏览器随即把 `scrollTop` 夹到新的最大值——那一夹就是
 * 「下拉框拉到底部会自动弹上去」。M34 的第一版用门槛躲：当前位置底下剩的清单装不下一截
 * 封面就不许收。它确实不弹了，代价是「可滚范围本来就比封面矮」的短列表整段都不折封面，
 * 于是同一套界面在两段行程上是两种行为——用户按「南宁不收、南京收」把它打回了。
 *
 * 现在改成正面补：折叠让出多少，就在清单末尾垫多少（`mastPad` → `--tail-h`）。滚动区
 * 高度多出那一截、内容也多出那一截，可滚范围自始至终没变小，读数没有可被夹的理由，
 * 短列表长列表于是走同一条路。垫的量只算「这一收会夹掉的像素」
 * （`steal = top - (room - occupied)`），长列表上它是 0，一分空间也不多占；
 * 只有清单本来就比屏幕短的那几趟会露出末尾的空档，而那一点空档是清单自己读完了，
 * 不是封面腾出来的洞。补白与封面用同一条 `--dur-slow var(--ease-inout)` 过渡，
 * 两边始终互补，中途也不会出现「可滚范围短暂变小」的夹。
 *
 * 反过来展开不需要这种补：`top < 12` 时视口缩回去只会让可滚范围变大，动不了读数。
 */
const mastCollapsed = ref(false)
const mastScrollY = ref(0)

/** 快照没到手就没有名字、没有城市、没有地点数，这块只能整条不上屏；
 *  读失败那一栏有自己的兜底块，别在它头顶再挂一条封面。 */
const mastShown = computed(() => !!store.trip && !loadFail.value)

const mastRef = ref<InstanceType<typeof TripMasthead> | null>(null)

/** 封面这一整块从列表头上占掉的高度（含跟着一起收的外边距——收起让出来的正是整块，
 *  只量元素本身会少算 margin 那一格，补白就偏乐观了）。取历史最大值：收起态读到 0、
 *  重展开的过渡没走完会读小；换到更矮的封面形态只会让它偏大，而偏大在这里是安全方向
 *  ——宁可多垫一格，不可挤掉读数。 */
let mastReserve = 0

function mastOccupied(): number {
  const el = mastRef.value?.rootEl
  if (el) {
    const h = el.offsetHeight + Number.parseFloat(getComputedStyle(el).marginTop || '0')
    if (h > mastReserve) mastReserve = h
  }
  return mastReserve
}

/** 折叠会夹掉的那一截，垫在清单末尾接住（写进 `--tail-h`，见 `.panel__content::after`）。 */
const mastPad = ref(0)

function onPanelScroll(e: Event) {
  const el = e.target as HTMLElement
  const top = el.scrollTop
  const room = el.scrollHeight - el.clientHeight - mastPad.value
  mastScrollY.value = top
  if (mastCollapsed.value) {
    if (top < 12) {
      mastCollapsed.value = false
      mastPad.value = 0
    }
    return
  }
  if (top > 148) {
    const occupied = mastOccupied()
    const steal = top - (room - occupied)
    if (steal > 0) mastPad.value = Math.min(occupied, Math.round(steal))
    mastCollapsed.value = true
  }
}

// 换了另一段行程：滚动容器回到了顶上，但这一趟不会有第二次 scroll 来把封面放开。
watch(
  () => store.trip?.id,
  () => {
    mastCollapsed.value = false
    mastScrollY.value = 0
    mastPad.value = 0
    mastReserve = 0
  },
)

/**
 * 抽屉背后是地图，不是列表：列表那一屏被抽屉盖掉 72%，留在上面等于什么也看不见，
 * 而这一站长在哪儿恰好是改停留时长、看排程结果时最想知道的一件事。
 * 关掉抽屉把视图还给用户原来那一侧，不是无条件回列表。
 */
let viewBeforeSheet: 'list' | 'map' = 'list'
watch(sheetOpen, (open) => {
  if (open) {
    viewBeforeSheet = mobileView.value
    mobileView.value = 'map'
    return
  }
  if (mobileView.value === 'map') mobileView.value = viewBeforeSheet
})

// -- 分栏（M24b / M30）-------------------------------------------------------------------
// 行程 / 出行清单 / 费用 / 聊天是四块各自完整的界面，不再从上到下堆在同一条侧栏里。
// pane 写进 ?pane=：刷新留在当前页，前进后退也能跟上；「行程」不占参数，保持短链。

type Pane = 'trip' | 'checklist' | 'cost' | 'chat'
const PANES: Pane[] = ['trip', 'checklist', 'cost', 'chat']

function readPane(value: unknown): Pane {
  return PANES.includes(value as Pane) ? (value as Pane) : 'trip'
}

const route = useRoute()
const router = useRouter()
const pane = ref<Pane>(readPane(route.query.pane))

watch(
  pane,
  (p) => {
    void router.replace({ query: { ...route.query, pane: p === 'trip' ? undefined : p } })
  },
  // immediate：认不出来的 ?pane=xxx 也要把地址栏改回界面真正显示的那一档。
  { immediate: true },
)
// 后退键改的是路由，这里把它读回来；值没变时赋值不会触发上面那个 watch，不会来回打架。
watch(
  () => route.query.pane,
  (v) => {
    pane.value = readPane(v)
  },
)

/**
 * dock 的「行程 / 地图」是**行程这一页**的两个视图。清单或费用开着时按下去只改
 * `mobileView` 是看不见的——屏幕上什么都不动（O9 里最伤的一条表现）。所以换视图
 * 顺带把人带回行程页。
 */
function showMobileView(view: 'list' | 'map') {
  mobileView.value = view
  pane.value = 'trip'
}

/** dock 的清单 / 费用：从地图全屏切过去时顺带收回列表那侧，不然换回行程页还停在地图上。 */
function showPane(p: Pane) {
  pane.value = p
  mobileView.value = 'list'
}

// -- 聊天这一面（M30）--------------------------------------------------------------------
// 同一份 MessagePanel，两个宿主：宽屏是第四页签，窄屏是顶栏气泡开出的底部抽屉。
// `pane === 'chat'` 是意图（也是地址栏里那位）；下面这一位只管抽屉板子还在不在屏幕上——
// AppModal 的退场要播完才许卸载，所以不能拿 pane 直接 v-if 它。

const chatWanted = computed(() => pane.value === 'chat')
const chatSheetShown = ref(false)
const chatSheetEl = ref<InstanceType<typeof AppModal> | null>(null)

watch(
  chatWanted,
  (on) => {
    if (on) {
      if (narrow.value) chatSheetShown.value = true
      return
    }
    if (chatSheetShown.value) chatSheetEl.value?.close()
  },
  { immediate: true },
)

/* 抽屉开着时把窗口拉宽：页签宿主接管这一面，抽屉得自己走完退场，不然它会永远悬在半空。 */
watch(narrow, (isNarrow) => {
  if (!isNarrow && chatSheetShown.value) chatSheetEl.value?.close()
})

function onChatSheetClosed() {
  chatSheetShown.value = false
  if (pane.value === 'chat') pane.value = 'trip'
}

/** 「9+」封顶：一位数的徽标比两位数安静，而三百条未读也要说同一句「去看」。 */
const chatBadge = computed(() =>
  store.chatUnread > 9 ? '9+' : String(store.chatUnread),
)

/**
 * 气泡上那行「关于：某一站」按下去：切到那一天、选中那张卡，抽屉顺手关掉。
 *
 * 窄屏的选中会把地点编辑器抬起来（这是点卡片本来的行为），所以这里不额外解释「为什么
 * 又开了一个抽屉」——两件事都是用户自己按出来的。
 */
function openChatRef(ref: { placeId?: string; dayId?: string }) {
  if (ref.dayId) {
    selectDay(ref.dayId)
    return
  }
  const placeId = ref.placeId
  if (!placeId) return
  const place = store.places.find((p) => p.id === placeId)
  if (place) selectDay(place.day_id)
  store.selectPlace(placeId)
  if (chatSheetShown.value) chatSheetEl.value?.close()
}

// 徽标与回执上的「查看」：store 不碰路由，落到这里的这一句才决定去哪儿。
watch(
  () => store.chatOpenWanted,
  () => showPane('chat'),
)

/** 未读只在「界面切回前台」时补一条汇总——后台那几十分钟里浏览器连定时器都不给。 */
function onVisibility() {
  if (!document.hidden) store.flushChatSummary()
}

// -- 写入失败的反馈（M26a）----------------------------------------------------------------
// `store.opError` 仍是唯一真相（助手那侧靠它判断 optimize 有没有失败），这里只换呈现方式。
// 旧做法是渲染在 `.panel__scroll` 最顶部：用户滚到第 3 天添加地点被拒时，消息落在视口外，
// 屏幕上什么都没发生、只是刚加的那行自己消失了，而且它既不超时也没关闭按钮。
let opErrorToast: number | null = null
watch(
  () => store.opError,
  (err) => {
    // 清空不当作关闭信号：`applyRemoteOp` 对**每一条**远端广播都会把 opError 置 null，
    // 两个人的会话里别人一动，我刚要看的那条红色告警就没了——那正是 M26a 要修的病。
    // 回执的寿命交给它自己的计时器（danger 9 秒），只有下一条错误才顶掉上一条。
    if (!err) return
    if (opErrorToast !== null) feedback.dismiss(opErrorToast)
    // 连着一串拒绝时只留最新一枚，不然屏幕会被同一条消息糊满。
    opErrorToast = feedback.show({
      kind: err.level,
      message: err.message,
      hint: err.hint,
    })
  },
)

/** 清单与费用是整屏文档：宽屏下地图让位，窄屏下靠底部 dock 回地图。 */
const docOpen = computed(() => pane.value !== 'trip')

// -- 展开态（M15b）---------------------------------------------------------------------
// 选中与展开是两件事：点天头选中这一天（并展开它），点箭头只折叠/展开。折叠是本地
// 偏好，不进协议；记忆按行程分键，重新打开还是上次看到的样子。

const expandKey = `tourplanopt.day-expanded-${props.tripId}`
const expandedIds = ref<Set<string>>(new Set())
/** 用户亲手折叠过的天：排程提醒不再强行把它展开。 */
const touchedDays = new Set<string>()
let knownDays = new Set<string>()

function persistExpanded() {
  try {
    localStorage.setItem(expandKey, JSON.stringify([...expandedIds.value]))
  } catch {
    // 无痕模式下 localStorage 会抛；丢掉折叠偏好不影响功能。
  }
}

function setExpanded(dayId: string, on: boolean) {
  if (expandedIds.value.has(dayId) === on) return
  const next = new Set(expandedIds.value)
  if (on) next.add(dayId)
  else next.delete(dayId)
  expandedIds.value = next
  persistExpanded()
}

function toggleDay(dayId: string) {
  touchedDays.add(dayId)
  setExpanded(dayId, !expandedIds.value.has(dayId))
}

function selectDay(dayId: string) {
  store.currentDayId = dayId
  setExpanded(dayId, true)
}

function initExpand() {
  const ids = store.days.map((d) => d.id)
  knownDays = new Set(ids)
  let saved: unknown = null
  try {
    saved = JSON.parse(localStorage.getItem(expandKey) ?? 'null')
  } catch {
    saved = null
  }
  const next = new Set(
    Array.isArray(saved) ? saved.filter((id): id is string => ids.includes(id)) : [],
  )
  if (!Array.isArray(saved) && store.currentDayId) next.add(store.currentDayId)
  expandedIds.value = next
}

async function removeDay(dayId: string) {
  const ok = await dialog.confirm({
    title: '删除这空白的一天？',
    message: '这一天尚未安排地点，删除后将从行程中去掉，同伴的视图同步变化。',
    confirmLabel: '删除',
    danger: true,
  })
  if (ok) store.deleteDay(dayId)
}

async function renameDay(dayId: string) {
  const day = store.days.find((d) => d.id === dayId)
  if (!day) return
  const name = await dialog.prompt({
    title: '当天名称',
    message: `留空则恢复为默认的「第 ${day.day_index + 1} 天」。`,
    value: day.title,
    required: false,
    confirmLabel: '保存',
  })
  if (name === null) return
  store.updateDay(dayId, { title: name.trim() })
}

/**
 * 卡片菜单里的「重命名」：就地改名（展开态下那行地名就是输入框）是快路，这一条是保底——
 * 名字很长、手指在触屏上要点准输入框、或者从地图上点开又没展开列表时，它都得能改。
 */
async function renamePlace(place: Place) {
  closeCardMenu()
  const name = await dialog.prompt({
    title: '地点名称',
    value: place.name,
    confirmLabel: '保存',
    emptyMessage: '请输入地点名称',
  })
  if (name === null) return
  const next = name.trim()
  if (next && next !== place.name) store.updatePlace(place.id, { name: next })
}

const searchEl = ref<InstanceType<typeof PlaceSearch> | null>(null)
const recoEl = ref<InstanceType<typeof RecommendPanel> | null>(null)

/** 「发现」的开关长在添加地点那一行上，展开态由面板自己按行程记住；这里只留一份镜像，
    用来画箭头与 `aria-expanded`。 */
const recoOpen = ref(false)
const cityText = computed(() => store.trip?.city?.trim() ?? '')

/**
 * 空天那一行点进来：先选中这一天（新地点会落在这里），再把焦点送到搜索框。
 * 亮那一下不能省——点的是列表中段的一行，视线不在上方，没有回声就像点了没反应。
 */
let flashTimer: ReturnType<typeof setTimeout> | null = null
const flash = ref(false)

function askAddHere(dayId: string) {
  selectDay(dayId)
  searchEl.value?.focus()
  if (flashTimer) clearTimeout(flashTimer)
  flash.value = true
  flashTimer = setTimeout(() => (flash.value = false), 900)
}

async function setTripCity() {
  const city = await dialog.prompt({
    title: '目的地城市',
    message: '推荐与搜索的范围以该城市为准，留空则按已选地点自行判断。',
    value: store.trip?.city ?? '',
    placeholder: '例如 大理',
    required: false,
    confirmLabel: '保存',
  })
  if (city === null) return
  store.updateTripFields({ city: city.trim() })
}

async function renameTrip(current: string) {
  const name = await dialog.prompt({
    title: '行程名称',
    value: current,
    confirmLabel: '保存',
    emptyMessage: '请输入行程名称',
  })
  if (name === null) return
  store.updateTripFields({ title: name.trim() })
}

/* ---------- 封面（M36 / M37，行程页这一处宿主） ---------- */

const coverOpen = ref(false)

/** 城市候选只在面板开着的那一会儿去取：海报头已经不自动用它了，只有面板里那一档手动候选要列。
 *  关面板把源换成空串，composable 自己清空；同一座城市第二次打开走它模块级的结果缓存，不再发请求。 */
const cityCoverPhotos = useCityCoverCandidates(
  () => (coverOpen.value ? (store.trip?.city ?? '') : ''),
)

/** 面板里的「当前」要说的是用户亲手那张，不是链子上现在显示的那张：后者一换档就跟着变，
 *  「当前」这个字就成了谎话。 */
const customCover = computed(() => store.trip?.cover_url ?? '')

function openCoverPicker() {
  coverOpen.value = true
}

function onCoverPick(url: string) {
  if (!store.trip || store.trip.cover_url === url) return
  store.updateTripFields({ cover_url: url })
  feedback.show({ message: '封面已更新，同行的人都会看到' })
}

function onCoverAuto() {
  if (!store.trip?.cover_url) return
  store.updateTripFields({ cover_url: '' })
  feedback.show({ message: '已恢复默认封面', hint: '改用内置的那一张，与城市、地点图片无关' })
}

async function onCoverFile(blob: Blob) {
  try {
    await store.uploadCover(blob)
    feedback.show({ message: '封面已更新，同行的人都会看到' })
  } catch (err) {
    // 上传走的是 HTTP 而不是 op，socket 那条错误通道不会替它喊，所以这里必须自己报。
    feedback.show({
      kind: 'danger',
      message: '封面没有传上去',
      hint: err instanceof ApiError ? err.message : '网络或对接出了岔子，可以重来一次。',
    })
  }
}

async function copyShareLink() {
  const url = `${window.location.origin}/trip/${props.tripId}`
  await copy(url, {
    receipt: '分享链接已复制，同行者打开即可共同编辑',
    fallbackTitle: '分享链接',
  })
}

/** 文字版行程：贴群聊用。入口在顶栏「更多」菜单里。 */
async function copyTextItinerary() {
  const lines: string[] = []
  const title = store.trip?.title || '未命名行程'
  lines.push(`📍 ${title}${store.trip?.city ? `（${store.trip.city}）` : ''}`)
  for (const day of store.days) {
    const list = store.places
      .filter((p) => p.day_id === day.id)
      .sort((a, b) => a.sort_index - b.sort_index)
    lines.push('')
    lines.push(`第 ${day.day_index + 1} 天${day.title ? ` · ${day.title}` : ''}`)
    for (const place of list) {
      const time = place.start_min !== null ? `${formatMin(place.start_min)} ` : ''
      lines.push(`${time}${place.name}${place.address ? `（${place.address}）` : ''}`)
    }
  }
  const text = lines.join('\n')
  await copy(text, {
    receipt: '文字版行程已复制，可直接贴进群聊',
    fallbackTitle: '文字版行程',
  })
}

function onJoin() {
  joined.value = true
  sessionStorage.setItem('tourplanopt.joined', '1')
  socket.connect(props.tripId)
}

/** 兜底块上的「再试一次」：后端可能只是慢了一拍，不该逼用户刷新整页。 */
async function retryLoad() {
  try {
    await store.load(props.tripId)
  } catch {
    /* loadError 已经写好，兜底块自己会换文案 */
  }
  socket.connect(props.tripId)
}

onMounted(async () => {
  await auth.load()
  try {
    await store.load(props.tripId)
  } catch {
    // store.loadError already holds the message/hint pair for the banner below.
  }
  // Logged-in users skip the name gate: their account IS the identity, and asking
  // them to re-type it in every new tab reads as broken. Guests still see the gate
  // because per-tab identity is what makes two-window collaboration testable.
  if (auth.user && !joined.value) {
    joined.value = true
    // The composable's identity feeds added_by / participant rows; align it with the
    // account name so creator colours and the roster show the person, not a hex id.
    setClientName(auth.user.name)
    sessionStorage.setItem('tourplanopt.joined', '1')
  }
  initExpand()
  document.addEventListener('visibilitychange', onVisibility)
  // 打不开就不记：分享 ID 敲错一次，不该在首页留一条永远点不进去的历史。
  if (!store.loadError) recordRecentTrip(props.tripId)
  if (joined.value) socket.connect(props.tripId)
})

onBeforeUnmount(() => {
  if (flashTimer) clearTimeout(flashTimer)
  document.removeEventListener('visibilitychange', onVisibility)
  socket.disconnect()
})

// Switching days is a presence change too, not just selection changes.
watch(
  () => store.currentDayId,
  (dayId) => {
    socket.sendPresence(dayId, store.selectedPlaceId)
    if (dayId) setExpanded(dayId, true)
  },
)

/** 提醒藏在折叠的天里等于没有提醒：有 warnings 且用户没亲手折叠过的天自动展开。 */
watch(
  () => store.timelines,
  () => {
    for (const [dayId, t] of Object.entries(store.timelines)) {
      if (t.warnings.length && !touchedDays.has(dayId)) setExpanded(dayId, true)
    }
  },
)

/** 天增减：清理已消失的展开项，新出现的天默认展开（否则像行程凭空少了几天）。 */
watch(
  () => store.days.map((d) => d.id).join(','),
  () => {
    const alive = new Set(store.days.map((d) => d.id))
    const next = new Set([...expandedIds.value].filter((id) => alive.has(id)))
    for (const id of alive) if (!knownDays.has(id)) next.add(id)
    knownDays = alive
    expandedIds.value = next
    persistExpanded()
  },
)

async function onPoiPicked(poi: Poi) {
  store.opError = null
  store.addPlace({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function onStash(poi: Poi) {
  store.opError = null
  store.stashAdd({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

/** 地图选点：右键/长按地图某处 → regeo 预填名称 → 加入所选日期或暂存至想去清单。 */
const mapPick = ref<{
  lng: number
  lat: number
  name: string
  address: string
  loading: boolean
} | null>(null)

function onMapPick(point: { lng: number; lat: number }) {
  const round = (v: number) => Math.round(v * 1e6) / 1e6
  mapPick.value = {
    lng: round(point.lng),
    lat: round(point.lat),
    name: '',
    address: '',
    loading: true,
  }
  apiFetch<{ name: string; address: string }>(
    `/api/poi/regeo?lng=${mapPick.value.lng}&lat=${mapPick.value.lat}`,
  )
    .then((info) => {
      if (!mapPick.value) return
      mapPick.value.address = info.address || ''
      mapPick.value.name = info.name || info.address || '地图选点'
    })
    .catch(() => {
      // 没配 Web 服务 Key 时 regeo 不可用：仍可手动命名添加。
      if (mapPick.value) mapPick.value.name = '地图选点'
    })
    .finally(() => {
      if (mapPick.value) mapPick.value.loading = false
    })
}

function confirmMapPick() {
  const p = mapPick.value
  if (!p?.name.trim()) return
  store.opError = null
  store.addPlace({ name: p.name.trim(), lng: p.lng, lat: p.lat, address: p.address })
  mapPick.value = null
}

function stashMapPick() {
  const p = mapPick.value
  if (!p?.name.trim()) return
  store.opError = null
  store.stashAdd({ name: p.name.trim(), lng: p.lng, lat: p.lat, address: p.address })
  mapPick.value = null
}

/** 加入的就是当前选中的那一天：按钮上写清是哪一天，别让用户回头核对页签。 */
const mapPickDayLabel = computed(() => {
  const day = store.currentDay
  return day ? `第 ${day.day_index + 1} 天` : '行程'
})

/** 卡片操作菜单（右键/长按/⋯呼出）。 */
const cardMenu = ref<{ place: Place; x: number; y: number } | null>(null)
const cardMenuEl = ref<HTMLElement | null>(null)
const cardMenuPos = ref<MenuPosition>({ left: 0, top: 0 })

/**
 * 打开卡片菜单：先按点击处摆出来，挂载之后再量真实尺寸夹进视口（O5）。
 *
 * 原来写的是 `innerHeight - 150`——一个凭空的估计。菜单高度随行程天数长：七天行程有
 * 六行「移到 D×」再加固定项，约 300px，被裁掉的恰好是最常用的那几行。
 */
function openCardMenu(place: Place, pos: { x: number; y: number }) {
  cardMenuPos.value = { left: pos.x, top: pos.y }
  cardMenu.value = { place, ...pos }
  void nextTick(() => {
    if (cardMenuEl.value) {
      cardMenuPos.value = anchorMenu(
        { left: pos.x, right: pos.x, top: pos.y, bottom: pos.y },
        cardMenuEl.value,
      )
    }
  })
}

function closeCardMenu() {
  cardMenu.value = null
}

/** 菜单里地点所在的天：卡片可以在非选中的天上，起点/终点只能落在它自己那一天。 */
const menuDay = computed(() => {
  const menu = cardMenu.value
  if (!menu) return null
  return store.days.find((d) => d.id === menu.place.day_id) ?? null
})

/** 卡片菜单里那张卡在本天序列里的位次（按 id 比，不按对象——菜单存的是打开那一刻的引用）。 */
const cardMenuOrder = computed(() => {
  const menu = cardMenu.value
  if (!menu) return { index: -1, count: 0 }
  const ordered = store.places
    .filter((p) => p.day_id === menu.place.day_id)
    .slice()
    .sort((a, b) => a.sort_index - b.sort_index)
  return { index: ordered.findIndex((p) => p.id === menu.place.id), count: ordered.length }
})

/** 卡片菜单里的「移到其他天」候选（地点当前所在的天除外）。 */
const cardMenuOtherDays = computed(() => {
  const menu = cardMenu.value
  if (!menu) return []
  return store.days.filter((d) => d.id !== menu.place.day_id)
})

async function copyAddress(place: Place) {
  closeCardMenu()
  await copy(place.address || `${place.lng}, ${place.lat}`, {
    receipt: '地址已复制，可直接贴进地图或群聊',
    fallbackTitle: '地点地址',
  })
}

function closeCardMenuOnClick(e: MouseEvent) {
  if (!(e.target as HTMLElement).closest('.cardmenu')) closeCardMenu()
}

if (typeof window !== 'undefined') {
  window.addEventListener('click', closeCardMenuOnClick)
  window.addEventListener('resize', closeCardMenu)
}

// Dev-only handle for console assertions in the two-window verification drills
// (deep-compare both windows' day order after a concurrent-drag storm).
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__trip = store
  ;(window as unknown as Record<string, unknown>).__socket = socket
  ;(window as unknown as Record<string, unknown>).__client_id = selfId
}
</script>

<template>
  <div class="shell">
    <TripHeader
      :title="store.trip?.title ?? ''"
      :city="store.trip?.city ?? ''"
      :presence="store.presence"
      :self-id="selfId"
      :status="socket.status"
      :chat-unread="store.chatUnread"
      :hide-title="mastShown && !mastCollapsed"
      @share="copyShareLink"
      @rename="renameTrip(store.trip?.title ?? '')"
      @set-city="setTripCity"
      @cover="openCoverPicker"
      @copy-text="copyTextItinerary"
      @chat="showPane('chat')"
    />

    <!-- 海报头（D1）：名字的大字这一屏说了两遍，所以它开着的时候顶栏那行标题让位，
         重命名与改城市都由这里的标题和「更多」菜单接手。 -->
    <TripMasthead
      v-if="mastShown"
      ref="mastRef"
      :collapsed="mastCollapsed"
      :scroll-y="mastScrollY"
      @rename="renameTrip(store.trip?.title ?? '')"
      @pick-cover="openCoverPicker"
    />

    <AmapKeyCheck />

    <div v-if="loadFail" class="shell__loadfail">
      <div class="banner banner--danger">
        <div class="banner__body">
          <div class="banner__title">{{ loadFail.message }}</div>
          <div v-if="loadFail.hint" class="banner__hint tiny">{{ loadFail.hint }}</div>
          <div class="banner__hint tiny">
            检查链接是否完整，或回<a href="/">首页</a>重新打开。
            <button class="chip chip--action" type="button" @click="void retryLoad()">
              再试一次
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-else
      class="shell__body"
      :class="{
        'map-open': mobileView === 'map',
        'doc-open': docOpen,
        'sheet-lift': sheetOpen,
      }"
    >
      <div class="panel">
        <nav class="panebar" role="tablist" aria-label="行程内的页面">
          <button
            class="panebar__btn"
            :class="{ 'panebar__btn--on': pane === 'trip' }"
            type="button"
            role="tab"
            :aria-selected="pane === 'trip'"
            @click="pane = 'trip'"
          >
            <List class="ic" :size="14" /> 行程
            <span v-if="store.places.length" class="panebar__n tiny">
              {{ store.places.length }}
            </span>
          </button>
          <button
            class="panebar__btn"
            :class="{ 'panebar__btn--on': pane === 'checklist' }"
            type="button"
            role="tab"
            :aria-selected="pane === 'checklist'"
            @click="pane = 'checklist'"
          >
            <Package class="ic" :size="14" /> 出行清单
            <span v-if="store.checklist.length" class="panebar__n tiny">
              {{ store.checklistDoneCount }}/{{ store.checklist.length }}
            </span>
          </button>
          <button
            class="panebar__btn"
            :class="{ 'panebar__btn--on': pane === 'cost' }"
            type="button"
            role="tab"
            :aria-selected="pane === 'cost'"
            @click="pane = 'cost'"
          >
            <Receipt class="ic" :size="14" /> 费用
            <span v-if="store.expenses.length" class="panebar__n tiny">
              {{ formatMoney(store.spentCents) }}
            </span>
          </button>
          <button
            class="panebar__btn"
            :class="{ 'panebar__btn--on': pane === 'chat' }"
            type="button"
            role="tab"
            :aria-selected="pane === 'chat'"
            @click="pane = 'chat'"
          >
            <MessageSquare class="ic" :size="14" /> 聊天
            <!-- 未读是「有事没看」，不是「这里有几条」：实心强调底，和上面三枚淡底数字分开
                 两套语义。清单那枚写的是 1/6，费用写的是 ¥172.84。 -->
            <span v-if="store.chatUnread" class="panebar__badge tiny mono">{{ chatBadge }}</span>
          </button>
        </nav>
        <OpTicker />
        <div
          class="panel__scroll panel__content"
          :class="{ 'panel__content--fill': pane === 'chat' && !narrow }"
          :style="{ '--tail-h': `${mastPad}px` }"
          @scroll.passive="onPanelScroll"
        >
          <div v-if="store.loading" class="skeletongroup" aria-label="正在加载行程">
            <div class="skeleton" style="height: 30px" />
            <div class="skeleton" style="height: 46px" />
            <div class="skeleton" style="height: 74px" />
            <div class="skeleton" style="height: 74px; width: 85%" />
            <div class="skeleton" style="height: 74px; width: 70%" />
          </div>

          <template v-else-if="store.trip">
            <!-- 一个描边块：搜索框与「发现」开关并排一行，发现就地往下展开。
                 v-show 不用 v-if：发现面板的缓存按 key 存在组件里，卸载一次就白烧一次配额。 -->
            <div v-show="pane === 'trip'" class="addbar card" :class="{ 'addbar--flash': flash }">
              <div class="addbar__row">
                <PlaceSearch
                  class="addbar__search"
                  bare
                  ref="searchEl"
                  :city="store.trip.city"
                  @select="onPoiPicked"
                  @stash="onStash"
                />
                <span class="addbar__div" aria-hidden="true" />
                <button
                  class="addbar__reco"
                  type="button"
                  :aria-expanded="recoOpen"
                  :title="cityText ? `${cityText}的景点、美食与夜市` : '推荐按城市给出，展开后可设置目的地城市'"
                  @click="recoEl?.toggle()"
                >
                  <Compass class="ic" :size="14" /> 发现
                  <ChevronDown class="addbar__caret" :class="{ 'addbar__caret--on': recoOpen }" :size="14" />
                </button>
              </div>

              <RecommendPanel
                ref="recoEl"
                :city="store.trip.city"
                :trip-id="tripId"
                @update:open="recoOpen = $event"
                @set-city="setTripCity"
              />
            </div>

            <StashPanel v-show="pane === 'trip'" />

            <div v-show="pane === 'trip'" class="daylist">
              <DaySection
                v-for="(day, i) in store.days"
                :key="day.id"
                :day="day"
                :expanded="expandedIds.has(day.id)"
                class="reveal"
                :style="{ '--i': i > 4 ? 4 : i, '--base': '140ms' }"
                @select="selectDay(day.id)"
                @toggle="toggleDay(day.id)"
                @rename="renameDay(day.id)"
                @remove="removeDay(day.id)"
                @menu="openCardMenu"
                @add-here="askAddHere(day.id)"
              />
              <!-- 添加一天留在末尾，但改成通栏虚线行：它不是又一张卡，是这一列的收口。 -->
              <button class="addday" type="button" @click="store.addDay()">
                <Plus class="ic" :size="14" /> 添加一天
              </button>
            </div>

            <section v-if="pane === 'checklist'" class="page">
              <p class="page__lead tiny muted">清单由同行人共同维护，打勾状态实时同步。</p>
              <ChecklistPanel />
            </section>

            <section v-else-if="pane === 'cost'" class="page">
              <p class="page__lead tiny muted">记录每笔开销，按人分摊并给出结算建议。</p>
              <ExpensePanel />
            </section>

            <!-- 聊天：宽屏这一份是第四页签，窄屏交给顶栏气泡开出的抽屉（同一组件两份宿主）。
                 v-show 不用 v-if：切走页签时那句没发完的话不该蒸发。 -->
            <section v-if="!narrow" v-show="pane === 'chat'" class="page page--chat">
              <MessagePanel :active="pane === 'chat'" @open-ref="openChatRef" />
            </section>
          </template>
        </div>
      </div>
      <div class="map-host">
        <MapPanel @pick="onMapPick" />
      </div>
    </div>

    <!-- 窄屏：页签与视图切换合成一条 dock。上面一条 .panebar、下面一条两入口的 dock，
         等于把「这是哪一页」在屏幕两头各说一遍。 -->
    <nav class="mobile-switch" aria-label="切换页面">
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': pane === 'trip' && mobileView === 'list' }"
        type="button"
        @click="showMobileView('list')"
      >
        <List class="ic" :size="15" /> 行程
      </button>
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': pane === 'trip' && mobileView === 'map' }"
        type="button"
        @click="showMobileView('map')"
      >
        <MapIcon class="ic" :size="15" /> 地图
      </button>
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': pane === 'checklist' }"
        type="button"
        @click="showPane('checklist')"
      >
        <Package class="ic" :size="15" /> 清单
      </button>
      <button
        class="mobile-switch__btn"
        :class="{ 'mobile-switch__btn--on': pane === 'cost' }"
        type="button"
        @click="showPane('cost')"
      >
        <Receipt class="ic" :size="15" /> 费用
      </button>
    </nav>

    <JoinGate v-if="!joined" @join="onJoin" />

    <!-- 窄屏的聊天：抽屉不抬地图（这一面要看的是话，不是这一站在哪儿），也不进底部那条
         dock——dock 四个按钮已经贴住拇指能到的边界，第五个会把前四个都挪位。 -->
    <AppModal
      v-if="chatSheetShown && narrow"
      ref="chatSheetEl"
      title="聊天"
      sub="同行的人都会看到这句话"
      variant="sheet"
      @close="onChatSheetClosed"
    >
      <div class="chatsheet">
        <MessagePanel active @open-ref="openChatRef" />
      </div>
    </AppModal>

    <!-- 封面（M36 / M37）：这一处宿主只负责开合与写库，挑图与预缩都在内容件里。
         行程图片与城市图片两档在 M37 退出海报头的自动链，只留在这里当手动候选。 -->
    <AppModal
      v-if="coverOpen"
      :autofocus="false"
      title="行程封面"
      sub="封面属于行程本身，改动会同步给同行人。不选的话用内置的默认封面。"
      @close="coverOpen = false"
    >
      <CoverPicker
        :current="customCover"
        :shown="store.coverPhoto"
        :source="store.coverSource"
        :place-photos="store.placeCovers"
        :city-photos="cityCoverPhotos"
        :city="store.trip?.city ?? ''"
        @pick="onCoverPick"
        @file="onCoverFile"
        @auto="onCoverAuto"
      />
    </AppModal>

    <template v-if="joined">
      <!-- 收起来的是那个会动的角色，不是助手：面板照旧挂着，入口在顶栏「更多」里（TripHeader 按同一条偏好补）。 -->
      <Sprite v-if="settings.prefs.pet_visible" />
      <AssistantPanel />
    </template>

    <div
      v-if="cardMenu"
      ref="cardMenuEl"
      class="cardmenu card"
      :style="{ left: `${cardMenuPos.left}px`, top: `${cardMenuPos.top}px` }"
    >
      <button class="cardmenu__item" type="button" @click="renamePlace(cardMenu.place)">
        <Pencil class="ic" :size="13" /> 重命名
      </button>
      <button
        class="cardmenu__item"
        type="button"
        title="就这一站说一句，同伴的屏幕上会同时出现"
        @click="store.askAboutPlace(cardMenu.place.id); closeCardMenu()"
      >
        <MessageSquare class="ic" :size="13" /> 说一句
      </button>
      <button
        v-if="cardMenu.place.address"
        class="cardmenu__item"
        type="button"
        @click="copyAddress(cardMenu.place)"
      >
        复制地址
      </button>
      <a
        class="cardmenu__item"
        target="_blank"
        rel="noopener"
        :href="`https://uri.amap.com/marker?position=${cardMenu.place.lng},${cardMenu.place.lat}&name=${encodeURIComponent(cardMenu.place.name)}`"
        @click="closeCardMenu"
      >
        <ExternalLink class="ic" :size="13" /> 在高德中查看
      </a>
      <button
        class="cardmenu__item"
        type="button"
        :title="
          cardMenu.place.locked
            ? '取消后这一站重新参与一键优化；手填的时刻也会一并取消'
            : '不参与后，一键优化会把它留在原位，只重排它前后的站'
        "
        @click="store.setPlaceLocked(cardMenu.place.id, !cardMenu.place.locked); closeCardMenu()"
      >
        <LockOpen v-if="cardMenu.place.locked" class="ic" :size="13" />
        <Lock v-else class="ic" :size="13" />
        {{ cardMenu.place.locked ? '恢复参与优化' : '不参与优化' }}
      </button>
      <button
        v-if="menuDay && menuDay.id && menuDay.start_place_id !== cardMenu.place.id"
        class="cardmenu__item"
        type="button"
        @click="store.setStartPlace(menuDay.id, cardMenu.place.id); closeCardMenu()"
      >
        <Flag class="ic" :size="13" /> 设为起点
      </button>
      <button
        v-if="menuDay && menuDay.id && menuDay.end_place_id !== cardMenu.place.id"
        class="cardmenu__item"
        type="button"
        @click="store.setEndPlace(menuDay.id, cardMenu.place.id); closeCardMenu()"
      >
        <BedDouble class="ic" :size="13" /> 设为终点
      </button>
      <button
        v-if="cardMenuOrder.index > 0"
        class="cardmenu__item"
        type="button"
        @click="store.nudgePlace(cardMenu.place.day_id, cardMenu.place.id, -1); closeCardMenu()"
      >
        <ArrowUp class="ic" :size="13" /> 上移一位
      </button>
      <button
        v-if="cardMenuOrder.index >= 0 && cardMenuOrder.index < cardMenuOrder.count - 1"
        class="cardmenu__item"
        type="button"
        @click="store.nudgePlace(cardMenu.place.day_id, cardMenu.place.id, 1); closeCardMenu()"
      >
        <ArrowDown class="ic" :size="13" /> 下移一位
      </button>
      <button
        v-for="d in cardMenuOtherDays"
        :key="d.id"
        class="cardmenu__item"
        type="button"
        @click="store.movePlaceToDay(cardMenu.place.id, d.id); closeCardMenu()"
      >
        <ArrowRight class="ic" :size="13" /> 移到第 {{ d.day_index + 1 }} 天{{
          d.title ? ` · ${d.title}` : ''
        }}
      </button>
    </div>

    <div v-if="mapPick" class="mappick card" role="dialog" aria-label="将地图所选位置加入行程">
      <strong>{{ mapPick.loading ? '正在读取这个位置的名称…' : '把这个位置加入行程？' }}</strong>
      <input
        v-model="mapPick.name"
        class="mappick__name"
        placeholder="地点名称"
        maxlength="120"
        @keyup.enter="confirmMapPick"
      />
      <p v-if="mapPick.address" class="tiny muted mappick__addr">{{ mapPick.address }}</p>
      <div class="mappick__actions">
        <button
          class="btn btn--sm"
          type="button"
          :disabled="!mapPick.name.trim()"
          @click="confirmMapPick"
        >
          <ArrowRight class="ic" :size="13" /> 加入{{ mapPickDayLabel }}
        </button>
        <button
          class="btn btn--sm btn--ghost"
          type="button"
          :disabled="!mapPick.name.trim()"
          @click="stashMapPick"
        >
          加入想去
        </button>
        <button class="btn btn--sm btn--ghost" type="button" @click="mapPick = null">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cardmenu {
  position: fixed;
  z-index: var(--z-bar);
  display: flex;
  flex-direction: column;
  min-width: 170px;
  /* anchorMenu 只能夹 left；菜单自身比视口还宽时右缘照样出界，宽度必须自己封顶。 */
  max-width: min(320px, calc(100vw - 16px));
  padding: 4px;
  box-shadow: var(--shadow-lg);
  animation: cardmenu-in var(--dur-slow) var(--ease-pop);
}

.cardmenu__item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 34px;
  padding: 8px 10px;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text);
  text-align: left;
  text-decoration: none;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.cardmenu__item:hover {
  background: var(--surface-2);
}

.mappick {
  position: fixed;
  z-index: var(--z-overlay);
  left: 50%;
  bottom: 88px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(320px, calc(100vw - 32px));
  padding: 14px 16px;
  transform: translateX(-50%);
  box-shadow: var(--shadow-pop);
  animation: mappick-in var(--dur-slow) var(--ease-pop);
}

.mappick__name {
  padding: 8px 10px;
  font-size: calc(14px * var(--fs-scale));
  background: var(--surface-2);
  border: 1px solid var(--ink);
  border-radius: var(--radius-sm);
}

.mappick__addr {
  margin: 0;
}

.mappick__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

@keyframes cardmenu-in {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
}

/* 只有 from 帧：动画结束后回到元素自身的样式，所以 from 里必须重复 translateX(-50%)，
   否则弹层会在弹出的瞬间横跳一次。 */
@keyframes mappick-in {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(10px) scale(0.97);
  }
}

/* 这个容器既是定高的滚动区、又是列向 flex——两条身份凑在一起时，flex 的默认
   `flex-shrink: 1` 会先动手把子项压扁，压根不滚。被压的永远是唯一能缩到 0 的那条
   （发现面板带内层滚动，min-content 是 0，实测高 2px）。所以每条子项都钉住自然高度，
   容器只负责滚动。 */
.panel__content {
  display: flex;
  flex-direction: column;
  gap: 10px;
  /* 实测 padding 为 0 时卡片顶到面板边线，右边那条 1px 墨线被 border-right 切掉一半。
     描边风最怕边线糊在一起：留一圈，让每张卡的轮廓是完整的一条。 */
  padding: 10px 10px 0;
}

/* 收尾这一圈不敢写回容器自己的 padding-bottom：sticky 钉的是「滚动视口扣掉 padding」
   那一条线，写在容器上就等于把地点编辑器的吸底栏永久抬离面板下缘（实测差 20px）。
   垫在最后一项之后，吸底栏才真的贴住边线。连同 10px 的 gap，仍是 20px 的收口气。
   `--tail-h` 是海报头折叠让出的那一截（见 onPanelScroll）：接在末尾而不是凭空多一个元素，
   就是为了不往里层插第二道 gap。过渡必须与 `.mast` 的 height 同一条曲线——两边一帧都
   得互补，中途只要「视口抬得比补白快」，可滚范围就会短暂变小，读数照样被夹一次。 */
.panel__content::after {
  flex: 0 0 auto;
  height: calc(10px + var(--tail-h, 0px));
  content: "";
  transition: height var(--dur-slow) var(--ease-inout);
}

.panel__content > * {
  flex: 0 0 auto;
}

/* 聊天要的是「铺满这一栏、自己滚」：外层再滚一次就会出现两个滚动条，而输入框会被推出
   视口——一句要回的话得先滚到页尾，那就不叫聊天了。 */
.panel__content--fill {
  overflow: hidden;
}

/* 只在它真的被看着那一面时抬起来：这一节平时只是 `v-show` 藏着的（为的是切页签不丢草稿），
   要是把 `flex: 1 1 auto` 常驻在它身上，就等于给整栏的 shrink-0 不变量留了个后门。 */
.panel__content--fill > .page--chat {
  flex: 1 1 auto;
  min-height: 0;
  padding-bottom: 4px;
}

/* 窄屏抽屉里的那一份：外层 `.modal__body` 自己会滚，所以这里给一个定高，让列表在里面滚。 */
.chatsheet {
  display: flex;
  height: min(58vh, 520px);
  /* 键盘收起/地址栏伸缩时 vh 是「大视口」，抽屉会把输入框顶到看不见的地方。 */
  height: min(58dvh, 520px);
  padding: 0 14px 14px;
}

/* ---------- 窄屏：地点抽屉与地图抬起（见上面 sheetOpen） ---------- */

/* 位移写在 .map-host 上，不去动 AMap 的投影：panTo 之后这一站就在容器正中，容器整体上
   移半个抽屉，落点自然停在抽屉上方那条可见带的中间。抽屉开着的时候点击全被它的遮罩接住，
   所以这段位移不会让地图的命中测试错位；关掉抽屉，地图自己落回去。 */
.shell__body.sheet-lift .map-host {
  transform: translateY(calc(var(--place-sheet-h) * -0.5));
}

/* 抬起的部分总得有个边界：不裁的话地图会盖到顶栏上去。用 clip 不用 hidden——
   hidden 会在这里开出一个滚动容器，编辑器的吸底栏就不再钉在 `.panel__scroll` 上，
   那一格的出口又会跟着滚走（见 PlaceEditor 里 `.edit__foot` 的前提）。 */
.shell__body {
  overflow: clip;
}

@media (max-width: 860px) {
  .map-host {
    transition: transform var(--dur-slow) var(--ease-out);
  }

  /* 选点卡浮在 dock 之上：88px 是「dock 65 + 23」的一次性手算，
     没算 Home 条安全区，也没跟 --dock-h 的字号上限联动。统一读令牌。 */
  .mappick {
    bottom: calc(var(--dock-total-h) + 16px);
  }
}

/* ---------- 分栏（M24b）：行程 / 出行清单 / 费用 ---------- */

.panebar {
  display: flex;
  flex: 0 0 auto;
  gap: 3px;
  justify-content: center;
  padding: 6px 8px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}

.panebar__btn {
  display: inline-flex;
  flex: 1 1 auto;
  gap: 6px;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: 220px;
  padding: 7px 8px;
  color: var(--text-2);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.panebar__btn:hover:not(.panebar__btn--on) {
  color: var(--text);
  background: var(--surface-3);
}

/* 选中态沿用分段控件那套（白底 + 强调描边 + accent-strong 文字）：实心强调底会把一条
   导航栏变成一个大按钮，而 accent-strong 的对比度在别处已经验过。 */
.panebar__btn--on {
  color: var(--accent-strong);
  background: var(--surface);
  border-color: var(--accent);
  box-shadow: var(--shadow-sm);
}

.panebar__n {
  flex: 0 0 auto;
  padding: 1px 6px;
  color: var(--text-2);
  background: var(--surface-3);
  border-radius: var(--radius-pill);
}

.panebar__btn--on .panebar__n {
  color: var(--accent-strong);
  background: var(--accent-soft);
}

/* 未读徽标：强调色实心 + 反白字。三枚「量」徽标（10 / 1/6 / ¥172.84）都是淡底数字，
   同一排里再放一枚淡底数字就分不出「有事没看」和「这里有多少」。 */
.panebar__badge {
  flex: 0 0 auto;
  min-width: 18px;
  padding: 1px 5px;
  color: var(--accent-ink);
  text-align: center;
  background: var(--accent);
  border-radius: var(--radius-pill);
}

/* 窄屏这四档各归各位：行程/地图与清单/费用在底部 dock，聊天在顶栏气泡。这里整条藏掉，
   否则同一件事会在屏幕两头各说一遍。 */
@media (max-width: 860px) {
  .panebar {
    display: none;
  }
}

.page {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  /* 左右交给 `.panel__content` 那一圈，这里只管上下。 */
  padding: 6px 0 24px;
}

/* 文档页铺页面底色：面板卡靠底色差浮出来，而不是贴边铺满。
   与 body 共用同一张顶光屏底，这两页才不像另一个系统的附表。 */
.shell__body.doc-open .panel {
  background: var(--page-art);
}

.page__lead {
  margin: 0;
}

/* 文档页要的是行宽不是地图：宽屏下让地图退场、内容区放宽。窄屏本来就是单栏，
   切换交给底部 dock。 */
@media (min-width: 861px) {
  .shell__body.doc-open .panel {
    flex: 1 1 auto;
    min-width: 0;
  }
  .shell__body.doc-open .map-host {
    display: none;
  }
}

.daylist {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 添加一天：通栏虚线一行，读作「这一列的收口」，不是又一张卡。 */
.addday {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  min-height: 38px;
  padding: 8px;
  color: var(--text-2);
  background: none;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  transition:
    color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.addday:hover {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: var(--accent);
}

.skeletongroup {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* ---------- 添加地点：搜索框与「发现」共用一个描边 ---------- */

.addbar {
  border: 1px solid var(--ink);
  border-radius: var(--radius);
  transition: border-color var(--dur) var(--ease-out);
}

.addbar:focus-within,
.addbar--flash {
  border-color: var(--accent);
}

/* 空天那一行点进来时的回声：整条栏亮一下。动画被 reduced-motion 掐掉时停在自然态
   （不亮），不影响任何人点到搜索框。 */
.addbar--flash {
  animation: addbar-flash 900ms var(--ease-out);
}

@keyframes addbar-flash {
  from {
    box-shadow: 0 0 0 4px var(--accent-soft);
  }
}

.addbar__row {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 44px;
  padding: 0 10px;
}

.addbar__search {
  flex: 1 1 auto;
  min-width: 0;
}

.addbar__div {
  flex: 0 0 auto;
  width: 1px;
  height: 20px;
  background: var(--border);
}

.addbar__reco {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 5px;
  align-items: center;
  min-height: 28px;
  padding: 4px 6px;
  color: var(--text-2);
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: pointer;
  transition:
    color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.addbar__reco:hover {
  color: var(--accent-strong);
  background: var(--accent-soft);
}

.addbar__caret {
  color: var(--text-3);
  transition: transform var(--dur) var(--ease-inout);
}
.addbar__caret--on {
  transform: rotate(180deg);
}
</style>
