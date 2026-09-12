<script setup lang="ts">
import { ref } from 'vue'

import { getClientName, setClientName } from '@/composables/useClientIdentity'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ join: [name: string] }>()

const auth = useAuthStore()
const name = ref(auth.user?.name ?? getClientName())
const submitted = ref(false)

function join() {
  if (submitted.value) return
  submitted.value = true
  setClientName(name.value)
  emit('join', name.value.trim())
}
</script>

<template>
  <div class="gate">
    <div class="gate__box card">
      <h2 class="gate__title">加入行程</h2>
      <p class="muted tiny">
        同一行程的成员可实时查看彼此添加的地点与安排。<br />
        该名称会标注在您的改动上，请使用可识别的称呼。
      </p>

      <form class="gate__form" @submit.prevent="join">
        <input
          v-model="name"
          class="input"
          type="text"
          maxlength="40"
          placeholder="请输入昵称，例如：小明"
          autocomplete="off"
          autofocus
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
  z-index: 40;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgba(246, 247, 249, 0.75);
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
}
</style>
