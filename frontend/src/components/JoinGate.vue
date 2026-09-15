<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { getClientName, setClientName } from '@/composables/useClientIdentity'
import { useAuthStore } from '@/stores/auth'
import { trapTab } from '@/utils/focusTrap'

/**
 * 加入遮罩（O1）。
 *
 * 它一直是事实上的模态：不填昵称就进不去。但界面上没有任何模态该有的说法——
 * 没有 role/aria-modal，Tab 能一路跑到遮罩背后那些还活着的按钮上去。
 * 这里补齐语义，并复用 AppModal 那条焦点陷阱；Esc 则故意不接：
 * 这道门没有「取消」可退，能退就等于把协同状态卡在一个没人负责的出口上。
 */
const emit = defineEmits<{ join: [name: string] }>()

const auth = useAuthStore()
const box = ref<HTMLElement | null>(null)
const nameEl = ref<HTMLInputElement | null>(null)
const name = ref(auth.user?.name ?? getClientName())
const submitted = ref(false)

function join() {
  if (submitted.value) return
  submitted.value = true
  setClientName(name.value)
  emit('join', name.value.trim())
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Tab') trapTab(box.value, e)
}

let prevOverflow = ''

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  prevOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  //  autofocus 属性只对首次渲染生效，这扇门是后进来的：自己把焦点放好。
  nameEl.value?.focus()
  nameEl.value?.select()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = prevOverflow
})
</script>

<template>
  <div class="gate" role="dialog" aria-modal="true" aria-labelledby="gate-title">
    <div ref="box" class="gate__box card">
      <h2 id="gate-title" class="gate__title">加入行程</h2>
      <p class="muted tiny">
        同一行程的成员可实时查看彼此添加的地点与安排。<br />
        该名称会标注在您的改动上，请使用可识别的称呼。
      </p>

      <form class="gate__form" @submit.prevent="join">
        <input
          ref="nameEl"
          v-model="name"
          class="input"
          type="text"
          maxlength="40"
          aria-label="昵称"
          placeholder="请输入昵称，例如：小明"
          autocomplete="off"
        />
        <button class="btn btn--primary" type="submit" :disabled="!name.trim()">
          进入行程
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.gate {
  position: absolute;
  z-index: var(--z-overlay);
  inset: 0;
  display: grid;
  place-items: center;
  /* 雾从 --bg 调出来：原来那枚冷白 rgba(246,247,249) 既不属于亮色纸面也不属于深色，
     两种主题下都是「糊了一层别家的玻璃」。 */
  background: color-mix(in srgb, var(--bg) 78%, transparent);
  backdrop-filter: blur(3px);
}

.gate__box {
  width: min(380px, calc(100vw - 40px));
  padding: 26px;
}

.gate__title {
  margin: 0 0 6px;
  font-size: 20px;
}

.gate__form {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}

.gate__form .input {
  flex: 1 1 auto;
  min-height: 40px;
}
</style>
