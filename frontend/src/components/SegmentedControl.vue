<script setup lang="ts">
/**
 * 自绘分段控件。原生 <select> 在 Windows 上会带一套系统箭头与下拉皮肤，跟 token 体系
 * 完全不搭，所以凡是「三选一」这种有限选项都用它。
 *
 * modelValue 走 string 而不是泛型：调用方拿的是 'driving' | 'walking' 这类联合类型，
 * 传进来时收窄、发出去时由调用方断言回去，比在 SFC 上开 generic 省事。
 */
export interface SegOption {
  value: string
  label: string
  hint?: string
}

defineProps<{
  modelValue: string
  options: readonly SegOption[]
  label: string
}>()

const emit = defineEmits<{ 'update:modelValue': [string] }>()
</script>

<template>
  <div class="seg" role="radiogroup" :aria-label="label">
    <button
      v-for="opt in options"
      :key="opt.value"
      class="seg__opt"
      :class="{ 'seg__opt--on': opt.value === modelValue }"
      type="button"
      role="radio"
      :aria-checked="opt.value === modelValue"
      @click="emit('update:modelValue', opt.value)"
    >
      <span class="seg__label">{{ opt.label }}</span>
      <span v-if="opt.hint" class="seg__hint tiny">{{ opt.hint }}</span>
    </button>
  </div>
</template>

<style scoped>
.seg {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 3px;
  padding: 3px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.seg__opt {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 8px;
  color: var(--text-2);
  text-align: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: calc(var(--radius-sm) - 3px);
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.seg__opt:hover:not(.seg__opt--on) {
  color: var(--text);
  background: var(--surface-3);
}

/* 选中态用强调浅底 + accent-strong 文字（6.1:1）——实心青会让整条控件变成一个大按钮。 */
.seg__opt--on {
  color: var(--accent-strong);
  background: var(--surface);
  border-color: var(--accent);
  box-shadow: var(--shadow-sm);
}

.seg__label {
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
}

.seg__hint {
  color: var(--text-3);
}
</style>
