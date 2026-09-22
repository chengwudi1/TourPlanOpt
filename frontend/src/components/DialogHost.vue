<script setup lang="ts">
import { computed, nextTick, ref, shallowRef, watch } from 'vue'

import AppModal from '@/components/AppModal.vue'
import type { ConfirmRequest, DialogEntry, DialogValue, PromptRequest, TextRequest } from '@/stores/dialog'
import { useDialogStore } from '@/stores/dialog'

/**
 * 确认框与输入框的落点：一条队列 + 一个 AppModal。
 *
 * 挂在 App.vue 的 RouterView 之外（和 ToastHost 同一个理由：带 transform 的过渡容器会让
 * position: fixed 改以页面为参照）。这里的 `active` 只在自己的 onClosed 里清空，
 * 绝不由队列直接 v-if 掉——AppModal 的退场动画必须由它自己播完。
 */
const store = useDialogStore()

const modal = ref<InstanceType<typeof AppModal> | null>(null)
const active = shallowRef<DialogEntry | null>(null)
const draft = ref('')
const error = ref('')
const copyHint = ref('')
const inputEl = ref<HTMLInputElement | null>(null)
const textEl = ref<HTMLTextAreaElement | null>(null)
const okEl = ref<HTMLButtonElement | null>(null)

const confirmReq = computed(() => {
  const entry = active.value
  return entry && entry.kind === 'confirm' ? (entry.req as ConfirmRequest) : null
})
const promptReq = computed(() => {
  const entry = active.value
  return entry && entry.kind === 'prompt' ? (entry.req as PromptRequest) : null
})
const textReq = computed(() => {
  const entry = active.value
  return entry && entry.kind === 'text' ? (entry.req as TextRequest) : null
})

const title = computed(() => active.value?.req.title ?? '')
const message = computed(() => active.value?.req.message ?? '')
const danger = computed(() => confirmReq.value?.danger ?? false)
const cancelLabel = computed(() => {
  if (textReq.value) return '关闭'
  const req = confirmReq.value ?? promptReq.value
  return req?.cancelLabel ?? '取消'
})
const okLabel = computed(() => confirmReq.value?.confirmLabel ?? promptReq.value?.confirmLabel ?? '确定')

/** 上一条还在退场时不抢镜：两条确认框叠在一起，答案就会记到错误的问题上。 */
function pump() {
  if (active.value) return
  active.value = store.take()
}

watch(() => store.ticket, pump, { immediate: true })

watch(active, async (entry) => {
  error.value = ''
  copyHint.value = ''
  draft.value = entry && entry.kind === 'prompt' ? ((entry.req as PromptRequest).value ?? '') : ''
  if (!entry) return
  await nextTick()
  // 预填的值全选上：改名是「换一个」，不是「在后面再接一段」。
  if (inputEl.value) inputEl.value.select()
  else if (textEl.value) textEl.value.select()
  else okEl.value?.focus()
})

watch(draft, () => {
  if (error.value) error.value = ''
})

function answer(value: DialogValue) {
  const entry = active.value
  if (!entry || entry.answered) return
  store.settle(entry, value)
  modal.value?.close()
}

function cancel() {
  const entry = active.value
  if (!entry || entry.answered) return
  store.settleCancel(entry)
  modal.value?.close()
}

function onClosed() {
  // 遮罩、Esc、右上角 × 都只触发 AppModal 的 close，不经过上面两个函数——兜底按取消兑现。
  store.settleCancel(active.value)
  active.value = null
  pump()
}

function submit() {
  const entry = active.value
  if (!entry || entry.answered) return
  if (entry.kind === 'confirm') {
    answer(true)
    return
  }
  if (entry.kind !== 'prompt') return
  const req = entry.req as PromptRequest
  const raw = draft.value
  if (req.required !== false && !raw.trim()) {
    error.value = req.emptyMessage ?? '请输入内容'
    return
  }
  const invalid = req.validate ? req.validate(raw) : null
  if (invalid) {
    error.value = invalid
    return
  }
  answer(raw)
}

/** 剪贴板不可用时才走到这个对话框，所以「复制」得留一条手动退路。 */
async function copyManual() {
  const req = textReq.value
  if (!req) return
  try {
    await navigator.clipboard.writeText(req.text)
    answer(null)
  } catch {
    textEl.value?.focus()
    textEl.value?.select()
    copyHint.value = '浏览器不允许自动复制：文本已选好，按 Ctrl+C（手机上长按）即可复制'
  }
}
</script>

<template>
  <AppModal v-if="active" ref="modal" :title="title" variant="sheet" @close="onClosed">
    <div class="dlg">
      <p v-if="message" class="dlg__msg">{{ message }}</p>
      <div v-if="promptReq" class="dlg__field">
        <input
          ref="inputEl"
          v-model="draft"
          class="input dlg__input"
          :class="{ 'dlg__input--unit': !!promptReq.unit }"
          type="text"
          :inputmode="promptReq.inputMode ?? 'text'"
          :placeholder="promptReq.placeholder ?? ''"
          :maxlength="promptReq.maxLength"
          autocomplete="off"
          :aria-invalid="error ? 'true' : undefined"
          @keyup.enter="submit"
        />
        <span v-if="promptReq.unit" class="dlg__unit">{{ promptReq.unit }}</span>
      </div>
      <div v-if="textReq" class="dlg__field">
        <textarea
          ref="textEl"
          class="input dlg__text"
          rows="5"
          readonly
          aria-label="待复制的文本"
          :value="textReq.text"
          @click="textEl?.select()"
        />
      </div>
      <p v-if="error" class="dlg__err">{{ error }}</p>
      <p v-else-if="copyHint" class="dlg__hint">{{ copyHint }}</p>
    </div>

    <template #footer>
      <button class="btn dlg__btn" type="button" @click="cancel">{{ cancelLabel }}</button>
      <button
        v-if="textReq"
        ref="okEl"
        class="btn btn--primary dlg__btn"
        type="button"
        @click="copyManual"
      >
        复制
      </button>
      <button
        v-else
        ref="okEl"
        class="btn dlg__btn"
        :class="danger ? 'btn--danger' : 'btn--primary'"
        type="button"
        @click="submit"
      >
        {{ okLabel }}
      </button>
    </template>
  </AppModal>
</template>

<style scoped>
.dlg {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 16px;
}
.dlg__msg {
  font-size: calc(13px * var(--fs-scale));
  line-height: 1.65;
  color: var(--text-2);
  /* 原生框能把「仅影响当前设备…」分段显示，换成 <p> 就全糊成一行——换行得自己认。 */
  white-space: pre-line;
}
.dlg__field {
  position: relative;
  display: flex;
}
.dlg__input {
  width: 100%;
  font-size: calc(15px * var(--fs-scale));
}
.dlg__input--unit {
  padding-right: 36px;
}
.dlg__unit {
  position: absolute;
  top: 50%;
  right: 12px;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text-2);
  transform: translateY(-50%);
  pointer-events: none;
}
.dlg__text {
  width: 100%;
  font-family: inherit;
  font-size: calc(13px * var(--fs-scale));
  line-height: 1.6;
  color: var(--text);
  resize: vertical;
  /* 结算明细与分享链接都是按行看的，横向不滚：宁可换行也不让人以为后面丢了字。 */
  white-space: pre-wrap;
  word-break: break-all;
}
.dlg__err {
  font-size: calc(12px * var(--fs-scale));
  color: var(--danger);
}
.dlg__hint {
  font-size: calc(12px * var(--fs-scale));
  color: var(--text-2);
}
.dlg__btn {
  flex: 1;
  justify-content: center;
}
</style>
