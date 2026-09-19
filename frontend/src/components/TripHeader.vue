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
  Link2,
  MapPin,
  Pencil,
  ShieldCheck,
  User,
} from '@/components/icons'
import { useAmapHealth } from '@/composables/useAmapHealth'
import { useAuthStore } from '@/stores/auth'
import { useFeedbackStore } from '@/stores/feedback'
import { useTripStore } from '@/stores/trip'
import { anchorMenu, type MenuPosition } from '@/utils/anchorMenu'
import { shareTripCard } from '@/utils/shareCard'

const props = defineProps<{
  title: string
  city: string
  presence: Presence[]
  selfId: string
  status: SocketStatus
}>()

const emit = defineEmits<{ share: []; rename: []; setCity: []; copyText: [] }>()

const auth = useAuthStore()
const store = useTripStore()
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

const initials = computed(() =>
  props.presence.map((p) => ({
    client_id: p.client_id,
    name: p.name,
    color: p.color,
    letter: (p.name || '?').slice(0, 1).toUpperCase(),
    isSelf: p.client_id === props.selfId,
  })),
)

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

/** 菜单项一律先关再做事：动作可能弹出对话框或触发下载，留着菜单会挡住视线。 */
function run(fn: () => void) {
  return () => {
    closeMenu()
    fn()
  }
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

    <h1 class="triphead__name">
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
      <span
        v-for="(p, i) in initials"
        :key="p.client_id"
        class="avatar"
        :style="{
          background: p.color || 'var(--accent)',
          '--ring': p.color || 'var(--accent)',
          '--i': i,
        }"
        :title="p.isSelf ? `${p.name}（你）` : p.name"
      >
        {{ p.letter }}
      </span>
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
        <p class="tripmenu__meta tiny muted">
          {{ presenceLabel }}
          <template v-if="presenceNames"> · {{ presenceNames }}</template>
        </p>
        <p class="tripmenu__meta tiny muted">协作状态：{{ statusLabel }}</p>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('rename'))">
          <Pencil class="ic" :size="14" /> 重命名行程
        </button>
        <button class="tripmenu__item" type="button" role="menuitem" @click="run(() => emit('setCity'))">
          <MapPin class="ic" :size="14" /> {{ city ? `目的地城市：${city}` : '设置目的地城市' }}
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
  flex: 0 0 var(--header-h);
  height: var(--header-h);
  padding: 0 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}

.triphead__back {
  background: var(--surface-2);
  border-radius: 50%;
}

/* 行程名是这一屏的主角；品牌字留在首页，这里不占位。 */
.triphead__name {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.3;
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
  padding: 1px 8px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 0;
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.triphead__city:hover {
  color: var(--accent);
  background: var(--accent-soft);
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
  font-size: 12px;
  font-weight: 700;
  color: var(--warp-ink);
  border: 2px solid var(--surface);
  border-radius: 50%;
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

.triphead__menu {
  position: fixed;
  z-index: var(--z-bar);
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 208px;
  max-width: min(320px, calc(100vw - 16px));
  padding: 6px;
}

.tripmenu__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  color: var(--text);
  font-size: 13px;
  text-align: left;
  text-decoration: none;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.tripmenu__item:hover {
  background: var(--surface-2);
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

/* ---------- 手机：一行只留最该看的 ---------- */
@media (max-width: 860px) {
  .triphead {
    gap: 6px;
    padding: 0 8px;
  }

  /* 手机上行程名要拿到尽可能宽的余量：这些次要控件统一进「更多」。
     健康点单独放行——它由下面那条规则决定「只有出问题时才占位」。 */
  .triphead__city,
  .triphead__login,
  .triphead__user,
  .avatars,
  .triphead > .iconbtn:not(.triphead__back):not(.triphead__more):not(.triphead__health) {
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
