<script setup lang="ts">
import { computed, ref } from 'vue'

import { Check, GripVertical, ListChecks, Package, Trash2 } from '@/components/icons'
import { useDragSort } from '@/composables/useDragSort'
import { useTripStore } from '@/stores/trip'
import type { ChecklistItem } from '@/types/domain'

const store = useTripStore()

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

const listEl = ref<HTMLElement | null>(null)
useDragSort(listEl, (ids) => store.reorderChecklist(ids), undefined, {
  item: '.chk',
  handle: '.chk__drag',
  idAttr: 'data-item-id',
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

function rename(item: ChecklistItem) {
  const text = window.prompt('修改清单项名称：', item.text)
  if (text === null) return
  const trimmed = text.trim()
  if (!trimmed || trimmed === item.text) return
  store.updateChecklist(item.id, { text: trimmed })
}

function remove(item: ChecklistItem) {
  if (item.done || window.confirm(`删除「${item.text}」？`)) store.removeChecklist(item.id)
}
</script>

<template>
  <section class="check card">
    <div class="check__head">
      <strong class="check__title"><ListChecks class="ic" :size="14" /> 出行清单</strong>
      <span v-if="total" class="tiny muted">{{ done }}/{{ total }} 已备好</span>
      <span v-else class="tiny muted">出发前逐项确认，避免临行遗漏</span>
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

    <div v-if="total" class="check__bar" role="img" :aria-label="`已备好 ${done} / ${total}`">
      <i :class="{ 'check__bar--full': allDone }" :style="{ width: `${pct}%` }" />
    </div>

    <ul v-if="total" ref="listEl" class="check__list">
      <li
        v-for="item in store.checklistSorted"
        :key="item.id"
        :data-item-id="item.id"
        class="chk"
        :class="{ 'chk--done': item.done }"
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
          @click="rename(item)"
        >
          {{ item.text }}
        </button>
        <span v-if="item.added_by" class="tiny muted chk__by">{{ item.added_by }}</span>
        <button class="iconbtn chk__drag" type="button" title="拖动排序">
          <GripVertical class="ic" :size="14" />
        </button>
        <button class="iconbtn chk__drop" type="button" title="删除" @click="remove(item)">
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

.check__bar {
  height: 4px;
  overflow: hidden;
  background: var(--surface-3);
  border-radius: 999px;
}

.check__bar i {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
  transition: width var(--dur-slow) var(--ease-out);
}

.check__bar--full {
  background: var(--ok);
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

.chk__box {
  display: grid;
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  padding: 0;
  color: transparent;
  background: var(--surface);
  border: 2px solid var(--border-strong);
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
  font-size: 14px;
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
