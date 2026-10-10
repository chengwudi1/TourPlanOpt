<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { Presence } from '@/types/domain'
import type { SocketStatus } from '@/stores/socket'
import {
  ArrowLeft,
  Copy,
  Ellipsis,
  ImageDown,
  ImagePlus,
  Link2,
  MapPin,
  MessageSquare,
  Pencil,
  Settings,
  ShieldCheck,
  Sparkles,
  User,
} from '@/components/icons'
import { useAmapHealth } from '@/composables/useAmapHealth'
import { useAssistantStore } from '@/stores/assistant'
import { useAuthStore } from '@/stores/auth'
import { useFeedbackStore } from '@/stores/feedback'
import { useSettingsStore } from '@/stores/settings'
import { useTripStore } from '@/stores/trip'
import { anchorMenu, type MenuPosition } from '@/utils/anchorMenu'
import { shareTripCard } from '@/utils/shareCard'

const props = defineProps<{
  title: string
  city: string
  presence: Presence[]
  selfId: string
  status: SocketStatus
  /** 没看的那几句有几条，只决定徽标；入口本身常驻（见 chatBadge）。 */
  chatUnread?: number
  /** 海报头开着的时候这一行不再重复行程名：两块大字上下叠着，读起来像没排完版。 */
  hideTitle?: boolean
}>()

const emit = defineEmits<{
  share: []
  rename: []
  setCity: []
  cover: []
  copyText: []
  chat: []
  /** 按某个同伴的头像：跳到此刻他正看着的那一站。 */
  goto: [placeId: string]
}>()

const auth = useAuthStore()
const store = useTripStore()
const settings = useSettingsStore()
const assistant = useAssistantStore()
const router = useRouter()

/** 登录页是整页路由：带上 next，登录成功后回到当前这段行程。 */
const loginTo = computed(() => ({
  name: 'login' as const,
  query: store.trip ? { next: `/trip/${store.trip.id}` } : {},
}))
const feedback = useFeedbackStore()
const {
  health: amapHealth,
  expanded: amapExpanded,
  toggle: toggleHealthPanel,
} = useAmapHealth()

/**
 * 移动端这一行只有 390px 可用，而原来它要塞下：返回、行程名、城市、健康点、
 * 连接态、登录、每个头像、两个图标按钮——结果是行程名被挤到 51px（O2），
 * 而同屏那些次要控件拿到了完整空间。这里按「行程名优先」重排：
 * 手机上只留返回、行程名、在线人数、更多；其余进「更多」菜单。
 */

/** 返回：站内有上一页就回退（从「我的行程」点进来的常见路径）；
 * 直接打开分享链接进来的，回落到首页。 */
function goBack() {
  if (window.history.state?.back) router.back()
  else router.push('/')
}

const sharing = ref(false)

/** 生成分享图：以前失败是空 catch，点了按钮屏幕上一句都不说（O3）。 */
async function shareImage() {
  if (!store.trip || sharing.value) return
  sharing.value = true
  try {
    const result = await shareTripCard(store.trip, store.currentPlaces)
    feedback.show({
      message: result === 'shared' ? '已调起系统分享' : '分享图已导出为图片文件',
    })
  } catch (err) {
    // 用户在系统分享面板里取消，不是失败，不该拿红条喊人。
    if (err instanceof DOMException && err.name === 'AbortError') return
    feedback.show({
      kind: 'danger',
      message: '分享图生成失败',
      hint: '可以稍后重试，或改用复制协作链接',
    })
  } finally {
    sharing.value = false
  }
}

/** 顶栏那枚气泡是窄屏唯一的聊天入口：没有未读也常驻，藏起来等于叫人再不来找这个功能。 */
const chatBadge = computed(() =>
  (props.chatUnread ?? 0) > 9 ? '9+' : String(props.chatUnread ?? 0),
)

const initials = computed(() =>
  props.presence.map((p) => {
    const where = store.presenceWhere.get(p.client_id) ?? null
    const isSelf = p.client_id === props.selfId
    /** 「第 3 天 · 浅草寺」；只报了天没点站时它自己就是一句完整的话。 */
    const whereText = where ? [where.dayLabel, where.placeName].filter(Boolean).join(' · ') : ''
    return {
      client_id: p.client_id,
      name: p.name,
      color: p.color,
      letter: (p.name || '?').slice(0, 1).toUpperCase(),
      isSelf,
      placeId: where?.placeId ?? null,
      whereText,
      /** 悬停就说清「这个人此刻在哪」，不必先点一下试。没停在任何一站要如实说，
       *  留空会被读成「坏了」。 */
      tip: isSelf
        ? `${p.name}（你）`
        : `${p.name || '同伴'} · ${whereText || '未选中任何一站'}`,
    }
  }),
)

/**
 * 不含自己的那几位。头像按下去要跳得过去，是因为地图只画当前这一天的标记——一个停在
 * 第 5 天的同伴在地图上彻底隐身，顶栏这一行是唯一能找到他的地方。
 */
const others = computed(() => initials.value.filter((p) => !p.isSelf))

function jumpTo(person: { placeId: string | null }) {
  if (!person.placeId) return
  emit('goto', person.placeId)
}

/** 手机上「几个人在线」要说成人话：原来只有一个色点，连点都没有（O2）。 */
const presenceLabel = computed(() => {
  const n = props.presence.length
  if (!n) return '仅你在线'
  return n === 1 ? '1 人在线' : `${n} 人在线`
})
const presenceNames = computed(() => {
  const names = props.presence.map((p) => p.name || '同伴')
  return names.length ? names.join('、') : ''
})

const statusLabel = computed(() => {
  switch (props.status) {
    case 'online':
      return '已连接'
    case 'connecting':
      return '连接中…'
    case 'reconnecting':
      return '重连中…'
    default:
      return '未连接'
  }
})

const healthLabel = computed(() =>
  amapHealth.value === 'good'
    ? '正常'
    : amapHealth.value === 'bad'
      ? '有问题'
      : '检查中',
)
/** 顶栏那颗点：健康时不渲染横幅了，总得有个地方回答「到底正常不正常」。 */
const healthTitle = computed(() => `地图服务${healthLabel.value} · 点开看详情`)
const healthDot = computed(() =>
  amapHealth.value === 'good' ? 'dot--ok' : amapHealth.value === 'bad' ? 'dot--danger' : '',
)

/** 「现在这张是怎么来的」在菜单里就得读出来，否则改错了不知道改的是哪一层。
 *  措辞与封面面板同源：同一件事全站只有一个名字。 */
const coverLabel = computed(() =>
  store.coverSource === 'custom' ? '封面：自己选的' : '封面：默认',
)

/* ---------- 「更多」菜单：手机上把次要控件收进一处 ---------- */

const menuOpen = ref(false)
const menuPos = ref<MenuPosition>({ left: 0, top: 0 })
const moreBtn = ref<HTMLElement | null>(null)
const menuEl = ref<HTMLElement | null>(null)

async function toggleMenu() {
  menuOpen.value = !menuOpen.value
  if (!menuOpen.value) return
  await nextTick()
  // 先量菜单自己的尺寸再定位：写死偏移的做法在菜单变高之后就会露馅。
  if (moreBtn.value && menuEl.value) {
    menuPos.value = anchorMenu(moreBtn.value.getBoundingClientRect(), menuEl.value, 'end')
  }
}

function closeMenu() {
  menuOpen.value = false
}

/** 菜单项一律先关再做事：动作可能弹出对话框或触发下载，留着菜单会挡住视线。
 *
 *  只能直接做，不能 `return () => {...}`：模板里写的是 `@click="run(...)"`，Vue 会把这整段
 *  表达式包进事件处理器，返回值没人调用——那样八行菜单全都会静默失效，点了什么反应都没有。
 */
function run(fn: () => void) {
  closeMenu()
  fn()
}

function onDocClick(e: MouseEvent) {
  if (!menuOpen.value) return
  const el = e.target as HTMLElement | null
  if (el?.closest('.triphead__more') || el?.closest('.triphead__menu')) return
  closeMenu()
}

function onDocKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && menuOpen.value) closeMenu()
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onDocKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onDocKeydown)
})
</script>

<template>
  <header class="triphead">
    <button
      class="iconbtn triphead__back"
      type="button"
      title="返回"
      aria-label="返回首页"
      @click="goBack"
    >
      <ArrowLeft :size="16" />
    </button>

    <h1 class="triphead__name" :class="{ 'triphead__name--quiet': hideTitle }">
      <button
        class="triphead__title tap-pad"
        type="button"
        :title="title ? '点击重命名行程' : '点击设置行程名'"
        @click="emit('rename')"
      >
        <span class="triphead__text">{{ title || '未命名行程' }}</span>
        <!-- 重命名原来只靠 title 提示，界面上没有任何迹象（O10）。 -->
        <Pencil class="ic triphead__pencil" :size="13" aria-hidden="true" />
      </button>
      <button
        class="triphead__city tiny"
        type="button"
        :title="city ? '点击修改目的地城市' : '点击设置目的地城市：推荐与搜索范围依据该城市'"
        @click="emit('setCity')"
      >
        {{ city || '设城市' }}
      </button>
    </h1>

    <span class="triphead__spacer" />

    <span class="triphead__people tiny" :title="presenceNames">{{ presenceLabel }}</span>

    <button
      class="iconbtn triphead__health"
      :class="{ 'triphead__health--bad': amapHealth === 'bad' }"
      type="button"
      :title="healthTitle"
      :aria-label="healthTitle"
      :aria-expanded="amapExpanded"
      @click="toggleHealthPanel"
    >
      <span class="dot" :class="healthDot" aria-hidden="true" />
    </button>

    <span
      class="triphead__status tiny"
      :class="[`triphead__status--${status}`, { 'triphead__status--quiet': status === 'online' }]"
      :title="statusLabel"
    >
      <span class="dot dot--pulse" :class="`dot--${status === 'online' ? 'ok' : 'warn'}`" />
      <span v-if="status !== 'online'">{{ statusLabel }}</span>
    </span>

    <span v-if="auth.user" class="triphead__user tiny">
      {{ auth.user.name }}
      <button class="btn btn--sm btn--ghost" type="button" @click="auth.logout()">退出</button>
    </span>
    <RouterLink v-else class="triphead__login tiny" :to="loginTo">
      <User :size="13" /> 登录
    </RouterLink>

    <div v-if="initials.length" class="avatars" title="此刻在线">
      <button
        v-for="(p, i) in initials"
        :key="p.client_id"
        class="avatar"
        :class="{ 'avatar--jump': !p.isSelf && p.placeId }"
        :style="{
          background: p.color || 'var(--accent)',
          '--ring': p.color || 'var(--accent)',
          '--i': i,
        }"
        :title="p.tip"
        :aria-label="p.tip"
        :disabled="p.isSelf || !p.placeId"
        @click="jumpTo(p)"
      >
        {{ p.letter }}
      </button>
    </div>

    <button
      class="iconbtn"
      type="button"
      :disabled="sharing || !store.currentPlaces.length"
      :title="sharing ? '正在生成分享图…' : '生成分享图'"
      @click="shareImage"
    >
      <ImageDown :size="16" />
    </button>
    <button class="iconbtn" type="button" title="复制协作链接" @click="emit('share')">
      <Link2 :size="16" />
    </button>

    <button
      class="iconbtn triphead__chat"
      type="button"
      :title="chatUnread ? `聊天 · ${chatBadge} 条没看` : '聊天'"
      :aria-label="chatUnread ? `聊天，有 ${chatBadge} 条没看` : '聊天'"
      @click="emit('chat')"
    >
      <MessageSquare :size="16" />
      <span v-if="chatUnread" class="triphead__chat-n mono">{{ chatBadge }}</span>
    </button>

    <button
      ref="moreBtn"
      class="iconbtn triphead__more"
      type="button"
      title="更多操作"
      aria-label="更多操作"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      @click="toggleMenu"
    >
      <Ellipsis :size="16" />
    </button>

    <Teleport to="body">
      <div
        v-if="menuOpen"
        ref="menuEl"
        class="triphead__menu card"
        :style="{ left: `${menuPos.left}px`, top: `${menuPos.top}px` }"
        role="menu"
        aria-label="行程操作"
      >
        <p class="tripmenu__meta tiny muted">{{ presenceLabel }}</p>
        <button
          v-for="p in others"
          :key="p.client_id"
          class="tripmenu__item tripmenu__who"
          type="button"
          role="menuitem"
          :disabled="!p.placeId"
          @click="run(() => jumpTo(p))"
        >
          <span class="tripmenu__whodot" :style="{ background: p.color || 'var(--accent)' }" />
          <span class="tripmenu__whoname">{{ p.name || '同伴' }}</span>
          <span class="tripmenu__whowhere tiny muted">
            {{ p.whereText || '未选中任何一站' }}
          </span>
        </button>
        <p class="tripmenu__meta tiny muted">协作状态：{{ statusLabel }}</p>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('rename'))">
          <Pencil class="ic" :size="14" /> 重命名行程
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('setCity'))">
          <MapPin class="ic" :size="14" /> {{ city ? `目的地城市：${city}` : '设置目的地城市' }}
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('cover'))">
          <ImagePlus class="ic" :size="14" /> {{ coverLabel }}
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('share'))">
          <Link2 class="ic" :size="14" /> 复制协作链接
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('copyText'))">
          <Copy class="ic" :size="14" /> 复制文字版行程
        </button>
        <button
          class="tripmenu__item"
          type="button"
          role="menuitem"
          :disabled="sharing || !store.currentPlaces.length"
          @click="run(shareImage)"
        >
          <ImageDown class="ic" :size="14" /> {{ sharing ? '正在生成分享图…' : '生成分享图' }}
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(toggleHealthPanel)">
          <ShieldCheck class="ic" :size="14" /> 地图服务 · {{ healthLabel }}
        </button>
        <!-- 精灵收起来时，这里就是助手的唯一入口：少了右下角那个角标，待确认的条数改由这句文案报。 -->
        <button
          v-if="!settings.prefs.pet_visible"
          class="tripmenu__item"
          type="button"
          role="menuitem"
          @click="run(() => assistant.toggle(true))"
        >
          <Sparkles class="ic" :size="14" />
          {{ assistant.pendingCount ? `行程助手 · ${assistant.pendingCount} 项待确认` : '行程助手' }}
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => settings.show())">
          <Settings class="ic" :size="14" /> 设置
        </button>
        <RouterLink v-if="!auth.user" class="tripmenu__item" :to="loginTo" role="menuitem">
          <User class="ic" :size="14" /> 登录账号（可选）
        </RouterLink>
        <button v-else class="tripmenu__item" type="button" role="menuitem" @click="run(() => auth.logout())">
          <User class="ic" :size="14" /> 退出 {{ auth.user.name }}
        </button>
      </div>
    </Teleport>
  </header>
</template>

<style scoped>
.triphead {
  position: relative;
  display: flex;
  gap: 8px;
  align-items: center;
  /* 实占含刘海让位；内容行仍是 --header-h（padding-top 吃安全区）。 */
  flex: 0 0 var(--header-total-h);
  height: var(--header-total-h);
  padding: var(--sat) 12px 0;
  /* F 的 .tb：顶栏是一块压在照片上的玻璃。mock 那里 α 只有 .6，我们收在 --glass 的
     0.76——顶栏横跨全屏、上面全是小字，0.76 是「--text-2 压纯黑照片也过 4.5」
     反解出来的那一档。blur 属于手术清单（顶栏）。底边留墨发丝线而不是白线：
     白线在照片上勾不出「栏到哪里为止」——这一条要回答的是「内容从哪开始滚」。 */
  background: var(--glass);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--border);
}

/* 顶栏上唯一常驻的导航键借 F 的玻璃格：白线勾边的半透明块，与窄屏 dock 上的
   按钮同一套（--glass 的 72% 掺水）。实心奶白圆在玻璃栏上是一块不透明的补丁。 */
.triphead__back {
  background: color-mix(in srgb, var(--glass) 72%, transparent);
  border-color: var(--glass-border);
  border-radius: 50%;
}

.triphead__back:hover:not(:disabled) {
  background: color-mix(in srgb, var(--glass) 92%, transparent);
}

/* 行程名是这一屏的主角；品牌字留在首页，这里不占位。 */
.triphead__name {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
  font-size: calc(15px * var(--fs-scale));
  font-weight: 600;
  line-height: 1.3;
}

/* 海报头接管行程名的那一段时间：display: none 才是真的下屏——它同时把这一层从朗读
   顺序里摘掉，而屏幕上只留海报头那一枚 h1。重命名与改城市仍走「更多」菜单。 */
.triphead__name--quiet {
  display: none;
}

/* 标题本身是个按钮：可键盘聚焦、有可供性，不再只是「看起来像文字的地方」（O10）。 */
.triphead__title {
  display: inline-flex;
  gap: 6px;
  align-items: baseline;
  min-width: 0;
  padding: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}

.triphead__title:hover .triphead__text {
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 3px;
}

.triphead__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.triphead__pencil {
  flex: 0 0 auto;
  color: var(--text-2);
  opacity: 0.55;
}

.triphead__title:hover .triphead__pencil {
  opacity: 1;
}

.triphead__city {
  flex: 0 0 auto;
  /* 1px 白描边吃掉上下各 1px，内距跟着收一档，盒子尺寸与旧版一致。 */
  padding: 0 7px;
  color: var(--text-2);
  background: color-mix(in srgb, var(--glass) 80%, transparent);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

/* hover 收掉白边、翻成珊瑚浅底（mock 的 .pill.on 就是「on 时 border-color: transparent」）。 */
.triphead__city:hover {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: transparent;
}

.triphead__spacer {
  flex: 1 1 auto;
}

.triphead__people {
  flex: 0 0 auto;
  color: var(--text-2);
  white-space: nowrap;
}

.triphead__status {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  color: var(--text-2);
  white-space: nowrap;
}

.triphead__status--reconnecting {
  color: var(--warn);
}

.triphead__user {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--text-2);
  white-space: nowrap;
}

.triphead__login {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 4px 9px;
  color: var(--accent);
  text-decoration: none;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.triphead__login:hover {
  background: var(--accent-soft);
}

.avatars {
  display: flex;
}

.avatar {
  position: relative;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  font-family: inherit;
  font-size: calc(12px * var(--fs-scale));
  font-weight: 700;
  color: var(--warp-ink);
  appearance: none;
  /* 环色＝顶栏自己的玻璃：头像叠在玻璃上，环就得读同一个底（mock 的 .avs i 同理）。 */
  border: 2px solid var(--glass);
  border-radius: 50%;
}

/* 头像从纯展示变成可点：按下去跳到那个人正看着的那一站。自己那一位与没停在任何一站的
   同伴是禁用的，但禁用不许显灰——它仍然要读出「这个人在这儿」。 */
.avatar:disabled {
  cursor: default;
  opacity: 1;
}

.avatar--jump {
  cursor: pointer;
  transition: transform var(--dur-fast) var(--ease-pop);
}

.avatar--jump:hover {
  transform: translateY(-1px) scale(1.1);
}

/* 头像外圈＝「这个人此刻在页面里」。全局的 pulse-ring 放大到 2.6 倍，在这里是 68px 的一圈
   噪声（顶栏只有 48px 高），所以本地另起一个更收敛的缩放。按 --i 错峰，一屋子人看着像
   依次呼吸，不是所有人同时闪一下。 */
.avatar::before {
  position: absolute;
  inset: -1px;
  content: "";
  border: 2px solid var(--ring, var(--accent));
  border-radius: 50%;
  animation: avatar-ring 2.8s var(--ease-out) infinite;
  animation-delay: calc(var(--i, 0) * 420ms);
}

@keyframes avatar-ring {
  0% {
    opacity: 0.5;
    transform: scale(1);
  }
  45%,
  100% {
    opacity: 0;
    transform: scale(1.5);
  }
}

.avatar + .avatar {
  margin-left: -8px;
}

/* ---------- 「更多」菜单 ---------- */

/* 窄屏的聊天入口。宽屏不画它：第四页签就在正下方，同一件事不需要在屏幕两头各说一遍。 */
.triphead__chat {
  position: relative;
}

.triphead__chat-n {
  position: absolute;
  top: -3px;
  right: -4px;
  min-width: 15px;
  padding: 0 3px;
  font-size: calc(10px * var(--fs-scale));
  line-height: calc(15px * var(--fs-scale));
  color: var(--accent-ink);
  text-align: center;
  background: var(--accent);
  border-radius: var(--radius-pill);
}

@media (min-width: 861px) {
  .triphead__chat {
    display: none;
  }
}

.triphead__menu {
  position: fixed;
  z-index: var(--z-bar);
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 208px;
  max-width: min(320px, calc(100vw - 16px));
  padding: 6px;
  /* 浮层菜单换成重玻璃（与卡片菜单、地图选点同一套）：它悬在内容上，底下一律有字在动，
     实色 --surface 会把它变成一块和玻璃系统无关的白板。 */
  background: var(--glass-dense);
  border: 1px solid var(--glass-border);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
}

.tripmenu__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  color: var(--text);
  font-size: calc(13px * var(--fs-scale));
  text-align: left;
  text-decoration: none;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.tripmenu__item:hover {
  background: color-mix(in srgb, var(--text) 8%, transparent);
}

.tripmenu__item:disabled {
  color: var(--text-2);
  opacity: 0.6;
  cursor: not-allowed;
}

.tripmenu__meta {
  margin: 4px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 在场这一段：一行一个人，右边写他此刻在哪一天哪一站。站名可以很长，所以位置那一列
   自己封顶截断，人名不许被它挤没——认不出是谁的一行没有意义。 */
.tripmenu__who {
  gap: 6px;
}

.tripmenu__whodot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.tripmenu__whoname {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tripmenu__whowhere {
  flex: none;
  max-width: 46%;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 手机：一行只留最该看的 ---------- */
@media (max-width: 860px) {
  .triphead {
    gap: 6px;
    padding: var(--sat) 8px 0;
  }

  .tripmenu__item {
    min-height: 40px;
    padding: 8px 10px;
  }

  /* 手机上行程名要拿到尽可能宽的余量：这些次要控件统一进「更多」。
     健康点单独放行——它由下面那条规则决定「只有出问题时才占位」。 */
  .triphead__city,
  .triphead__login,
  .triphead__user,
  .avatars,
  .triphead > .iconbtn:not(.triphead__back):not(.triphead__more):not(.triphead__health):not(
      .triphead__chat
    ) {
    display: none;
  }

  .triphead__pencil {
    display: none;
  }

  /* 健康点与连接态在手机上只在「需要被看到」时占位。 */
  .triphead__health:not(.triphead__health--bad) {
    display: none;
  }

  .triphead__status--quiet {
    display: none;
  }
}

/* 「更多」两档都在：复制文字版行程这类低频动作只住在这里，桌面端把它藏掉就等于没做。
   手机上「几个人在线」那句是头像的替代读数，宽屏有头像就不用它了。 */
@media (min-width: 861px) {
  .triphead__people {
    display: none;
  }
}
</style>
