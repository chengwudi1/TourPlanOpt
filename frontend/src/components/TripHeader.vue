<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Presence } from '@/types/domain'
import type { SocketStatus } from '@/stores/socket'
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

const emit = defineEmits<{ share: []; rename: [] }>()

const auth = useAuthStore()
const store = useTripStore()

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
    <strong>TourPlanOpt</strong>
    <span
      class="triphead__title"
      :title="props.title ? '点击重命名行程' : '点击设置行程名'"
      @click="emit('rename')"
    >
      {{ title || '未命名行程' }}
      <span v-if="city" class="muted tiny">· {{ city }}</span>
    </span>
    <span class="triphead__spacer" />

    <span class="triphead__status tiny" :class="`triphead__status--${status}`">
      <span class="dot" :class="`dot--${status === 'online' ? 'ok' : 'warn'}`" />
      <span>{{ statusLabel }}</span>
    </span>

    <span v-if="auth.user" class="triphead__user tiny">
      {{ auth.user.name }}
      <button class="btn btn--sm btn--ghost" type="button" @click="auth.logout()">退出</button>
    </span>
    <a v-else class="triphead__user tiny" href="/">登录</a>

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

    <button class="btn btn--sm" type="button" :disabled="sharing || !store.currentPlaces.length" @click="shareImage">
      {{ sharing ? '生成中…' : '分享图' }}
    </button>
    <button class="btn btn--sm" type="button" @click="emit('share')">
      分享链接
    </button>
  </header>
</template>

<style scoped>
.triphead {
  display: flex;
  gap: 10px;
  align-items: center;
}

.triphead__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

a.triphead__user {
  color: var(--accent);
  text-decoration: none;
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
