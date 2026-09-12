<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { Presence } from '@/types/domain'
import type { SocketStatus } from '@/stores/socket'
import { ArrowLeft, ImageDown, Link2, User } from '@/components/icons'
import { useAuthStore } from '@/stores/auth'
import { useTripStore } from '@/stores/trip'
import { shareTripCard } from '@/utils/shareCard'

const props = defineProps<{
  title: string
  city: string
  presence: Presence[]
  selfId: string
  status: SocketStatus
}>()

const emit = defineEmits<{ share: []; rename: []; setCity: [] }>()

const auth = useAuthStore()
const store = useTripStore()
const router = useRouter()

/** 返回：站内有上一页就回退（从「我的行程」点进来的常见路径）；
 * 直接打开分享链接进来的，回落到首页。 */
function goBack() {
  if (window.history.state?.back) router.back()
  else router.push('/')
}

const sharing = ref(false)

async function shareImage() {
  if (!store.trip || sharing.value) return
  sharing.value = true
  try {
    await shareTripCard(store.trip, store.currentPlaces)
  } catch {
    // 用户取消分享（AbortError）或生成失败：静默即可，下载路径不经过这里失败。
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

    <h1
      class="triphead__name"
      :title="props.title ? '点击重命名行程' : '点击设置行程名'"
      @click="emit('rename')"
    >
      <span class="triphead__text">{{ title || '未命名行程' }}</span>
      <button
        class="triphead__city tiny"
        type="button"
        :title="city ? '点击修改目的地城市' : '点击设置目的地城市：推荐与搜索范围依据该城市'"
        @click.stop="emit('setCity')"
      >
        {{ city || '设城市' }}
      </button>
    </h1>

    <span class="triphead__spacer" />

    <span
      class="triphead__status tiny"
      :class="`triphead__status--${status}`"
      :title="statusLabel"
    >
      <span class="dot dot--pulse" :class="`dot--${status === 'online' ? 'ok' : 'warn'}`" />
      <span v-if="status !== 'online'">{{ statusLabel }}</span>
    </span>

    <span v-if="auth.user" class="triphead__user tiny">
      {{ auth.user.name }}
      <button class="btn btn--sm btn--ghost" type="button" @click="auth.logout()">退出</button>
    </span>
    <a v-else class="triphead__login tiny" href="/">
      <User :size="13" /> 登录
    </a>

    <div v-if="initials.length" class="avatars" title="此刻在线">
      <span
        v-for="p in initials"
        :key="p.client_id"
        class="avatar"
        :style="{ background: p.color || 'var(--accent)' }"
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
  </header>
</template>

<style scoped>
.triphead {
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
  cursor: pointer;
}

.triphead__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.triphead__city {
  flex: 0 0 auto;
  padding: 1px 8px;
  color: var(--text-2);
  background: var(--surface-2);
  border: 0;
  border-radius: 999px;
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
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  border: 2px solid var(--surface);
  border-radius: 50%;
}

.avatar + .avatar {
  margin-left: -8px;
}
</style>
