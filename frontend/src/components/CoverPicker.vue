<script setup lang="ts">
import { computed, ref } from 'vue'

import SegmentedControl, { type SegOption } from '@/components/SegmentedControl.vue'
import { Check, ImageDown, RotateCcw } from '@/components/icons'
import type { CoverSource } from '@/types/domain'
import { BUILTIN_COVERS } from '@/utils/builtinCovers'
import { shrinkToCover } from '@/utils/coverImage'

/**
 * 封面选择面板的内容件：四档来源（默认封面 / 本机上传 / 行程图片 / 城市图片）+ 一枚「恢复默认封面」。
 *
 * 与 `SettingsPanel` / `PlaceEditor` 同一套结构：**这里只挑图，不写图**。写库是宿主的事——
 * 站内图落 `cover_url`（走 op），本机文件落上传端点。所以本组件不认识 store，也不发请求。
 *
 * 唯一例外是那六张内置海报：它们是打包进前端的静态资产（`public/covers/`），不是数据，
 * 没有要现取的必要，也没有能让宿主递进来的理由——递进来也只是把同一个常量抄一遍。
 *
 * 候选图由宿主递进来而不是自己去读。创建弹窗那一处**不**复用这里：那一刻既没有地点图片可列，
 * 城市图片也没有可写进去的地方（还没有 trip_id），它用的是自己那枚「本机上传」海报位（决策 9）。
 */
const props = defineProps<{
  /** 用户亲手选/传的那一张（'' = 没设过，也就是还站在默认档上）。用来标出网格里「这是你自己的那张」。 */
  current: string
  /** 海报上此刻真的显示着的那一张（两档链走下来的结果）。活跃格标的是它，
   *  不是组件自己猜的某一档——默认档挑的是哪张只有那条散列知道。 */
  shown: string
  /** 上面那张出链于哪一档，用来在标题上说清「你现在看到的是默认挑的那张」。 */
  source: CoverSource
  placePhotos: { url: string; name: string }[]
  cityPhotos: string[]
  city: string
}>()

const emit = defineEmits<{
  /** 选了一张站内/外链的图当封面。 */
  pick: [url: string]
  /** 挑了一个本机文件（已预缩成 JPEG blob）。 */
  file: [blob: Blob, label: string]
  /** 恢复默认封面：写空串，让海报头自己挑内置的那一张。 */
  auto: []
}>()

const SOURCE_OPTIONS: readonly SegOption[] = [
  { value: 'builtin', label: '默认封面' },
  { value: 'upload', label: '本机上传' },
  { value: 'place', label: '行程图片' },
  { value: 'city', label: '城市图片' },
]

const PLACE_EMPTY = '这段行程里的地点都还没有图片。先在卡片里给某个地点补一张，或换一档来源。'
/** 打开面板先落在默认档：这一档解释「现在这张是怎么来的」，另外三档才是「换成别的」。 */
const tab = ref<string>('builtin')

const fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)

/** 海报上此刻那张标进网格里——否则用户看不出「现在用的就是这张」。 */
const active = computed(() => props.shown)

const gridItems = computed(() => {
  if (tab.value === 'builtin') return BUILTIN_COVERS.map((c) => ({ url: c.url, label: c.label }))
  if (tab.value === 'place') return props.placePhotos.map((p) => ({ url: p.url, label: p.name }))
  return props.cityPhotos.map((url, i) => ({ url, label: `城市候选 ${i + 1}` }))
})

const emptyHint = computed(() => {
  // 有图就不能给这句话：网格是 `v-else`，提示一非空整档就没了。
  if (tab.value === 'place') return props.placePhotos.length ? '' : PLACE_EMPTY
  if (tab.value !== 'city') return ''
  if (!props.city) return '还没有设置目的地城市，取不到城市图片。'
  if (!props.cityPhotos.length) return `${props.city} 的候选里没有带图的地点，换一档来源试试。`
  return ''
})

function onPickFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 先清掉 value：连选同一张文件时浏览器不会报 change，用户会以为键坏了。
  input.value = ''
  if (!file) return
  void useFile(file)
}

async function useFile(file: File) {
  busy.value = true
  try {
    const blob = await shrinkToCover(file)
    emit('file', blob, file.name)
  } catch {
    // 预缩这一步整个坏掉也不该吞掉这张图：原样交出去，后端的 4 MB 硬闸会给出人话。
    emit('file', file, file.name)
  } finally {
    busy.value = false
  }
}

function choose(url: string) {
  if (url === props.current) return
  emit('pick', url)
}
</script>

<template>
  <div class="cover">
    <p class="cover__now tiny muted">
      <template v-if="source === 'custom'">当前使用的是自己选的封面。</template>
      <template v-else>当前使用的是默认封面：按行程从内置的六张里固定挑一张，同行人看到同一张。</template>
    </p>

    <SegmentedControl v-model="tab" :options="SOURCE_OPTIONS" label="封面来源" />

    <section v-if="tab === 'upload'" class="cover__upload">
      <button class="btn btn--primary" type="button" :disabled="busy" @click="fileInput?.click()">
        <ImageDown class="ic" :size="14" /> {{ busy ? '处理中…' : '选一张本机图片' }}
      </button>
      <input ref="fileInput" class="cover__file" type="file" accept="image/jpeg,image/png,image/webp" @change="onPickFile" />
      <p class="tiny muted">支持 JPEG、PNG 与 WebP，单张不超过 4 MB。较大的图会先在本机缩到 1600px 再上传。</p>
    </section>

    <section v-else class="cover__grid-wrap">
      <p v-if="tab === 'builtin'" class="cover__hint tiny muted">
        这六张随应用一起装好，与网络无关。点中任意一张会固定为本行程的封面，之后不再自动更换。
      </p>
      <p v-if="emptyHint" class="cover__empty tiny muted">{{ emptyHint }}</p>
      <div v-else class="cover__grid">
        <button
          v-for="item in gridItems"
          :key="item.url"
          class="cover__cell"
          :class="{ 'is-active': item.url === active }"
          type="button"
          :title="item.label"
          @click="choose(item.url)"
        >
          <img :src="item.url" :alt="item.label" loading="lazy" />
          <Check v-if="item.url === active" class="cover__mark" :size="14" />
        </button>
      </div>
    </section>

    <div v-if="source === 'custom'" class="cover__foot">
      <button class="btn btn--sm" type="button" @click="emit('auto')">
        <RotateCcw class="ic" :size="13" /> 恢复默认封面
      </button>
      <span class="tiny muted">清掉自己选的这张，改回内置的默认封面。</span>
    </div>
  </div>
</template>

<style scoped>
.cover {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.cover__now {
  line-height: 1.6;
}

.cover__upload {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  align-items: flex-start;
}

.cover__file {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  opacity: 0;
  pointer-events: none;
}

.cover__hint {
  margin-bottom: var(--s2);
  line-height: 1.6;
}

.cover__grid-wrap {
  min-height: 96px;
}

.cover__empty {
  padding: var(--s4) 0;
  line-height: 1.6;
}

.cover__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
  gap: var(--s2);
  max-height: 268px;
  overflow-y: auto;
}

.cover__cell {
  position: relative;
  aspect-ratio: 4 / 3;
  padding: 0;
  overflow: hidden;
  cursor: pointer;
  background: var(--surface-2);
  border: 2px solid transparent;
  border-radius: var(--radius-sm);
}

.cover__cell img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover__cell:hover {
  border-color: var(--accent-soft);
}

.cover__cell.is-active {
  border-color: var(--accent);
}

.cover__mark {
  position: absolute;
  top: 4px;
  left: 4px;
  color: #fff;
  filter: drop-shadow(0 1px 2px rgb(0 0 0 / 60%));
}

.cover__foot {
  display: flex;
  gap: var(--s3);
  align-items: center;
  justify-content: space-between;
  padding-top: var(--s3);
  border-top: 1px solid var(--border);
}
</style>
