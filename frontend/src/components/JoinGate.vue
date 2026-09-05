<script setup lang="ts">
import { ref } from 'vue'

import { getClientName, setClientName } from '@/composables/useClientIdentity'

const emit = defineEmits<{ join: [name: string] }>()

const name = ref(getClientName())
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
        同一个行程里的每个人都能实时看到彼此添加的地点和安排。<br />
        用一个大家认得出的名字，你改的东西会带上它。
      </p>

      <form class="gate__form" @submit.prevent="join">
        <input
          v-model="name"
          class="input"
          type="text"
          maxlength="40"
          placeholder="你的名字，例如：小明"
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
