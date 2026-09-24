<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import AppModal from '@/components/AppModal.vue'
import PlaceEditor from '@/components/PlaceEditor.vue'
import { Ellipsis, GripVertical, Lock, MapPin, Trash2, X } from '@/components/icons'
import { useNarrowView } from '@/composables/useNarrowView'
import type { RailMark } from '@/components/TimeRail.vue'
import type { Place } from '@/types/domain'
import { formatDuration, formatMin } from '@/utils/time'

const props = defineProps<{
  place: Place
  index: number
  active?: boolean
  /** 别人正停在这一站上——给它描一圈他的颜色。 */
  editingBy?: { name: string; color: string } | null
  /** 高德 URI API 到这一站的导航链接（从上一站过来）。 */
  navHref?: string | null
  /** 添加这一站的人的颜色；空＝主题色。 */
  creatorColor?: string
  /** 同一天其它站的已钉时刻，画到时刻轨道上当参照点。 */
  marks?: RailMark[]
}>()

const emit = defineEmits<{
  select: [place: Place]
  close: [place: Place]
  remove: [place: Place]
  lock: [place: Place, locked: boolean]
  patch: [
    place: Place,
    patch: { name?: string; duration_min?: number; note?: string; start_min?: number | null },
  ]
  menu: [place: Place, pos: { x: number; y: number }]
}>()

/**
 * 编辑器只有一份本体（PlaceEditor），宿主按宽度换：宽屏内联在卡片下面，窄屏装进底部抽屉。
 * 手机上一条几百 px 高的内联面板会把列表撑成一堵墙，而抽屉背后还看得见地图。
 */
const narrow = useNarrowView()
const inlineOpen = computed(() => !!props.active && !narrow.value)
const sheetOpen = computed(() => !!props.active && narrow.value)

// 长按（触屏）打开卡片菜单：500ms 按住不动即视为呼出，移动/抬起取消。
let pressTimer: ReturnType<typeof setTimeout> | null = null
function pressStart(e: PointerEvent) {
  if (e.pointerType === 'mouse') return
  const place = props.place
  const x = e.clientX
  const y = e.clientY
  pressTimer = setTimeout(() => emit('menu', place, { x, y }), 500)
}
function pressCancel() {
  if (pressTimer) {
    clearTimeout(pressTimer)
    pressTimer = null
  }
}

/**
 * 就地改名：展开态下那一行地名本身就是输入框。「名字」是这一站最常被改的字段，
 * 藏进菜单或抽屉里就得先知道它居然能改；写在原地没有这个门槛。
 * 失焦提交、Esc 还原，与备注同一套规矩。
 */
const nameDraft = ref(props.place.name)
const nameTyping = ref(false)

watch(
  () => props.active,
  (active) => {
    if (active) nameDraft.value = props.place.name
  },
)
// 正在打字时不许被别人拽走；没在打字就跟着行数据走（菜单里改名要立刻看得见）。
watch(
  () => props.place.name,
  (v) => {
    if (!nameTyping.value) nameDraft.value = v
  },
)

function commitName() {
  nameTyping.value = false
  const next = nameDraft.value.trim()
  if (next && next !== props.place.name) emit('patch', props.place, { name: next })
  else nameDraft.value = props.place.name
}

function cancelName(e: KeyboardEvent) {
  nameDraft.value = props.place.name
  ;(e.target as HTMLElement).blur()
}

const ringStyle = computed(() =>
  props.editingBy
    ? { borderColor: props.editingBy.color, boxShadow: `0 0 0 3px ${props.editingBy.color}33` }
    : undefined,
)

/** 右侧时刻列。start_min 是服务端排程结果：没排过时只能给一个占位破折号。 */
const clock = computed(() =>
  props.place.start_min === null ? '—' : formatMin(props.place.start_min),
)
const stayText = computed(() => formatDuration(props.place.duration_min))

// Esc 是内联展开态的退出键——监听只在这一张卡展开期间挂着，同时最多一张，不会互相抢。
// 抽屉那一路交给 AppModal：它自己管 Esc、焦点与退场。
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close', props.place)
}

let escOn = false
function esc(on: boolean) {
  if (on === escOn) return
  escOn = on
  if (on) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
}

watch(inlineOpen, (on) => esc(on), { immediate: true })
onBeforeUnmount(() => esc(false))

/** 键盘选中：Enter/空格与点击同义。只认落在卡片本身上的按键，编辑器的键不归这里管。 */
function onCardKeydown(e: KeyboardEvent) {
  if (e.target !== e.currentTarget) return
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    emit('select', props.place)
  }
}
</script>

<template>
  <li
    class="card place"
    :class="{ 'place--active': active }"
    :style="ringStyle"
    :data-place-id="place.id"
    tabindex="0"
    :aria-label="`第 ${index + 1} 站：${place.name}`"
    @click="emit('select', place)"
    @keydown="onCardKeydown"
    @contextmenu.prevent="emit('menu', place, { x: $event.clientX, y: $event.clientY })"
    @pointerdown="pressStart"
    @pointerup="pressCancel"
    @pointermove="pressCancel"
    @pointercancel="pressCancel"
  >
    <!-- 整行都是把手：以前只有那一枚 grip 能拖，鼠标要瞄 14px 宽的窄条。触屏反过来——
         行上没有 touch-action:none，手指一划仍然是滚动，能拖的仍然只有那一枚 grip。 -->
    <div class="place__row">
      <span class="place__drag" title="拖动排序" aria-hidden="true">
        <GripVertical :size="14" />
      </span>

      <span class="place__lead">
        <img
          v-if="place.photo_url"
          class="place__photo"
          :src="place.photo_url"
          :alt="`${place.name} 的照片`"
          loading="lazy"
          referrerpolicy="no-referrer"
        />
        <span v-else class="place__photo place__photo--empty" aria-hidden="true">
          <MapPin :size="14" />
        </span>
        <span class="place__order" :style="creatorColor ? { background: creatorColor } : undefined">{{
          index + 1
        }}</span>
      </span>

      <div class="place__body">
        <div class="place__title">
          <input
            v-if="inlineOpen"
            v-model="nameDraft"
            class="place__name place__name--edit"
            type="text"
            maxlength="60"
            aria-label="这一站的名字"
            @click.stop
            @dblclick.stop
            @pointerdown.stop
            @focus="nameTyping = true"
            @keydown.enter.prevent="commitName"
            @keydown.esc.stop.prevent="cancelName"
            @blur="commitName"
          />
          <span v-else class="place__name">{{ place.name }}</span>
          <span
            v-if="place.locked"
            class="place__pin tiny"
            :title="
              place.user_start_min === null
                ? '顺序：不参与优化——一键优化会把它留在原位'
                : `时刻：钉在 ${formatMin(place.user_start_min)}——顺序也跟着不参与优化`
            "
          >
            <Lock :size="11" />
          </span>
          <span v-if="place.status === 'confirmed'" class="place__confirmed tiny">已确认</span>
          <span v-if="editingBy" class="place__editor tiny" :style="{ color: editingBy.color }">
            {{ editingBy.name }} 在这张卡上
          </span>
        </div>

        <div v-if="place.address" class="place__address tiny">{{ place.address }}</div>

        <div v-if="place.note && !active" class="place__note tiny">{{ place.note }}</div>
      </div>

      <div class="place__time">
        <span class="place__clock">{{ clock }}</span>
        <span class="place__stay tiny">停留 {{ stayText }}</span>
      </div>

      <div class="place__side">
        <!-- 退出键长在删除旁边，且必须比删除更显眼：× 这个字形人人读作「关闭」，
             而它以前恰好是删除——展开详情后找不到出口、只能把地点删掉，就是这么来的。 -->
        <button
          v-if="active"
          class="btn btn--sm btn--ghost place__close"
          type="button"
          title="收起详情"
          aria-label="收起详情"
          @click.stop="emit('close', place)"
        >
          <X :size="14" />
        </button>
        <button
          class="btn btn--sm btn--ghost place__menu"
          type="button"
          title="更多操作"
          @click.stop="emit('menu', place, { x: $event.clientX, y: $event.clientY })"
        >
          <Ellipsis :size="14" />
        </button>
        <button
          class="btn btn--sm btn--ghost place__delete"
          type="button"
          title="删除这个地点"
          aria-label="删除这个地点"
          @click.stop="emit('remove', place)"
        >
          <Trash2 :size="14" />
        </button>
      </div>
    </div>

    <!-- 宽屏：编辑器就地展开在卡片下方，横跨整张卡片——侧栏只有 400px，挤在正文列里
         轨道和读数条都会放不下。LWW 意味着在这里改一个字段永远不会盖掉别人对同一张卡
         另一个字段的修改。 -->
    <div v-if="inlineOpen" class="place__edit" @click.stop>
      <PlaceEditor
        :place="place"
        :marks="marks"
        :nav-href="navHref"
        @patch="(p, patch) => emit('patch', p, patch)"
        @lock="(p, locked) => emit('lock', p, locked)"
        @close="emit('close', place)"
      />
    </div>
  </li>

  <!-- 窄屏：同一份编辑器装进底部抽屉，抽屉上面还留着地图与列表。高度交给 `--sheet-h`
       这一个真源——地图抬起的那半个抽屉量的是同一个数。 -->
  <AppModal
    v-if="sheetOpen"
    class="place-sheet"
    :style="{ '--sheet-h': 'var(--place-sheet-h)' }"
    variant="sheet"
    :title="place.name"
    :sub="`第 ${index + 1} 站 · 停留 ${stayText}`"
    @close="emit('close', place)"
  >
    <PlaceEditor
      :place="place"
      :marks="marks"
      :nav-href="navHref"
      with-name
      @patch="(p, patch) => emit('patch', p, patch)"
      @lock="(p, locked) => emit('lock', p, locked)"
      @close="emit('close', place)"
    />
  </AppModal>
</template>

<style scoped>
.place {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
  padding: 8px 10px;
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out),
    box-shadow var(--dur) var(--ease-out),
    transform var(--dur) var(--ease-out);
}

/* 正文行＝拖拽把手（`useDragSort` 的 handle 就指这里）。编辑面板故意不在这行里：
   否则按住备注框选两个字就把整张卡拖走了，时刻轨道也拖不动。 */
.place__row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-start;
}

/* 描边与阴影由宿主递变量：日色带（DaySection）把这一档换成票券——不描边、只留一道
   近影。选择器要多带一枚 `.card`：全局那条 `.panel .card` 也是两类的权重，
   只写 `.place` 会被它压掉，兜底跟着它降过的那一档，别的宿主看到的还是原来那层影。 */
.place.card {
  border-color: var(--place-edge, var(--ink));
  box-shadow: var(--place-shadow, var(--shadow-sm));
}

/* 站点：一枚落在轨道中线上的实心圆（--dc 由这一天继承下来，所以颜色本身就是「第几天」）。
   白环负责把它从虚线上摘出来，外圈 --ink 保证深色态下仍认得出边界。
   挂在卡片上而不是列表上：重排与 hover 位移时节点要跟着这一站走。 */
.place::before {
  position: absolute;
  top: 15px;
  left: -17px;
  width: 10px;
  height: 10px;
  content: "";
  background: var(--dc, var(--accent));
  /* 环色跟宿主：白环在卡片上是干净的，落在日色带上就是一个白洞，所以由宿主递过来。 */
  border: 2px solid var(--place-ring, var(--surface));
  border-radius: 50%;
  box-shadow: 0 0 0 1px var(--ink);
  transition: transform var(--dur) var(--ease-pop);
}

.place--active::before {
  transform: scale(1.25);
}

/* 浮起 1px + 阴影升一级：行卡是列表里唯一可点的对象，靠这点位移认领 hover。
   拖拽中的幽灵不跟浮，否则它会跟旁边的行错开半像素。 */
.place:hover:not(.place--ghost) {
  background: var(--surface-hover);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.place--ghost {
  opacity: 0.4;
}

.place--active {
  border-color: var(--accent);
  box-shadow: var(--shadow-sm);
}

/* 鼠标端整行都能拖了，这一枚仍是触屏上唯一的把手：touch-action:none 只写在把手上，
   手指划过卡片正文照常滚动列表，按住 grip 不动才算拖动。常亮是因为它的职责按设备而变，
   不亮就没人知道触屏该按哪里。 */
.place__drag {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  align-self: stretch;
  padding: 0 2px;
  color: var(--text-2);
  cursor: grab;
  user-select: none;
  touch-action: none;
}

.place__drag:active {
  cursor: grabbing;
}

.place__lead {
  position: relative;
  flex: 0 0 auto;
}

.place__photo {
  display: block;
  width: 34px;
  height: 34px;
  background: var(--surface-2);
  object-fit: cover;
  border-radius: 50%;
}

.place__photo--empty {
  display: grid;
  place-items: center;
  color: var(--text-faint);
}

/* 序号＝地图标记上的同一个数字与同一个创建者颜色，读图与读列表因此对得上同一站。 */
.place__order {
  position: absolute;
  right: -5px;
  bottom: -2px;
  display: grid;
  place-items: center;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  font-size: calc(10px * var(--fs-scale));
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #fff;
  background: var(--accent);
  border: 1.5px solid var(--surface);
  border-radius: var(--radius-pill);
}

.place__body {
  /* basis 必须是 0：换行是在「收缩之前」按假想主轴尺寸定的，而标题那一列的自动 basis
     就把 357px 的卡片撑满了 —— 于是右侧的 ⋯/× 被整块挤到第二行，孤零零挂在正文下面。
     给 0 之后第一行永远装得下，这一列照样靠 grow 吃掉剩余宽度。 */
  flex: 1 1 0;
  min-width: 0;
}

.place__title {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 5px;
  align-items: baseline;
}

.place__name {
  /* 必须给个下限：overflow:hidden 把这枚 flex 项的自动最小尺寸压成 0，于是窄屏上
     旁边的「某某 在这张卡上」会把地名挤成一两个省略号，而不是把自己换到下一行去。 */
  min-width: 5em;
  overflow: hidden;
  font-size: calc(13px * var(--fs-scale));
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 就地改名那一版：长得跟它替换掉的文字一样，只在描边上表态。flex 基线给得比文字小，
   所以它和右边的锁标、确认标还能同处一行；装不下时它自己缩，标不会挤走名字。 */
.place__name--edit {
  flex: 1 1 8em;
  min-width: 8em;
  padding: 1px 4px;
  margin-left: -4px;
  font-family: inherit;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: text;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.place__name--edit:hover {
  border-color: var(--accent);
}

.place__name--edit:focus {
  background: var(--surface);
  border-color: var(--accent);
  outline: none;
}

.place__pin {
  display: inline-flex;
  color: var(--warn);
}

.place__editor {
  white-space: nowrap;
}

.place__address {
  margin-top: 1px;
  overflow: hidden;
  color: var(--text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.place__confirmed {
  color: var(--ok);
}

.place__note {
  padding: 4px 6px;
  margin-top: 5px;
  white-space: pre-wrap;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

.place__time {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 1px;
  align-items: flex-end;
  padding-top: 1px;
}

.place__clock {
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
}

.place__stay {
  color: var(--text-2);
  white-space: nowrap;
}

.place__side {
  display: flex;
  /* 竖着叠：这一行在拖把手、照片、时间列之后只剩 ~280px，横着排两颗按钮要吃掉 58px，
     地点名就会被挤到「成都大熊猫繁育研究…」。叠成竖列只占 26px，名字回到十一个字。 */
  flex-direction: column;
  flex: 0 0 auto;
  gap: 0;
  align-items: center;
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}

.place:hover .place__side,
.place:focus-within .place__side,
.place--active .place__side {
  opacity: 1;
}

/* 键盘落点：整卡可聚焦，聚焦时给一圈主色描边；否则「tab 到哪张卡」无从看出。 */
.place:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.place__close,
.place__menu,
.place__delete {
  padding: 2px 6px;
  font-size: calc(13px * var(--fs-scale));
}

/* 出口与删除挨着长，颜色必须分开：× 走主色，垃圾桶才走危险色。 */
.place__close:hover {
  color: var(--accent-strong);
  background: var(--accent-soft);
}

/* 这三颗挨着，用 .tap-pad 向外撑会互相盖住落点——直接加高更诚实。 */
@media (max-width: 860px) {
  .place {
    padding: 8px;
  }

  /* 390px 实测（_trip-layout-probe）：一行里 drag 28 + 照片 34 + 4×gap 32 +
     时间列 ~70 + 常显按钮 ~60 之后，正文只剩 64–90px——旧注释「正文列 218px 起」
     不成立，地名被省略号吃成两三个字。窄屏改为两行制：body 的 basis 直接占满
     第一行剩余（98 > drag+照片+gap 的 94，时间列与按钮必然折到第二行），
     地名拿到 ~214px；时间横排在左、操作在右。 */
  .place__row {
    row-gap: 2px;
    /* 长按行卡不该弹文字选择柄；就地改名的输入框在下面豁免回来。 */
    user-select: none;
  }

  .place__row input,
  .place__row textarea {
    -webkit-user-select: auto;
    user-select: auto;
  }

  .place__body {
    flex-basis: calc(100% - 98px);
  }

  .place__time {
    flex-direction: row;
    align-items: baseline;
    gap: 8px;
    margin-right: auto;
    padding-top: 0;
  }

  .place__close,
  .place__menu,
  .place__delete {
    min-height: 34px;
    padding: 2px 8px;
  }

  /* 第二行右端横排：竖叠是桌面上省宽度的方案，手机上宽度已经还给地名了。 */
  .place__side {
    flex-direction: row;
    gap: 2px;
    align-items: flex-start;
  }

  /* 把手在卡片最左边，左右没有别的落点，可以安心撑宽到可指按的尺寸。 */
  .place__drag {
    min-width: 28px;
    justify-content: center;
    padding: 0;
  }
}

/* ---------- 宽屏内联宿主 ---------- */

/* 进场、字段、吸底栏都在 PlaceEditor 里；这里只剩一道把正文与编辑器分开的虚线。
   注意别在这里再开 overflow——祖先一变成滚动容器，编辑器那条吸底栏就钉不住了。 */
.place__edit {
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}

/* 触屏没有 hover：把手与行内按钮常驻，否则无法拖动或删除；hover 的浮起位移作废，
   否则最后点过的那张卡一直「浮」着不回位。 */
@media (hover: none) {
  .place__drag,
  .place__side {
    opacity: 1;
  }
  .place:hover:not(.place--ghost) {
    transform: none;
  }
}

/* 抽屉正文与标题对齐：head 有 16px，body 没有，分区标题就会贴着屏幕边错开一截。
   只补左右——这一层就是滚动容器，写 padding-bottom 会把吸底栏抬离面板下缘。
   写 :global 是因为 AppModal 把 $attrs 落在内层面板上，带 scope 属性的选择器配不到它；
   作用域交给只有本抽屉会带的 `.place-sheet` 收住。 */
:global(.place-sheet .modal__body) {
  padding: 0 16px;
}
</style>
