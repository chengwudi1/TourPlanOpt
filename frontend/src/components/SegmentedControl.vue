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
/* F 皮肤：整条控件是一块玻璃，选中的那一格是一枚实心近黑圆片（mock 的 .tabs/.tab.on）。
   容器走 --glass-dense 而不是标准档：hint 行是 --text-3，标准玻璃上不许放它。 */
.seg {
  display: grid;
  grid-auto-flow: column;
  /* 1fr 的隐式最小是 min-content：一句长提示能把整个等宽列撑破（设置抽屉 358px 里
     六组这控件全撞这条）。minmax(0,1fr) 把下限压回 0，让文字自己折行。 */
  grid-auto-columns: minmax(0, 1fr);
  gap: 4px;
  padding: 4px;
  background: var(--glass-dense);
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
}

.seg__opt {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  padding: 6px 8px;
  color: var(--text-2);
  text-align: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease);
}

.seg__opt:hover:not(.seg__opt--on) {
  color: var(--text);
  /* 轨道本身是重玻璃，实色 --surface-3 的 hover 格会是一块不透明补丁。 */
  background: color-mix(in srgb, var(--text) 8%, transparent);
}

/* 选中格 = mock 的 .tab.on：实心近黑圆片 + 白字（--chip-on 组），不做投影——
   圆片压在玻璃上本身就是最深的一块，再加影子会像浮起来。深色主题里这组令牌反过来
   变浅片配深字，仍是同一条「实心对比格」的语义。 */
.seg__opt--on {
  color: var(--chip-on-ink);
  background: var(--chip-on);
}

.seg__label {
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
}

.seg__hint {
  color: var(--text-3);
}

/* 选中格里的提示行（「1 段」「按真实路况计算」）跟着白字走，否则 text-3 压在深圆片上
   只剩 2:1 一带——mock 的 .tab.on i 也是同一条 rgba(255,255,255,.7)。 */
.seg__opt--on .seg__hint {
  color: color-mix(in srgb, var(--chip-on-ink) 72%, transparent);
}

/* ---------- 手机 ---------- */
@media (max-width: 860px) {
  .seg__opt {
    min-height: 38px;
  }
  .seg__hint {
    overflow-wrap: anywhere;
  }
}
</style>
