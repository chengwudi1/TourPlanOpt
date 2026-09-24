<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  ExternalLink,
  Landmark,
  MapPin,
  MoonStar,
  Plus,
  ShoppingBasket,
  UtensilsCrossed,
} from '@/components/icons'
import SegmentedControl, { type SegOption } from '@/components/SegmentedControl.vue'
import { useTripStore } from '@/stores/trip'
import type { Poi } from '@/types/domain'
import { apiFetch } from '@/utils/api'

const props = defineProps<{ city: string; tripId: string }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; setCity: [] }>()

const store = useTripStore()

type Sort = 'composite' | 'hot' | 'distance'

/** 开关本身长在「添加地点」栏那一行上（父组件），这里只报状态、只接指令。 */
const open = ref(false)
const category = ref<'scenic' | 'food' | 'night'>('scenic')
const sort = ref<Sort>('composite')
const pois = ref<Poi[]>([])
const amapUrl = ref('')
const loading = ref(false)
const error = ref<{ message: string; hint: string } | null>(null)
/** key = 城市:类目:排序:基准点。命中就换数据，不重新打接口；distance 档零配额，
    换基准点只是本地重排，缓存里躺着的是同一批候选的不同顺序，值得各存一份。 */
const cache = new Map<string, { pois: Poi[]; amapUrl: string }>()
// 在途去重：缓存要等响应回来才写，同一 key 在那之前能被 watch 的多次触发钻过去
// （实测挪一次基准点就发出两条一模一样的请求）。配额在服务端有缓存兜着，但这条请求本就不该发。
const inFlight = new Set<string>()

const CATEGORIES: {
  key: 'scenic' | 'food' | 'night'
  label: string
  icon: typeof Landmark
}[] = [
  { key: 'scenic', label: '景点', icon: Landmark },
  { key: 'food', label: '小吃美食', icon: UtensilsCrossed },
  { key: 'night', label: '夜市', icon: MoonStar },
]

const SORT_OPTIONS: SegOption[] = [
  { value: 'composite', label: '综合' },
  { value: 'hot', label: '热度' },
  { value: 'distance', label: '距离' },
]

/* ---------- 距离基准：用户显式选定的一个地点 ---------- */

/** 基准点必须是行程里看得见的一个地点（或想去清单一员），不许是凭空坐标，也不许是几个地点的
    平均位置——「距此」的「此」要能在界面上指出来，质心就指不出来了。 */
const originId = ref('')

/** 高德坐标一律 6 位小数，1e-6 已经比它更细，拿来判「就是同一处」。 */
function sameCoord(a: [number, number], b: [number, number]): boolean {
  return Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6
}

function rowById(id: string): { name: string; lng: number; lat: number } | null {
  const place = store.places.find((p) => p.id === id)
  if (place) return { name: place.name, lng: place.lng, lat: place.lat }
  // 酒店常常还躺在想去清单里，没排进某一天，但「离酒店多远」正是用户要的那个数。
  const item = store.stash.find((s) => s.id === id)
  return item ? { name: item.name, lng: item.lng, lat: item.lat } : null
}

function rowAt(coord: [number, number]): string | null {
  const place = store.places.find((p) => sameCoord([p.lng, p.lat], coord))
  if (place) return place.id
  return store.stash.find((s) => sameCoord([s.lng, s.lat], coord))?.id ?? null
}

const originPlace = computed(() => (originId.value ? rowById(originId.value) : null))

/** 行 id 的备胎：基准点最后一次活着时的坐标。只在 id 失效时用得上。 */
const originAt = ref<[number, number] | null>(null)

const origin = computed<[number, number] | null>(() =>
  originPlace.value ? [originPlace.value.lng, originPlace.value.lat] : null,
)

/** 下拉按天分组，项前带天内序号：一天里可以有三行都叫「地图选点」，光靠天头分不开。 */
const originGroups = computed(() =>
  store.days
    .map((day) => ({
      label: `第 ${day.day_index + 1} 天${day.title ? ` · ${day.title}` : ''}`,
      items: store.places
        .filter((p) => p.day_id === day.id)
        .sort((a, b) => a.sort_index - b.sort_index)
        .map((p, i) => ({
          id: p.id,
          label: `${i + 1}. ${p.name}${
            day.start_place_id === p.id
              ? '（当天起点）'
              : day.end_place_id === p.id
                ? '（当天终点）'
                : ''
          }`,
        })),
    }))
    .filter((g) => g.items.length),
)

const originStash = computed(() => store.stash.map((s) => ({ id: s.id, label: s.name })))

const originNote = computed(() => {
  if (originPlace.value) return `距离按「${originPlace.value.name}」的直线距离计算，非路况耗时`
  if (originGroups.value.length || originStash.value.length) {
    return '未选择基准点：请选择一个地点后按距离重排'
  }
  return '行程中还没有可作基准的地点：先添加地点，或把它加入想去'
})

/* ---------- 排序、基准点与展开态：按行程记住 ---------- */

const prefsKey = computed(() => `tourplanopt.reco-${props.tripId}`)

function loadPrefs() {
  let raw: unknown = null
  try {
    raw = JSON.parse(localStorage.getItem(prefsKey.value) ?? 'null')
  } catch {
    return // 脏数据与无痕模式一样：回到默认，不连带面板一起坏
  }
  if (!raw || typeof raw !== 'object') return
  const { sort: saved, origin: savedOrigin, originAt: savedAt, open: savedOpen } = raw as {
    sort?: unknown
    origin?: unknown
    originAt?: unknown
    open?: unknown
  }
  if (saved === 'composite' || saved === 'hot' || saved === 'distance') sort.value = saved
  if (typeof savedOrigin === 'string') originId.value = savedOrigin
  if (
    Array.isArray(savedAt) &&
    savedAt.length === 2 &&
    savedAt.every((v) => typeof v === 'number' && Number.isFinite(v))
  ) {
    originAt.value = [savedAt[0] as number, savedAt[1] as number]
  }
  if (typeof savedOpen === 'boolean') open.value = savedOpen
}

function persistPrefs() {
  // 坐标快照在这里顺手刷新：它是行 id 失效时唯一的线索，必须跟着基准点走。
  if (originPlace.value) originAt.value = [originPlace.value.lng, originPlace.value.lat]
  try {
    localStorage.setItem(
      prefsKey.value,
      JSON.stringify({
        sort: sort.value,
        origin: originId.value,
        originAt: originAt.value,
        open: open.value,
      }),
    )
  } catch {
    // 无痕模式：丢掉偏好不影响功能
  }
}

// 切档、换基准、开合都得记住：只写一部分的话，「刷新后展开态没回来」会被当成没保存的 bug。
watch([sort, originId, open], persistPrefs)
watch(open, (v) => emit('update:open', v), { immediate: true })

/** 上次选的基准点可能早就被删了。等行程确实加载完再判失效——挂载那一刻 places 还是空的，
    抢着清会把「快照先到、偏好在后」的正常路径误杀成没选过。 */
watch(
  () => [originId.value, store.loading, originPlace.value === null] as const,
  ([id, isLoading]) => {
    if (isLoading || !id || originPlace.value) return
    // id 没了不等于地点没了：「放进第 1 天」就是删旧行建新行，坐标一分没动。
    originId.value = originAt.value ? (rowAt(originAt.value) ?? '') : ''
    persistPrefs()
  },
  { immediate: true },
)

/** 键用发给服务端的那串坐标：基准点是用户明确挑的，只有真把它挪走才该重排。掺行 id 的话，
    「放进某一天」换个 id、坐标一分不动，也会白重排一次。 */
const originKey = computed(() =>
  origin.value ? `${origin.value[0].toFixed(6)},${origin.value[1].toFixed(6)}` : 'none',
)

/** 基准点只跟距离档有关：无条件掺进缓存键的话，综合和热度会每换一个基准点就多存一份，
    切回去还得白打一次接口（实测过）。 */
const basisKey = computed(() => (sort.value === 'distance' ? originKey.value : 'none'))

// 后发先至：下拉里连点两个基准点时，先发的请求可能后回来，把顺序退回上一条基准点的结果。
// 只让最新那一次落地，旧响应直接丢。
let reqSeq = 0

watch(
  () => [props.city, open.value, category.value, sort.value, basisKey.value] as const,
  async ([city, isOpen]) => {
    if (!city || !isOpen) return
    // 没有基准点就不发这一枪：服务端只会把综合那批原样退回，顶着「距离」标签的假顺序
    // 比一条空列表更坏。界面上此时显示的是「挑一个基准点」的提示。
    if (sort.value === 'distance' && !origin.value) return
    const key = `${city}:${category.value}:${sort.value}:${basisKey.value}`
    if (inFlight.has(key)) return
    const hit = cache.get(key)
    if (hit) {
      // 早退不等于数据对：pois 里可能还留着上一个 key 的结果，必须拿这份缓存盖回去。
      pois.value = hit.pois
      amapUrl.value = hit.amapUrl
      error.value = null
      return
    }
    inFlight.add(key)
    const seq = ++reqSeq
    loading.value = true
    error.value = null
    // origin 回显是服务端唯一的「我采纳了这个基准点」信号：读不懂或境外坐标会被当作没给。
    const asked = sort.value === 'distance' && origin.value !== null
    try {
      const params = new URLSearchParams({ city, category: category.value, sort: sort.value })
      if (sort.value === 'distance' && origin.value) {
        const [lng, lat] = origin.value
        params.set('origin', `${lng.toFixed(6)},${lat.toFixed(6)}`)
      }
      const data = await apiFetch<{
        pois: Poi[]
        amap_url: string
        origin?: [number, number] | null
      }>(`/api/city/recommendations?${params}`)
      if (asked && data.origin == null) {
        // 服务端把读不懂或境外的 origin 当作没给，退回的就是综合顺序——假顺序不配进缓存。
        throw new Error('这个基准点高德认不出来（境外坐标？）')
      }
      // 结果属于它自己那个 key，缓存照写；被更新的请求取代时只丢界面，不丢这一份。
      cache.set(key, { pois: data.pois, amapUrl: data.amap_url })
      if (seq !== reqSeq) return
      pois.value = data.pois
      amapUrl.value = data.amap_url
    } catch (err) {
      if (seq !== reqSeq) return
      const e = err as Error
      error.value = {
        message: e.message || '推荐加载失败',
        hint: asked ? '在上方换一个地点当基准' : '稍后再试',
      }
    } finally {
      inFlight.delete(key)
      // 只有最新那次才有权收spinner——旧请求先回来时新请求还在路上。
      if (seq === reqSeq) loading.value = false
    }
  },
)

/** 基准点自己也可能在这批候选里（同一个 POI）——那一行不许写「距此 10 m」，它就是把 0 米
    向上取整到最小粒度的产物。 */
function isBasis(poi: Poi): boolean {
  return origin.value !== null && sameCoord([poi.lng, poi.lat], origin.value)
}

function fmtDistance(meters: number | null | undefined): string | null {
  if (meters == null) return null
  if (meters < 1000) return `${Math.max(10, Math.round(meters / 10) * 10)} m`
  if (meters < 10000) return `${(meters / 1000).toFixed(1)} km`
  return `${Math.round(meters / 1000)} km`
}

function add(poi: Poi) {
  store.addPlace({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function stash(poi: Poi) {
  store.stashAdd({
    name: poi.name,
    lng: poi.lng,
    lat: poi.lat,
    address: poi.address,
    amap_poi_id: poi.id,
    photo_url: poi.photo,
  })
}

function added(poi: Poi): boolean {
  return store.currentPlaces.some((p) => p.amap_poi_id && p.amap_poi_id === poi.id)
}

onMounted(loadPrefs)

defineExpose({
  show: () => (open.value = true),
  toggle: () => (open.value = !open.value),
})
</script>

<template>
  <div v-show="open" class="reco">
    <div class="reco__body">
      <!-- 没有城市时这一整块只有一件事可做，那就给这件事一个键。类目、排序、距离基准在
           按城市之前全是死控件——摆出来只会让人以为点了会有反应，而它永远没反应。 -->
      <div v-if="!city" class="reco__need">
        <div class="reco__need-text">
          <div class="reco__need-title">尚未设置目的地城市</div>
          <p class="tiny muted">景点、美食与夜市推荐按城市给出，设置之后可直接在此处加入行程。</p>
        </div>
        <button class="btn btn--sm btn--primary" type="button" @click="emit('setCity')">
          <MapPin class="ic" :size="13" /> 设置城市
        </button>
      </div>

      <template v-else>
        <p class="reco__lead tiny muted">{{ city }}的景点、美食与夜市，点击即可加入行程</p>

        <div class="reco__tabs">
          <button
            v-for="c in CATEGORIES"
            :key="c.key"
            class="reco__tab"
            :class="{ 'reco__tab--on': category === c.key }"
            type="button"
            @click="category = c.key"
          >
            <component :is="c.icon" class="ic" :size="13" /> {{ c.label }}
          </button>
        </div>

        <SegmentedControl v-model="sort" :options="SORT_OPTIONS" label="发现结果的排序方式" />
        <div v-if="sort === 'distance'" class="reco__origin">
          <label class="tiny muted" for="reco-origin">距离基准点</label>
          <select id="reco-origin" v-model="originId" class="input reco__select">
            <option value="">不设置（该档位不按距离重排）</option>
            <optgroup v-for="g in originGroups" :key="g.label" :label="g.label">
              <option v-for="o in g.items" :key="o.id" :value="o.id">{{ o.label }}</option>
            </optgroup>
            <optgroup v-if="originStash.length" label="想去清单">
              <option v-for="o in originStash" :key="o.id" :value="o.id">{{ o.label }}</option>
            </optgroup>
          </select>
          <p class="reco__note tiny">{{ originNote }}</p>
        </div>

        <p v-if="loading" class="reco__empty tiny muted">正在加载 {{ city }} 的推荐结果…</p>
        <p v-else-if="error" class="reco__empty tiny muted">{{ error.message }}（{{ error.hint }}）</p>
        <p v-else-if="sort === 'distance' && !originPlace" class="reco__empty tiny muted">
          选择基准点后按直线距离重排；未选择时按综合排序展示。
        </p>

        <ul v-else-if="pois.length" class="reco__list">
          <li v-for="poi in pois" :key="poi.id || poi.name" class="reco__item">
            <img
              v-if="poi.photo"
              class="reco__photo"
              :src="poi.photo"
              :alt="`${poi.name} 的照片`"
              loading="lazy"
              referrerpolicy="no-referrer"
            />
            <div class="reco__info">
              <div class="reco__name">{{ poi.name }}</div>
              <div class="tiny muted reco__addr">
                {{ poi.address || poi.district }}<template v-if="poi.distance_m != null">
                  · <template v-if="isBasis(poi)">即为基准点</template
                  ><template v-else>距离 {{ fmtDistance(poi.distance_m) }}</template></template
                >
              </div>
            </div>
            <button
              class="btn btn--sm"
              type="button"
              title="暂存至想去清单"
              @click="stash(poi)"
            >
              <ShoppingBasket class="ic" :size="13" />
            </button>
            <button
              class="btn btn--sm"
              type="button"
              :disabled="added(poi)"
              @click="add(poi)"
            >
              <template v-if="!added(poi)"><Plus class="ic" :size="13" /> 加入</template>
              <template v-else>已加入</template>
            </button>
          </li>
        </ul>
        <p v-else class="reco__empty tiny muted">当前类目暂无推荐结果，可更换类目或排序方式。</p>

        <a
          v-if="amapUrl"
          class="reco__amap tiny"
          :href="amapUrl"
          target="_blank"
          rel="noopener"
        >
          <ExternalLink class="ic" :size="12" /> 在高德地图中查看更多
        </a>
      </template>
    </div>
  </div>
</template>

<style scoped>
.reco__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 12px 12px;
}

/* 说明行原来长在开关那一行上，现在开关并进了「添加地点」栏，它只能待在展开体里。
   上边距归它自己：head 没了，没人再替这段撑出呼吸。 */
.reco__lead {
  margin: 0;
  padding-top: 10px;
}

/* 没有城市那一档：说明与动作并排，键就长在句子末尾——只写「请先设置目的地城市」而不给入口，
   等于把用户留在原地猜按钮在哪。这一档不渲染 `.reco__lead`，10px 顶距得自己撑。 */
.reco__need {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 10px;
  align-items: center;
  justify-content: space-between;
  padding-top: 10px;
}

.reco__need-text {
  display: flex;
  flex: 1 1 220px;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.reco__need-title {
  font-size: calc(14px * var(--fs-scale));
  font-weight: 600;
  color: var(--text);
}

.reco__need-text p {
  margin: 0;
}

.reco__tabs {
  display: flex;
  gap: 6px;
}

.reco__tab {
  padding: 5px 11px;
  font-size: calc(13px * var(--fs-scale));
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
}

.reco__tab--on {
  color: var(--accent-strong);
  background: var(--accent-soft);
  border-color: var(--accent);
}

/* 基准点候选数量不定、还要按天分组，自绘下拉得自己接管键盘与焦点，性价比为负，这里破例用原生
   select，只把它拉回 token 体系的字号与颜色（Windows 上原生 select 默认 13.33px + 系统灰字）。 */
.reco__origin {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reco__select {
  width: 100%;
  font-size: calc(13px * var(--fs-scale));
  font-weight: 600;
  color: var(--text);
}

.reco__note {
  margin: 0;
}

/* 与「添加地点」栏第一行之间的界线：整块只有一个描边，内部靠这条细线分层。 */
.reco {
  border-top: 1px solid var(--border);
}

.reco__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.reco__item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
}

.reco__photo {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 10px;
  background: var(--surface-2);
}

.reco__item:hover {
  background: var(--surface-2);
}

.reco__info {
  flex: 1;
  min-width: 0;
}

.reco__name {
  font-size: calc(14px * var(--fs-scale));
  font-weight: 600;
}

.reco__addr {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reco__empty {
  padding: 10px 12px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}

/* ---------- 手机 ---------- */
@media (max-width: 860px) {
  /* 展开体以前不限高：十几条结果把当天的行程整个推出视口，「看一眼推荐」
     变成「滚回行程」。列表自己限高内滚，外层那一栏不再被撑长。 */
  .reco__list {
    max-height: 42dvh;
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .reco__tab {
    min-height: 34px;
    padding: 5px 12px;
  }

  .reco__need-text {
    flex-basis: 100%;
  }
}

@media (hover: none) {
  .reco__item:hover {
    background: transparent;
  }
}

.reco__amap {
  color: var(--accent);
  text-decoration: none;
}
.reco__amap:hover {
  text-decoration: underline;
}
</style>
