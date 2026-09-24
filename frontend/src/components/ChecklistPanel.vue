<script setup lang="ts">
import { computed, ref } from 'vue'

import { Check, GripVertical, ListChecks, Package, Trash2 } from '@/components/icons'
import { useDragSort } from '@/composables/useDragSort'
import { useDialogStore } from '@/stores/dialog'
import { useTripStore } from '@/stores/trip'
import type { ChecklistItem } from '@/types/domain'

const store = useTripStore()
const dialog = useDialogStore()

const draft = ref('')

/**
 * 一份「不带一定会后悔」的最小集。
 *
 * 只补行程里还没有的：去重在服务端做，所以这颗按钮可以反复点，点第二次不会多出一屏重复
 * 条目，也不会把已经打勾的那条重置成未完成。
 */
const PACKING_TEMPLATE = [
  '身份证 / 护照',
  '现金与银行卡',
  '充电宝',
  '数据线与充电头',
  '耳机',
  '洗漱用品',
  '换洗衣物与鞋子',
  '常用药品',
  '纸巾',
  '雨伞 / 防晒',
  '插座转换器',
  '备用眼镜',
]

const total = computed(() => store.checklist.length)
const done = computed(() => store.checklistDoneCount)
const pct = computed(() => (total.value ? Math.round((done.value / total.value) * 100) : 0))
const allDone = computed(() => total.value > 0 && done.value === total.value)

/**
 * 全员同一人整理时，那个名字在表头说一次就够了。
 *
 * 逐行挂署名是在「多人协作」时才成立的信息；一份由「补常用」一次生成的清单，
 * 15 行重复同一个昵称会把整页压成噪声，反而看不出谁后来加过东西。
 */
const sharedAuthor = computed(() => {
  const names = new Set(store.checklist.map((i) => i.added_by).filter(Boolean))
  return names.size === 1 && store.checklist.length > 1 ? [...names][0] : ''
})

const listEl = ref<HTMLElement | null>(null)
useDragSort(listEl, (ids) => store.reorderChecklist(ids), undefined, {
  item: '.chk',
  handle: '.chk__drag',
  idAttr: 'data-item-id',
  ghostClass: 'chk--ghost',
})

/** 一行一条地粘贴进来，就当成一次批量添加：省得从备忘录里复制十行要点十次。 */
function submit() {
  const texts = draft.value
    .split('\n')
    .map((t) => t.trim())
    .filter(Boolean)
  if (!texts.length) return
  store.checklistAdd(texts)
  draft.value = ''
}

async function rename(item: ChecklistItem) {
  const text = await dialog.prompt({
    title: '修改清单项',
    value: item.text,
    confirmLabel: '保存',
    emptyMessage: '清单项不能为空',
  })
  if (text === null) return
  const trimmed = text.trim()
  if (trimmed === item.text) return
  store.updateChecklist(item.id, { text: trimmed })
}

/** 一律立即删 + 5 秒可撤销：确认框会被肌肉记忆按下去，挡不住手快，只会拖慢手快的人。 */
function remove(item: ChecklistItem) {
  store.deleteChecklistWithUndo(item.id)
}
</script>

<template>
  <section class="check card">
    <div class="check__head">
      <strong class="check__title"><ListChecks class="ic" :size="14" /> 出行清单</strong>
      <svg
        v-if="total"
        class="check__ring"
        viewBox="0 0 24 24"
        role="img"
        :aria-label="`已备好 ${done} / ${total}`"
      >
        <circle class="check__ring-bg" pathLength="100" cx="12" cy="12" r="9" />
        <circle
          class="check__ring-fg"
          :class="{ 'check__ring-fg--full': allDone }"
          pathLength="100"
          cx="12"
          cy="12"
          r="9"
          :style="{ strokeDashoffset: 100 - pct }"
        />
      </svg>
      <span v-if="total" class="tiny muted">{{ done }}/{{ total }} 已备好</span>
      <span v-else class="tiny muted">出发前逐项确认，避免临行遗漏</span>
      <span v-if="sharedAuthor" class="tiny muted">· 由 {{ sharedAuthor }} 整理</span>
      <button
        v-if="total"
        class="btn btn--sm btn--ghost check__fill"
        type="button"
        title="仅补充行程中缺失的条目，可重复点击"
        @click="store.checklistAdd(PACKING_TEMPLATE)"
      >
        <Package class="ic" :size="13" /> 补常用
      </button>
    </div>

    <ul v-if="total" ref="listEl" class="check__list">
      <li
        v-for="(item, i) in store.checklistSorted"
        :key="item.id"
        :data-item-id="item.id"
        class="chk reveal reveal--pop"
        :class="{ 'chk--done': item.done }"
        :style="{ '--i': i > 6 ? 6 : i }"
      >
        <button
          class="chk__box"
          type="button"
          :aria-checked="item.done"
          role="checkbox"
          :title="item.done ? '取消标记' : '标记为已备好'"
          @click="store.updateChecklist(item.id, { done: !item.done })"
        >
          <Check class="ic" :size="13" />
        </button>
        <button
          class="chk__text"
          type="button"
          title="点击改名"
          @click="void rename(item)"
        >
          {{ item.text }}
        </button>
        <span v-if="item.added_by && !sharedAuthor" class="tiny muted chk__by">{{ item.added_by }}</span>
        <button class="iconbtn chk__drag" type="button" title="拖动排序">
          <GripVertical class="ic" :size="14" />
        </button>
        <button class="iconbtn chk__drop" type="button" title="删除这一项" @click="void remove(item)">
          <Trash2 class="ic" :size="14" />
        </button>
      </li>
    </ul>

    <div v-else class="check__empty">
      <p class="tiny muted check__empty-text">
        尚未添加清单项。可载入一份常用清单，或在下方自行添加。
      </p>
      <button class="btn btn--sm" type="button" @click="store.checklistAdd(PACKING_TEMPLATE)">
        <Package class="ic" :size="13" /> 补常用清单
      </button>
    </div>

    <div class="check__add">
      <input
        v-model="draft"
        class="input check__input"
        type="text"
        maxlength="120"
        placeholder="添加一项，按回车确认；粘贴多行可一次添加多项"
        @keyup.enter="submit"
      />
      <button class="btn btn--sm" type="button" :disabled="!draft.trim()" @click="submit">
        添加
      </button>
    </div>
  </section>
</template>

<style scoped>
.check {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}

.check__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.check__title {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--accent-strong);
}

.check__fill {
  margin-left: auto;
}

/* 环读数在标题行里，所以它得跟字一起站住：baseline 对齐会让替换元素拿底边当基线，
   看着就是往下掉半格。 */
.check__ring {
  flex: 0 0 auto;
  align-self: center;
  width: 18px;
  height: 18px;
}

.check__ring circle {
  fill: none;
  stroke-width: 3;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
}

.check__ring-bg {
  stroke: var(--surface-3);
}

.check__ring-fg {
  stroke: var(--accent);
  stroke-linecap: round;
  stroke-dasharray: 100;
  transition:
    stroke-dashoffset var(--dur-slow) var(--ease-out),
    stroke var(--dur-fast) var(--ease-out);
}

/* 环是读数不是控件，所以备齐了可以换 --ok：这一档不邀请点击。 */
.check__ring-fg--full {
  stroke: var(--ok);
}

.check__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.chk {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 4px 6px 4px 2px;
  border-radius: var(--radius-sm);
}

.chk:hover {
  background: var(--surface-hover);
}

/* 拖拽中的占位行压暗，且不跟 hover：跟行卡同一套读法——那是位置，不是可点的东西。 */
.chk--ghost {
  opacity: 0.4;
}

.chk--ghost:hover {
  background: transparent;
}

.chk__box {
  display: grid;
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  padding: 0;
  color: transparent;
  background: var(--surface);
  border: 2px solid var(--ink);
  border-radius: 6px;
  cursor: pointer;
  place-items: center;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.chk__box:hover {
  border-color: var(--accent);
}

.chk--done .chk__box {
  color: var(--accent-ink);
  background: var(--accent);
  border-color: var(--accent);
}

.chk__text {
  flex: 1;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  font: inherit;
  font-size: calc(14px * var(--fs-scale));
  color: inherit;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: none;
  border: 0;
  cursor: pointer;
}

.chk--done .chk__text {
  color: var(--text-3);
  text-decoration: line-through;
}

.chk__by {
  flex: 0 0 auto;
  max-width: 84px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chk__drag {
  padding: 3px;
  cursor: grab;
}

.chk__drop {
  padding: 3px;
  opacity: 0;
}

.chk:hover .chk__drop,
.chk:focus-within .chk__drop {
  opacity: 1;
}

/* 触屏没有 hover：删除与拖动把手常驻，否则这一项既删不掉也挪不动；
   行洗色是 hover 的副产品，触屏上会粘在最后点过的一行上，作废。 */
@media (hover: none) {
  .chk__drag,
  .chk__drop {
    opacity: 1;
  }
  .chk:hover {
    background: transparent;
  }
  .chk--done:hover .chk__box {
    background: var(--accent);
  }
}

/* ---------- 手机：清单是「勾一下」的屏，落点按手指来 ---------- */
@media (max-width: 860px) {
  .chk {
    min-height: 38px;
    padding: 6px 6px 6px 2px;
  }
  /* 20px 的复选框是这一屏最高频的靶子，按不准等于功能没有。 */
  .chk__box {
    width: 28px;
    height: 28px;
    border-radius: 8px;
  }
  .chk__drag,
  .chk__drop {
    padding: 7px;
  }
}

.check__empty {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.check__empty-text {
  margin: 0;
}

.check__add {
  display: flex;
  gap: 6px;
}

.check__input {
  flex: 1;
  min-width: 0;
}
</style>
