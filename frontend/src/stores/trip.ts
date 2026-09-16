import { acceptHMRUpdate, defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type {
  ChecklistItem,
  Day,
  DayTimeline,
  Expense,
  Participant,
  Place,
  PlaceCreateInput,
  PlaceStatus,
  Presence,
  Snapshot,
  StashItem,
  Trip,
} from '@/types/domain'
import type { OpBroadcastFrame } from '@/types/protocol'
import { Ops } from '@/types/protocol'
import { colorForClient, getClientId, useClientIdentity } from '@/composables/useClientIdentity'
import { apiFetch, postJson } from '@/utils/api'
import { formatMoney } from '@/utils/money'
import { useSocketStore } from '@/stores/socket'
import { useFeedbackStore } from '@/stores/feedback'

type PendingOp =
  | { kind: 'place_add'; tempId: string; restore?: PlaceRestore; cancelled?: boolean }
  /** 一批清单条目共用一条 op：整批的临时行要在回广播时一起清掉。 */
  | { kind: 'checklist_add'; tempIds: string[] }
  | { kind: 'expense_add'; tempId: string }
  /** 删除是本地先移除再发 op，回广播时那一行已经查不到了：名字只能暂存在这里。 */
  | { kind: 'deleted'; name: string }

/**
 * 撤销删除时要在 place_add 落地之后补发的第二笔。
 *
 * 为什么不能一次做完：新行的 id 是服务端给的，`place_update` / `place_lock` 都只认真 id，
 * 拿临时 id 去发只会收到一个 `place_not_found`。所以先记下要恢复什么，回执到了再发。
 */
export interface PlaceRestore {
  /** 只有原值与「新行的默认值」不同才带上；undefined 表示不动。 */
  patch: { start_min?: number; status?: PlaceStatus }
  /** 钉了位置但没钉时刻：只能靠 place_lock 恢复。 */
  lock: boolean
  /** 这一天原本从这个地点出发 / 收在这个地点：撤销后要把锚改指到新 id。 */
  startAnchor: boolean
  endAnchor: boolean
}

/** 删除地点前拍下的快照：够把这一行原样放回原位。 */
export interface DeletedPlace {
  /** 整行原样留着——撤销一条「刚加完还没落地」的行时要把乐观行放回去。 */
  row: Place
  /** 删的那一刻这一行还没有服务端身份（PLACE_ADD 的回执还在路上）。 */
  wasTemp: boolean
}

/** 删除回执里「撤销」能按多久。够读完一句话再抬手点一下，长过这个就该让屏幕安静下来。 */
const UNDO_MS = 5000

export interface OptimizeSummary {
  before_min: number
  after_min: number
  saved_min: number
}

export interface OptimizeResult {
  day_id: string
  place_ids: string[]
  prev_place_ids: string[]
  summary: OptimizeSummary
  /** 排程后的权威行（含 start_min/arrive_min/travel_min_before）。 */
  places: Place[]
  end_min: number
  warnings: string[]
  exact: boolean
}

/** op 流台账的一行。seq 原样带给界面看：「序号在涨」本身就是两端还在同步的证据。 */
export interface OpEvent {
  seq: number
  op: string
  /** 发起者的 client_id；服务端自己生成的那类（优化排程）为空。 */
  origin: string
  ts: number
  /** 「动词 + 对象」的一句话摘要，在行被覆盖或删除之前取好。 */
  text: string
}

/**
 * Trip state. The WebSocket pushes authoritative results into `applySnapshot` and
 * `applyRemoteOp`; every local mutation here has an optimistic path plus op_id echo
 * filtering. `applySnapshot` is the single authoritative-state entry point, so resync
 * and initial load share one code path.
 *
 * The three protocol rules encoded below:
 * 1. `rev` is a convergence stamp, not a CAS -- a remote row is applied iff its rev is
 *    strictly greater than the local one (or the id is unknown). Equal revs are already
 *    represented locally (by our own optimistic write).
 * 2. Reorder carries the full ordered id array, never index deltas.
 * 3. Ops broadcast results to everyone including the originator; `pendingOps` is how we
 *     tell "my echo" (confirm, do not re-apply) from "someone else's op".
 */
export const useTripStore = defineStore('trip', () => {
  const trip = ref<Trip | null>(null)
  const days = ref<Day[]>([])
  const places = ref<Place[]>([])
  const participants = ref<Participant[]>([])
  const presence = ref<Presence[]>([])
  const stash = ref<StashItem[]>([])
  const checklist = ref<ChecklistItem[]>([])
  const expenses = ref<Expense[]>([])
  const currentDayId = ref<string | null>(null)
  const selectedPlaceId = ref<string | null>(null)
  const loading = ref(false)
  const loadError = ref<{ message: string; hint: string } | null>(null)
  /** 最近一次写入失败的唯一真相；呈现交给 ToastHost。level 区分「你的数据没存进去」与
   * 「并发对手赢了、本地已收敛到权威值」——后者不是失败，不该用同一套告警色喊人。 */
  const opError = ref<{ message: string; hint: string; level: 'danger' | 'info' } | null>(null)
  const optimizing = ref(false)
  const optimizeResult = ref<OptimizeResult | null>(null)

  /**
   * day_id -> the server's schedule for that day. Populated by `timeline_updated` frames,
   * which follow every op that moves or re-times a place, and by optimize results. The
   * place rows inside are already in `places` (higher rev, since persist_schedule bumps
   * it); this map exists for the day-level numbers the rows cannot express.
   */
  const timelines = ref<Record<string, DayTimeline>>({})

  /** op_id -> optimistic bookkeeping (which temp rows a write created). */
  const pendingOps = new Map<string, PendingOp>()

  const currentDay = computed(
    () => days.value.find((d) => d.id === currentDayId.value) ?? null,
  )

  /** 当前天已安排的地点。 */
  const currentPlaces = computed(() =>
    places.value
      .filter((p) => p.day_id === currentDayId.value)
      .slice()
      .sort((a, b) => a.sort_index - b.sort_index),
  )

  const selectedPlace = computed(
    () => currentPlaces.value.find((p) => p.id === selectedPlaceId.value) ?? null,
  )

  /** 当天的排程结果（结束时刻、全程交通、警告）。只在收到过 timeline 帧后有值。 */
  const currentTimeline = computed(
    () => (currentDayId.value ? timelines.value[currentDayId.value] ?? null : null),
  )

  /** 排程提醒（「23:30 才结束」「固定时间早于预计到达」）。不点优化也看得到。 */
  const currentWarnings = computed(() => currentTimeline.value?.warnings ?? [])

  // -- M22 派生：出行清单与账本 ---------------------------------------------------------

  const checklistSorted = computed(() =>
    checklist.value
      .slice()
      .sort((a, b) => a.sort_index - b.sort_index),
  )
  const checklistDoneCount = computed(() => checklist.value.filter((i) => i.done).length)

  /** 服务端按 created_at 正序给出，面板要「最新在上」。
   * 这里只做反转，不按时间重排：乐观临时行的时间串与服务端格式不同，排序会把它塞错位置，
   * 而它 push 在末尾、反转后天然是最新一条——回广播来了再换成权威行，位置不变。 */
  const expensesNewestFirst = computed(() => expenses.value.slice().reverse())
  const spentCents = computed(() => expenses.value.reduce((sum, e) => sum + e.amount_cents, 0))

  function selectPlace(placeId: string | null) {
    selectedPlaceId.value = placeId
    if (trip.value) {
      useSocketStore().sendPresence(currentDayId.value, placeId)
    }
  }

  function applySnapshot(snap: Snapshot) {
    trip.value = snap.trip
    days.value = snap.days
    places.value = snap.places
    participants.value = snap.participants
    presence.value = snap.presence ?? []
    stash.value = snap.stash ?? []
    checklist.value = snap.checklist ?? []
    expenses.value = snap.expenses ?? []
    // 台账只记这一页亲眼看到的广播：换行程与重连都会重新 welcome，
    // 留着上一段行程的 seq 等于把别处的改动报成本页刚发生的。
    opLog.value = []
    const alive = new Set(snap.days.map((d) => d.id))
    timelines.value = Object.fromEntries(
      Object.entries(timelines.value).filter(([dayId]) => alive.has(dayId)),
    )
    if (!currentDayId.value || !snap.days.some((d) => d.id === currentDayId.value)) {
      currentDayId.value = snap.days[0]?.id ?? null
    }
  }

  async function load(tripId: string) {
    loading.value = true
    loadError.value = null
    try {
      applySnapshot(await apiFetch<Snapshot>(`/api/trips/${encodeURIComponent(tripId)}`))
      void registerSelf()
    } catch (err) {
      loadError.value = describe(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * Announce this tab as a participant. Fire-and-forget: an offline backend must not
   * block the trip from rendering, and the hello frame supersedes the REST registration.
   */
  async function registerSelf() {
    if (!trip.value) return
    const identity = useClientIdentity()
    try {
      const me = await apiFetch<Participant>(
        `/api/trips/${trip.value.id}/participants`,
        postJson({
          client_id: identity.client_id,
          name: identity.name,
          color: colorForClient(identity.client_id),
        }),
      )
      upsertParticipant(me)
    } catch {
      // Presence stays empty until the WebSocket takes over; not worth a banner.
    }
  }

  function upsertParticipant(participant: Participant) {
    const index = participants.value.findIndex((p) => p.client_id === participant.client_id)
    if (index === -1) participants.value.push(participant)
    else participants.value[index] = participant
  }

  // -- outbound mutations (optimistic) -------------------------------------------------

  /** Add a place. Optimistically inserts a temp row; the echo (or another client's
   * place_added) replaces it with the server's authoritative row.
   * `forDayId` overrides the target day -- the assistant says which day it parsed. */
  function addPlace(
    input: PlaceCreateInput,
    forDayId?: string | null,
    restore?: PlaceRestore,
  ): Place | null {
    const dayId = forDayId || currentDayId.value
    if (!dayId || !trip.value) return null

    const tempId = `tmp-${crypto.randomUUID()}`
    const maxIndex = places.value.reduce(
      (max, p) => (p.day_id === dayId ? Math.max(max, p.sort_index) : max),
      -1,
    )
    // 撤销删除会指定 position：乐观行照着它落位，否则本地先插到末尾、回执到了再跳回去。
    const sortIndex = input.position ?? maxIndex + 1
    // 恢复时刻的那一笔要等真 id 才发得出去，但卡片不能先演一遍「没钉住」再钉上。
    const pin = restore?.patch.start_min
    const optimistic: Place = {
      id: tempId,
      day_id: dayId,
      trip_id: trip.value.id,
      sort_index: sortIndex,
      name: input.name,
      amap_poi_id: input.amap_poi_id ?? '',
      address: input.address ?? '',
      photo_url: input.photo_url ?? '',
      lng: input.lng,
      lat: input.lat,
      duration_min: input.duration_min ?? 60,
      user_start_min: pin ?? null,
      start_min: pin ?? null,
      arrive_min: null,
      travel_min_before: null,
      locked: pin !== undefined ? true : (restore?.lock ?? false),
      status: restore?.patch.status ?? 'pending',
      note: input.note ?? '',
      added_by: input.added_by || useClientIdentity().name,
      rev: 1,
      created_at: '',
      updated_at: '',
    }
    if (input.position !== null && input.position !== undefined) {
      // 服务端会把这一位之后的行整体后移一位，本地跟着做，免得两行抢同一个 sort_index。
      for (const p of places.value) {
        if (p.day_id === dayId && p.sort_index >= sortIndex) p.sort_index += 1
      }
    }
    places.value.push(optimistic)

    const opId = useSocketStore().sendOp(Ops.PLACE_ADD, { day_id: dayId, ...input })
    pendingOps.set(opId, { kind: 'place_add', tempId, ...(restore ? { restore } : {}) })
    return optimistic
  }

  /** Field-level edit: apply locally now, let the server echo confirm. LWW means the
   * last patch wins per field; different fields on the same card never clobber. */
  function updatePlace(placeId: string, patch: Partial<Place>) {
    const place = places.value.find((p) => p.id === placeId)
    if (!place) return
    const allowed: (keyof Place)[] = ['name', 'address', 'duration_min', 'start_min', 'note', 'status']
    for (const [key, value] of Object.entries(patch)) {
      if (allowed.includes(key as keyof Place)) {
        ;(place as Record<string, unknown>)[key] = value
      }
    }
    // rev is NOT bumped locally: the server is its only authority. A local bump would
    // collide with the server's rev for this very op, and the echo (which may carry
    // server-side side effects like the time->lock pin) would then be dropped by the
    // rev guard. Echo rev+1 > local rev applies cleanly.
    //
    // 「设置即锁定」必须在本地一起镜像：服务端收到 start_min 会连带写 user_start_min 与
    // locked。少了这一步，一张卡会同时给出三个答案——轨道读 user_start_min、开关读
    // locked、读数条读 start_min，于是「解锁之后轨道还停在旧时刻」这类自相矛盾。
    if ('start_min' in patch) {
      const pinned = patch.start_min ?? null
      place.user_start_min = pinned
      place.locked = pinned !== null
    }
    useSocketStore().sendOp(Ops.PLACE_UPDATE, { place_id: placeId, patch })
  }

  /** Pin a place: locked rows are anchors the optimizer routes around. */
  function setPlaceLocked(placeId: string, locked: boolean) {
    const place = places.value.find((p) => p.id === placeId)
    if (!place) return
    place.locked = locked
    // 解锁的同一笔里服务端会清掉 user_start_min（钉住可以只钉位置，放开则时刻一并交回
    // 排程），所以这里必须跟着清，否则轨道会留着一个已经不存在的钉法。
    if (!locked) place.user_start_min = null
    useSocketStore().sendOp(Ops.PLACE_LOCK, { place_id: placeId, locked })
  }

  /**
   * 撤销删除的第二笔：新行的 id 是服务端给的，拿到之后才能把时刻、状态、钉住与锚补回去。
   * 带时刻的那一笔会连带镜像出 locked（设置即锁定），所以只在「钉位置不钉时间」时另发 place_lock。
   */
  function applyPlaceRestore(place: Place, restore: PlaceRestore) {
    if (Object.keys(restore.patch).length) updatePlace(place.id, restore.patch)
    if (restore.lock) setPlaceLocked(place.id, true)
    // 撤销是「重新加一行」，id 换了：原来指着旧 id 的起点/终点锚得跟着改指。
    if (restore.startAnchor) setStartPlace(place.day_id, place.id)
    if (restore.endAnchor) setEndPlace(place.day_id, place.id)
  }

  /**
   * 删除一个地点，并回一份够把它原样放回去的快照。
   *
   * 临时 id 的行不能发 `place_delete`：服务端还不认它，只会回一个 `place_not_found`，
   * 而那一笔还在路上的 `place_add` 落地时又把行送回来——「删了又自己长回来」。
   * 所以给它立个墓碑，等回执到了反过来补一发删除（见 applyRemoteOp）。
   */
  function deletePlace(placeId: string): DeletedPlace | null {
    const place = places.value.find((p) => p.id === placeId)
    if (!place) return null
    const row: Place = { ...place }
    places.value = places.value.filter((p) => p.id !== placeId)
    if (selectedPlaceId.value === placeId) selectedPlaceId.value = null
    const wasTemp = placeId.startsWith('tmp-')
    if (wasTemp) {
      cancelPendingAdd(placeId)
    } else {
      const opId = useSocketStore().sendOp(Ops.PLACE_DELETE, { place_id: placeId })
      pendingOps.set(opId, { kind: 'deleted', name: row.name })
    }
    return { row, wasTemp }
  }

  /** 给还在等回执的那笔 place_add 立墓碑；找不到（早落地了）就不立。 */
  function cancelPendingAdd(tempId: string): boolean {
    for (const pending of pendingOps.values()) {
      if (pending.kind === 'place_add' && pending.tempId === tempId) {
        pending.cancelled = true
        return true
      }
    }
    return false
  }

  function erasePendingAddTombstone(tempId: string): boolean {
    for (const pending of pendingOps.values()) {
      if (pending.kind === 'place_add' && pending.tempId === tempId) {
        if (!pending.cancelled) return false
        pending.cancelled = false
        return true
      }
    }
    return false
  }

  /** 从原行算出「落地后要补的第二笔」；没什么要补的就返回 undefined，免得回执处空转。 */
  function restoreOf(row: Place): PlaceRestore | undefined {
    const patch: PlaceRestore['patch'] = {}
    // 新行的默认值是 status='pending'、时刻交给排程重算，只有原值不同才值得再发一笔。
    if (row.status !== 'pending') patch.status = row.status
    // 「设置即锁定」：带 user_start_min 的补丁会把时刻与钉住一起写回。
    if (row.user_start_min !== null) patch.start_min = row.user_start_min
    const lock = row.locked && row.user_start_min === null
    const day = days.value.find((d) => d.id === row.day_id)
    const startAnchor = day?.start_place_id === row.id || false
    const endAnchor = day?.end_place_id === row.id || false
    if (!Object.keys(patch).length && !lock && !startAnchor && !endAnchor) return undefined
    return { patch, lock, startAnchor, endAnchor }
  }

  /**
   * 撤销一次删除。
   *
   * 两条路：那一行如果到现在都还没落地（墓碑还在），把乐观行原样放回去就是完整的撤销；
   * 否则重新加一遍——内容、所在天、在原天里的位次都按快照复原，时刻与状态等第二笔补发。
   */
  function restorePlace(snap: DeletedPlace) {
    const row = snap.row
    if (snap.wasTemp && erasePendingAddTombstone(row.id)) {
      places.value = [...places.value, row]
      return
    }
    const day = days.value.find((d) => d.id === row.day_id)
    if (!day) {
      // 撤销窗口里那一天被删了：与其插一个服务端不认的行，不如说清楚为什么放不回去。
      useFeedbackStore().show({
        kind: 'danger',
        message: `无法放回「${row.name}」`,
        hint: '那一天已经被删除',
      })
      return
    }
    addPlace(
      {
        name: row.name,
        lng: row.lng,
        lat: row.lat,
        address: row.address,
        amap_poi_id: row.amap_poi_id,
        photo_url: row.photo_url,
        duration_min: row.duration_min,
        note: row.note,
        added_by: row.added_by,
        position: row.sort_index,
      },
      row.day_id,
      restoreOf(row),
    )
  }

  /** 删除地点的界面入口：删完给一条带「撤销」的回执（S2）。 */
  function deletePlaceWithUndo(placeId: string) {
    const snap = deletePlace(placeId)
    if (!snap) return
    useFeedbackStore().show({
      message: `已删除「${snap.row.name}」`,
      durationMs: UNDO_MS,
      action: { label: '撤销', run: () => restorePlace(snap) },
    })
  }

  /** Drag result: send the FULL ordered id array (never a delta) and apply optimistically. */
  function reorderDay(dayId: string, orderedIds: string[]) {
    applyOrder(dayId, orderedIds)
    useSocketStore().sendOp(Ops.DAY_REORDER, { day_id: dayId, place_ids: orderedIds })
  }

  /**
   * 上移 / 下移一位。拖动是手上的事，键盘和误触得有第二条路；两条路走的是同一个
   * `DAY_REORDER`（这里给的仍是整天完整的序列，不是位移量），所以收敛行为一致。
   */
  function nudgePlace(dayId: string, placeId: string, delta: number) {
    const ordered = places.value
      .filter((p) => p.day_id === dayId)
      .slice()
      .sort((a, b) => a.sort_index - b.sort_index)
      .map((p) => p.id)
    const from = ordered.indexOf(placeId)
    const to = from + delta
    if (from < 0 || to < 0 || to >= ordered.length) return
    ordered.splice(to, 0, ordered.splice(from, 1)[0])
    reorderDay(dayId, ordered)
  }

  function updateTripFields(patch: Partial<Trip>) {
    if (trip.value) Object.assign(trip.value, patch)
    useSocketStore().sendOp(Ops.TRIP_UPDATE, { patch })
  }

  /** Append a new day to the trip. `title`/`date` are optional; the server appends after
   * the last day and works out the date when none is given. */
  function addDay(title = '', date?: string | null) {
    useSocketStore().sendOp(Ops.DAY_ADD, { title, date: date ?? null })
  }

  /** 删除一个空的天（服务端拒绝有内容的天与最后一天）。 */
  function deleteDay(dayId: string) {
    useSocketStore().sendOp(Ops.DAY_DELETE, { day_id: dayId })
  }

  /** 设为起点：优化器把这一天从这个地点出发。传空即清除锚点（NULL，不是空串）。 */
  function setStartPlace(dayId: string, placeId: string | null) {
    useSocketStore().sendOp(Ops.DAY_UPDATE, {
      day_id: dayId,
      patch: { start_place_id: placeId || null },
    })
  }

  /** 设为终点：优化器把这一天收在这个地点，排程给出「几点到」。 */
  function setEndPlace(dayId: string, placeId: string | null) {
    useSocketStore().sendOp(Ops.DAY_UPDATE, {
      day_id: dayId,
      patch: { end_place_id: placeId || null },
    })
  }

  /** Rename a day (or set its date/mode later -- full patch goes through the op). */
  function updateDay(dayId: string, patch: Partial<Day>) {
    useSocketStore().sendOp(Ops.DAY_UPDATE, { day_id: dayId, patch })
  }

  // -- stash（想去清单）------------------------------------------------------------------

  interface StashAddInput {
    name: string
    lng: number
    lat: number
    address?: string
    amap_poi_id?: string
    photo_url?: string
  }

  /** 收进想去清单。条目 id 由服务端生成，靠 stash_added 回广播落地。 */
  function stashAdd(input: StashAddInput) {
    useSocketStore().sendOp(Ops.STASH_ADD, {
      ...input,
      photo_url: input.photo_url ?? '',
      added_by: useClientIdentity().name,
    })
  }

  function stashRemove(id: string) {
    stash.value = stash.value.filter((s) => s.id !== id)
    useSocketStore().sendOp(Ops.STASH_REMOVE, { id })
  }

  /** 把清单里的一条安排进当前天：加为地点 + 移出清单，两个 op 各自回广播。 */
  function promoteFromStash(item: StashItem) {
    addPlace({
      name: item.name,
      lng: item.lng,
      lat: item.lat,
      address: item.address,
      amap_poi_id: item.amap_poi_id,
      photo_url: item.photo_url,
    })
    stashRemove(item.id)
  }

  /**
   * 跨天移动。`beforePlaceId` 给的是「插到目标天哪一个地点前面」，不给或认不出就是末尾。
   *
   * 协议里没有「移到某天第几位」这一发 op，所以这里是两条既有 op 的组合：先 `place_move`
   * （服务端把它落到目标天末尾，并回广播两天的权威顺序），再补一条 `day_reorder` 挪到指定位。
   * 顺序不能反：reorder 的置换校验按当前库内容判，先重排再挪天必然被判 order_stale。
   * 别人在这一瞬并发改了目标天时，第二条会拿到 order_stale —— 那是按权威顺序收敛，不是丢数据。
   */
  function movePlaceToDay(placeId: string, toDayId: string, beforePlaceId?: string | null) {
    const place = places.value.find((p) => p.id === placeId)
    if (!place || place.day_id === toDayId) return
    removeLocalRow(placeId)
    const ordered = places.value.filter((p) => p.day_id === toDayId).map((p) => p.id)
    const at = beforePlaceId ? ordered.indexOf(beforePlaceId) : -1
    if (at >= 0) ordered.splice(at, 0, placeId)
    else ordered.push(placeId)
    places.value.push({ ...place, day_id: toDayId, sort_index: 0 })
    applyOrder(toDayId, ordered)
    const socket = useSocketStore()
    socket.sendOp(Ops.PLACE_MOVE, { place_id: placeId, day_id: toDayId })
    if (at >= 0) socket.sendOp(Ops.DAY_REORDER, { day_id: toDayId, place_ids: ordered })
  }

  // -- checklist（出行清单）--------------------------------------------------------------

  /**
   * 一次加一条或一批。整批走一条 op：一条一条发会把 seq 打成一串，别人端上看着像
   * 有人连点了十几次「添加」。去重由服务端负责，被它丢掉的临时行在回广播时一起清掉。
   */
  function checklistAdd(texts: string[]) {
    if (!trip.value) return
    const tripId = trip.value.id
    const clean = texts.map((t) => t.trim().slice(0, 120)).filter(Boolean)
    if (!clean.length) return
    const me = useClientIdentity()
    const maxIndex = checklist.value.reduce((max, i) => Math.max(max, i.sort_index), -1)
    const now = new Date().toISOString()
    const tempIds: string[] = []
    for (const [offset, text] of clean.entries()) {
      const tempId = `tmp-${crypto.randomUUID()}`
      tempIds.push(tempId)
      checklist.value.push({
        id: tempId,
        trip_id: tripId,
        sort_index: maxIndex + 1 + offset,
        text,
        done: false,
        added_by: me.name,
        rev: 1,
        created_at: now,
        updated_at: now,
      })
    }
    const opId = useSocketStore().sendOp(Ops.CHECKLIST_ADD, { texts: clean, added_by: me.name })
    pendingOps.set(opId, { kind: 'checklist_add', tempIds })
  }

  /** 改名 / 打勾共用一条 patch 通道（服务端白名单只认 text 与 done）。 */
  function updateChecklist(id: string, patch: Partial<Pick<ChecklistItem, 'text' | 'done'>>) {
    const item = checklist.value.find((i) => i.id === id)
    if (!item || !Object.keys(patch).length) return
    Object.assign(item, patch)
    // 本地不动 rev：与服务端撞号会被 echo 的 rev 守卫丢掉（同 updatePlace）。
    useSocketStore().sendOp(Ops.CHECKLIST_UPDATE, { id, patch })
  }

  function removeChecklist(id: string) {
    const item = checklist.value.find((i) => i.id === id)
    checklist.value = checklist.value.filter((i) => i.id !== id)
    const opId = useSocketStore().sendOp(Ops.CHECKLIST_DELETE, { id })
    if (item) pendingOps.set(opId, { kind: 'deleted', name: item.text })
  }

  /** 拖动结果：发整条有序 id 数组，与 day_reorder 同一套规矩（不发送增量）。 */
  function reorderChecklist(orderedIds: string[]) {
    applyChecklistOrder(orderedIds)
    useSocketStore().sendOp(Ops.CHECKLIST_REORDER, { item_ids: orderedIds })
  }

  // -- expenses（费用与 AA）--------------------------------------------------------------

  interface ExpenseAddInput {
    title: string
    amount_cents: number
    category?: string
    /** 分摊名单（client_id）。留空 = 只有付款人自己，服务端同样这么兜。 */
    split_ids?: string[]
  }

  function addExpense(input: ExpenseAddInput) {
    if (!trip.value) return
    const title = input.title.trim().slice(0, 80)
    if (!title || input.amount_cents <= 0) return
    const me = useClientIdentity()
    const splits = input.split_ids?.length ? input.split_ids : [me.client_id]
    const now = new Date().toISOString()
    const tempId = `tmp-${crypto.randomUUID()}`
    expenses.value.push({
      id: tempId,
      trip_id: trip.value.id,
      title,
      amount_cents: input.amount_cents,
      category: input.category ?? 'other',
      paid_by: me.client_id,
      paid_by_name: me.name,
      split_ids: splits,
      created_at: now,
      updated_at: now,
      rev: 1,
    })
    const opId = useSocketStore().sendOp(Ops.EXPENSE_ADD, {
      title,
      amount_cents: input.amount_cents,
      category: input.category ?? 'other',
      paid_by: me.client_id,
      paid_by_name: me.name,
      split_ids: splits,
    })
    pendingOps.set(opId, { kind: 'expense_add', tempId })
  }

  function updateExpense(
    id: string,
    patch: Partial<Pick<Expense, 'title' | 'amount_cents' | 'category' | 'split_ids'>>,
  ) {
    const expense = expenses.value.find((e) => e.id === id)
    if (!expense || !Object.keys(patch).length) return
    Object.assign(expense, patch)
    useSocketStore().sendOp(Ops.EXPENSE_UPDATE, { id, patch })
  }

  function removeExpense(id: string) {
    const expense = expenses.value.find((e) => e.id === id)
    expenses.value = expenses.value.filter((e) => e.id !== id)
    const opId = useSocketStore().sendOp(Ops.EXPENSE_DELETE, { id })
    if (expense) pendingOps.set(opId, { kind: 'deleted', name: expense.title })
  }


  // -- optimization --------------------------------------------------------------------

  /** Run the optimizer over one day over HTTP (it can take seconds on a cold cache);
   * the resulting order/schedule also arrives as a route_optimized broadcast for
   * everyone else in the room. Day defaults to the selected one (section footers pass
   * their own dayId so any expanded day can optimize without being selected). */
  async function optimize(
    costModel: 'haversine' | 'amap' = 'haversine',
    dayId: string | null = null,
  ): Promise<void> {
    const id = dayId ?? currentDayId.value
    if (!id || !trip.value || optimizing.value) return
    optimizing.value = true
    opError.value = null
    try {
      const result = await apiFetch<OptimizeResult>(
        `/api/trips/${trip.value.id}/days/${id}/optimize`,
        postJson({ cost_model: costModel, apply: true }),
      )
      applyOptimizeResult(result)
    } catch (err) {
      const e = err as { message?: string; hint?: string }
      opError.value = { message: e?.message ?? '优化失败', hint: e?.hint ?? '', level: 'danger' }
    } finally {
      optimizing.value = false
    }
  }

  function applyOptimizeResult(result: OptimizeResult) {
    applyOrder(result.day_id, result.place_ids)
    for (const place of result.places ?? []) upsertPlaceIfNewer(place)
    optimizeResult.value = result
    // The optimize response is authoritative for the day it ran on: seed the schedule
    // map exactly like a timeline_updated frame, so 结束时间/警告 appear without
    // waiting for (or depending on) the route_optimized broadcast echo.
    timelines.value = {
      ...timelines.value,
      [result.day_id]: {
        day_id: result.day_id,
        places: result.places ?? [],
        end_min: result.end_min ?? 0,
        travel_min: (result.places ?? []).reduce((sum, p) => sum + (p.travel_min_before ?? 0), 0),
        warnings: result.warnings ?? [],
        exact: result.exact ?? false,
      },
    }
  }

  /** One-click undo: replay the previous order through the normal reorder op. */
  function undoOptimize() {
    const result = optimizeResult.value
    if (!result) return
    reorderDay(result.day_id, result.prev_place_ids)
    optimizeResult.value = null
  }

  function dismissOptimizeResult() {
    optimizeResult.value = null
  }

  // -- inbound results -----------------------------------------------------------------

  /** 台账只留最近这些条：它回答的是「刚刚发生了什么」，不是审计日志。 */
  const OP_LOG_MAX = 24

  /** 服务端广播的是过去式的结果名，与客户端发出的 Ops.* 差一个 _ed。 */
  const OP_LABELS: Record<string, string> = {
    place_added: '添加地点',
    place_updated: '修改地点',
    place_locked: '改固定状态',
    place_deleted: '删除地点',
    place_moved: '移动地点',
    day_reordered: '重排顺序',
    day_added: '新增一天',
    day_deleted: '删除一天',
    day_updated: '修改这一天',
    trip_updated: '修改行程',
    stash_added: '收进想去',
    stash_removed: '移出想去',
    checklist_added: '添加待办',
    checklist_updated: '修改待办',
    checklist_deleted: '删除待办',
    checklist_reordered: '重排清单',
    expense_added: '记一笔',
    expense_updated: '修改一笔',
    expense_deleted: '删除一笔',
    route_optimized: '优化排程',
  }

  const opLog = ref<OpEvent[]>([])

  function dayLabel(dayId: string, fallbackIndex?: number): string {
    const day = days.value.find((d) => d.id === dayId)
    const index = day?.day_index ?? fallbackIndex
    if (index === undefined) return ''
    return `D${index + 1}`
  }

  /** 摘要里的对象名：能从 payload 拿就从它拿，拿不到才回本地行查（删除类只有这一条路）。
   * deletedName 是发起方在本地移除前暂存的那一份——删除类广播里只有 id，别人那端还能查到行，
   * 自己这端查不到。 */
  function opSubject(op: string, data: Record<string, unknown>, deletedName = ''): string {
    const nameOf = (row: unknown) => {
      const name = (row as { name?: unknown } | undefined)?.name
      return typeof name === 'string' ? name : ''
    }
    switch (op) {
      case 'place_added':
      case 'place_updated':
      case 'place_locked':
      case 'place_moved':
        return nameOf(data.place)
      case 'place_deleted':
        return places.value.find((p) => p.id === data.place_id)?.name ?? deletedName
      case 'day_reordered':
      case 'day_deleted':
        return dayLabel(String(data.day_id ?? ''))
      // 这两条广播只带整行，没有顶层 day_id。
      case 'day_updated':
      case 'day_added': {
        const row = data.day as Day | undefined
        return dayLabel(String(row?.id ?? ''), row?.day_index)
      }
      case 'checklist_added':
        return `${((data.items as ChecklistItem[] | undefined) ?? []).length} 项`
      case 'checklist_updated':
        return String((data.item as ChecklistItem | undefined)?.text ?? '')
      case 'checklist_deleted':
        return checklist.value.find((i) => i.id === data.id)?.text ?? deletedName
      case 'expense_added':
      case 'expense_updated': {
        const expense = data.expense as Expense | undefined
        return expense ? `${expense.title} ${formatMoney(expense.amount_cents)}` : ''
      }
      case 'expense_deleted':
        return expenses.value.find((e) => e.id === data.id)?.title ?? deletedName
      case 'stash_added':
        return nameOf(data.item)
      case 'route_optimized':
        return dayLabel(String(data.day_id ?? ''))
      default:
        return ''
    }
  }

  /** 记一条广播结果。放在 switch 之前调用：删除与覆盖类的名字只有那一刻还拿得到。 */
  function logOp(frame: OpBroadcastFrame) {
    const label = OP_LABELS[frame.op]
    if (!label) return
    const data = frame.data as Record<string, unknown>
    const pending = pendingOps.get(frame.op_id)
    const subject = opSubject(frame.op, data, pending?.kind === 'deleted' ? pending.name : '')
    opLog.value = [
      {
        seq: frame.seq,
        op: frame.op,
        origin: frame.origin,
        ts: frame.ts,
        text: subject ? `${label} ${subject}` : label,
      },
      ...opLog.value,
    ].slice(0, OP_LOG_MAX)
  }

  /** The echo or another client's op. `pendingOps` distinguishes the two. */
  function applyRemoteOp(frame: OpBroadcastFrame) {
    const data = frame.data as Record<string, unknown>
    opError.value = null
    logOp(frame)

    switch (frame.op) {
      case 'place_added': {
        const place = data.place as Place
        const pending = pendingOps.get(frame.op_id)
        pendingOps.delete(frame.op_id)
        if (pending?.kind === 'place_add' && pending.cancelled) {
          // 这一行在等回执期间就被删掉了：不要插进来，反过来补一发删除把它在服务器上收掉。
          // 位次交给紧随其后的 place_deleted 广播纠正。
          useSocketStore().sendOp(Ops.PLACE_DELETE, { place_id: place.id })
          break
        }
        if (pending?.kind === 'place_add') {
          removeLocalRow(pending.tempId)
        }
        upsertPlaceIfNewer(place)
        applyOrder(String(data.day_id), data.place_ids as string[])
        if (pending?.kind === 'place_add' && pending.restore) {
          applyPlaceRestore(place, pending.restore)
        }
        break
      }
      case 'place_updated':
      case 'place_locked': {
        pendingOps.delete(frame.op_id)
        upsertPlaceIfNewer(data.place as Place)
        break
      }
      case 'place_deleted': {
        pendingOps.delete(frame.op_id)
        removeLocalRow(String(data.place_id))
        applyOrder(String(data.day_id), data.place_ids as string[])
        break
      }
      case 'day_reordered': {
        pendingOps.delete(frame.op_id)
        applyOrder(String(data.day_id), data.place_ids as string[])
        break
      }
      case 'day_added': {
        const day = data.day as Day
        if (!days.value.some((d) => d.id === day.id)) days.value.push(day)
        break
      }
      case 'day_deleted': {
        pendingOps.delete(frame.op_id)
        const removedId = String(data.day_id)
        days.value = days.value.filter((d) => d.id !== removedId)
        places.value = places.value.filter((p) => p.day_id !== removedId)
        if (currentDayId.value === removedId) {
          currentDayId.value = days.value[0]?.id ?? null
        }
        break
      }
      case 'place_moved': {
        pendingOps.delete(frame.op_id)
        const place = data.place as Place
        // 权威结果直接覆盖：两天的顺序都由服务端给出。
        places.value = places.value.filter((p) => p.id !== place.id)
        places.value.push(place)
        applyOrder(String(data.old_day_id), data.old_place_ids as string[])
        applyOrder(String(data.day_id), data.place_ids as string[])
        break
      }
      case 'day_updated': {
        const day = data.day as Day
        const index = days.value.findIndex((d) => d.id === day.id)
        if (index !== -1 && days.value[index].rev <= day.rev) days.value[index] = day
        break
      }
      case 'trip_updated': {
        trip.value = data.trip as Trip
        break
      }
      case 'stash_added': {
        const item = data.item as StashItem
        if (!stash.value.some((s) => s.id === item.id)) stash.value.push(item)
        break
      }
      case 'stash_removed': {
        const id = String(data.id)
        stash.value = stash.value.filter((s) => s.id !== id)
        pendingOps.delete(frame.op_id)
        break
      }
      case 'checklist_added': {
        const pending = pendingOps.get(frame.op_id)
        pendingOps.delete(frame.op_id)
        if (pending?.kind === 'checklist_add') {
          dropChecklist(pending.tempIds)
        }
        for (const item of (data.items ?? []) as ChecklistItem[]) upsertChecklistIfNewer(item)
        applyChecklistOrder((data.item_ids ?? []) as string[])
        break
      }
      case 'checklist_updated': {
        pendingOps.delete(frame.op_id)
        upsertChecklistIfNewer(data.item as ChecklistItem)
        break
      }
      case 'checklist_deleted': {
        pendingOps.delete(frame.op_id)
        dropChecklist([String(data.id)])
        break
      }
      case 'checklist_reordered': {
        pendingOps.delete(frame.op_id)
        applyChecklistOrder((data.item_ids ?? []) as string[])
        break
      }
      case 'expense_added': {
        const pending = pendingOps.get(frame.op_id)
        pendingOps.delete(frame.op_id)
        if (pending?.kind === 'expense_add') dropExpense(pending.tempId)
        upsertExpenseIfNewer(data.expense as Expense)
        break
      }
      case 'expense_updated': {
        pendingOps.delete(frame.op_id)
        upsertExpenseIfNewer(data.expense as Expense)
        break
      }
      case 'expense_deleted': {
        pendingOps.delete(frame.op_id)
        dropExpense(String(data.id))
        break
      }
      case 'timeline_updated': {
        // Follows every schedule-affecting op (and greets a joining client). Rows ride
        // the normal rev guard -- a dry-run join frame carries equal revs and only
        // refreshes the day-level numbers below.
        for (const t of (data.timelines ?? []) as DayTimeline[]) applyTimelineSummary(t)
        break
      }
      case 'route_optimized': {
        // Someone else ran the optimizer (or we did, via HTTP): converge on the
        // authoritative order and schedule. The op_id is server-generated, so every
        // client takes this branch -- applying it twice is idempotent.
        applyOptimizeResult(data as unknown as OptimizeResult)
        break
      }
      default:
        break
    }
  }

  function applyReject(opId: string, reason: string, data: Record<string, unknown>) {
    const pending = pendingOps.get(opId)
    pendingOps.delete(opId)

    if (reason === 'order_stale') {
      // Converge to the authoritative array the server attached to the rejection.
      applyOrder(String(data.day_id), data.place_ids as string[])
      opError.value = { message: '顺序已被其他成员调整，已同步至最新', hint: '', level: 'info' }
      return
    }
    if (reason === 'checklist_stale') {
      applyChecklistOrder((data.item_ids ?? []) as string[])
      opError.value = {
        message: '清单顺序已被其他成员调整，已同步至最新',
        hint: '',
        level: 'info',
      }
      return
    }
    if (pending?.kind === 'place_add') {
      removeLocalRow(pending.tempId)
    }
    if (pending?.kind === 'checklist_add') {
      dropChecklist(pending.tempIds)
    }
    if (pending?.kind === 'expense_add') {
      dropExpense(pending.tempId)
    }
    const messages: Record<string, string> = {
      place_not_found: '该地点已被删除',
      day_not_found: '目标天不存在',
      day_not_empty: '这一天还有地点没删掉，先把它们移到别的天',
      day_last: '行程至少保留一天，最后一天不能删',
      bad_patch: '修改内容无效',
      bad_payload: '提交的内容无效',
      stash_not_found: '该想去地点已被删除',
      checklist_not_found: '该清单项已被删除',
      expense_not_found: '该笔开销已被删除',
      bad_expense: '记录添加失败：标题或金额无效',
      op_failed: '服务端处理这一步时出了错，请重试',
    }
    opError.value = {
      // 认不出的 reason 不把内部标识拼上界面（用户读不懂 `place_not_found`）。
      message: messages[reason] ?? '这一步没有保存，请重试',
      hint: '',
      level: 'danger',
    }
  }

  function clearPendingOps() {
    // A fresh snapshot supersedes every optimistic guess; temp rows would linger.
    places.value = places.value.filter((p) => !p.id.startsWith('tmp-'))
    checklist.value = checklist.value.filter((i) => !i.id.startsWith('tmp-'))
    expenses.value = expenses.value.filter((e) => !e.id.startsWith('tmp-'))
    // 例外：等回执期间被删掉的那笔 place_add。它还排在 socket 的离线队列里，重连之后
    // 照样会落地——墓碑跟着一起清，删掉的地点就会在重连后自己长回来。
    const tombstones = [...pendingOps].filter(
      ([, p]) => p.kind === 'place_add' && p.cancelled,
    )
    pendingOps.clear()
    for (const [opId, pending] of tombstones) pendingOps.set(opId, pending)
  }

  // -- presence ------------------------------------------------------------------------

  /** 谁（非自己）正在拖动哪一天：day_id -> 提示信息。天区块头各自展示自己那天的。 */
  const draggersByDay = computed(() => {
    const map = new Map<string, { name: string; color: string }>()
    for (const p of presence.value) {
      if (p.client_id === getClientId() || !p.dragging_day_id) continue
      map.set(p.dragging_day_id, { name: p.name, color: p.color })
    }
    return map
  })

  function applyPresence(p: Presence) {
    if (!p?.client_id) return
    const index = presence.value.findIndex((x) => x.client_id === p.client_id)
    if (index === -1) presence.value.push(p)
    else presence.value[index] = p
  }

  function removePresence(clientId: string) {
    presence.value = presence.value.filter((x) => x.client_id !== clientId)
  }

  /** The colour of whoever added a place (matched by display name) -- markers and
   * order badges wear it, so the map reads as the group's shared record. */
  function creatorColorOf(addedBy: string): string {
    if (!addedBy) return ''
    return participants.value.find((p) => p.name === addedBy)?.color ?? ''
  }

  // -- row helpers ---------------------------------------------------------------------

  /** One day's authoritative schedule: rows go through the rev guard, the day-level
   * numbers (end_min/travel_min/warnings/exact) always land. */
  function applyTimelineSummary(t: DayTimeline) {
    for (const place of t.places) upsertPlaceIfNewer(place)
    timelines.value = { ...timelines.value, [t.day_id]: t }
  }

  /** Server rows win on strictly greater rev; equal rev means we already have it. */
  function upsertPlaceIfNewer(place: Place) {
    const existing = places.value.find((p) => p.id === place.id)
    if (!existing) {
      places.value.push(place)
      return
    }
    if (place.rev > existing.rev) places.value[places.value.indexOf(existing)] = place
  }

  function removeLocalRow(placeId: string) {
    places.value = places.value.filter((p) => p.id !== placeId)
    if (selectedPlaceId.value === placeId) selectedPlaceId.value = null
  }

  /** Re-sequence one day from an authoritative id array. */
  function applyOrder(dayId: string, orderedIds: string[]) {
    if (!Array.isArray(orderedIds)) return
    const rank = new Map(orderedIds.map((id, i) => [id, i]))
    for (const place of places.value) {
      if (place.day_id !== dayId) continue
      const next = rank.get(place.id)
      if (next !== undefined) place.sort_index = next
    }
  }

  function upsertChecklistIfNewer(item: ChecklistItem) {
    if (!item?.id) return
    const index = checklist.value.findIndex((i) => i.id === item.id)
    if (index === -1) checklist.value.push(item)
    else if (item.rev > checklist.value[index].rev) checklist.value[index] = item
  }

  function applyChecklistOrder(orderedIds: string[]) {
    if (!Array.isArray(orderedIds)) return
    const rank = new Map(orderedIds.map((id, i) => [id, i]))
    for (const item of checklist.value) {
      const next = rank.get(item.id)
      if (next !== undefined) item.sort_index = next
    }
  }

  function dropChecklist(ids: string[]) {
    if (!ids.length) return
    const dead = new Set(ids)
    checklist.value = checklist.value.filter((i) => !dead.has(i.id))
  }

  function upsertExpenseIfNewer(expense: Expense) {
    if (!expense?.id) return
    const index = expenses.value.findIndex((e) => e.id === expense.id)
    if (index === -1) expenses.value.push(expense)
    else if (expense.rev > expenses.value[index].rev) expenses.value[index] = expense
  }

  function dropExpense(id: string) {
    expenses.value = expenses.value.filter((e) => e.id !== id)
  }

  function describe(err: unknown): { message: string; hint: string } {
    const e = err as { message?: string; hint?: string }
    return { message: e?.message ?? '加载失败', hint: e?.hint ?? '' }
  }

  return {
    trip,
    days,
    places,
    participants,
    presence,
    stash,
    checklist,
    expenses,
    currentDayId,
    selectedPlaceId,
    loading,
    loadError,
    opError,
    optimizing,
    optimizeResult,
    timelines,
    currentDay,
    currentPlaces,
    selectedPlace,
    currentTimeline,
    currentWarnings,
    checklistSorted,
    checklistDoneCount,
    expensesNewestFirst,
    spentCents,
    selectPlace,
    applySnapshot,
    load,
    registerSelf,
    addPlace,
    updatePlace,
    setPlaceLocked,
    deletePlaceWithUndo,
    reorderDay,
    nudgePlace,
    updateTripFields,
    addDay,
    updateDay,
    deleteDay,
    setStartPlace,
    setEndPlace,
    stashAdd,
    stashRemove,
    promoteFromStash,
    movePlaceToDay,
    checklistAdd,
    updateChecklist,
    removeChecklist,
    reorderChecklist,
    addExpense,
    updateExpense,
    removeExpense,
    optimize,
    undoOptimize,
    dismissOptimizeResult,
    opLog,
    applyRemoteOp,
    applyReject,
    applyPresence,
    removePresence,
    draggersByDay,
    creatorColorOf,
    clearPendingOps,
    applyOrder,
  }
})

if (import.meta.hot) {
  // dev 热更新时保住 store 状态：没有这一行，改 store 文件后长开页面的状态会错乱
  // （用户遇到的「刚刚卡了」的一部分来源）。
  acceptHMRUpdate(useTripStore, import.meta.hot)
}
