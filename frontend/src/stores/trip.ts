import { acceptHMRUpdate, defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type {
  Day,
  Participant,
  Place,
  PlaceCreateInput,
  Presence,
  Snapshot,
  StashItem,
  Trip,
} from '@/types/domain'
import type { OpBroadcastFrame } from '@/types/protocol'
import { Ops } from '@/types/protocol'
import { colorForClient, getClientId, useClientIdentity } from '@/composables/useClientIdentity'
import { apiFetch, postJson } from '@/utils/api'
import { useSocketStore } from '@/stores/socket'

interface PlaceAddPending {
  kind: 'place_add'
  tempId: string
}

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
  warnings: string[]
  exact: boolean
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
  const currentDayId = ref<string | null>(null)
  const selectedPlaceId = ref<string | null>(null)
  const loading = ref(false)
  const loadError = ref<{ message: string; hint: string } | null>(null)
  const opError = ref<{ message: string; hint: string } | null>(null)
  const optimizing = ref(false)
  const optimizeResult = ref<OptimizeResult | null>(null)

  /** op_id -> optimistic bookkeeping (e.g. which temp row a place_add created). */
  const pendingOps = new Map<string, PlaceAddPending>()

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
   * place_added) replaces it with the server's authoritative row. */
  function addPlace(input: PlaceCreateInput): Place | null {
    const dayId = currentDayId.value
    if (!dayId || !trip.value) return null

    const tempId = `tmp-${crypto.randomUUID()}`
    const maxIndex = places.value.reduce(
      (max, p) => (p.day_id === dayId ? Math.max(max, p.sort_index) : max),
      -1,
    )
    const optimistic: Place = {
      id: tempId,
      day_id: dayId,
      trip_id: trip.value.id,
      sort_index: maxIndex + 1,
      name: input.name,
      amap_poi_id: input.amap_poi_id ?? '',
      address: input.address ?? '',
      lng: input.lng,
      lat: input.lat,
      duration_min: input.duration_min ?? 60,
      start_min: null,
      arrive_min: null,
      travel_min_before: null,
      locked: false,
      status: 'pending',
      note: input.note ?? '',
      added_by: input.added_by || useClientIdentity().name,
      rev: 1,
      created_at: '',
      updated_at: '',
    }
    places.value.push(optimistic)

    const opId = useSocketStore().sendOp(Ops.PLACE_ADD, { day_id: dayId, ...input })
    pendingOps.set(opId, { kind: 'place_add', tempId })
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
    useSocketStore().sendOp(Ops.PLACE_UPDATE, { place_id: placeId, patch })
  }

  /** Pin a place: locked rows are anchors the optimizer routes around. */
  function setPlaceLocked(placeId: string, locked: boolean) {
    const place = places.value.find((p) => p.id === placeId)
    if (!place) return
    place.locked = locked
    useSocketStore().sendOp(Ops.PLACE_LOCK, { place_id: placeId, locked })
  }

  function deletePlace(placeId: string) {
    places.value = places.value.filter((p) => p.id !== placeId)
    if (selectedPlaceId.value === placeId) selectedPlaceId.value = null
    useSocketStore().sendOp(Ops.PLACE_DELETE, { place_id: placeId })
  }

  /** Drag result: send the FULL ordered id array (never a delta) and apply optimistically. */
  function reorderDay(dayId: string, orderedIds: string[]) {
    applyOrder(dayId, orderedIds)
    useSocketStore().sendOp(Ops.DAY_REORDER, { day_id: dayId, place_ids: orderedIds })
  }

  function updateTripFields(patch: Partial<Trip>) {
    if (trip.value) Object.assign(trip.value, patch)
    useSocketStore().sendOp(Ops.TRIP_UPDATE, { patch })
  }

  /** Append a new day to the trip. */
  function addDay() {
    useSocketStore().sendOp(Ops.DAY_ADD, {})
  }

  /** 删除一个空的天（服务端拒绝有内容的天与最后一天）。 */
  function deleteDay(dayId: string) {
    useSocketStore().sendOp(Ops.DAY_DELETE, { day_id: dayId })
  }

  /** 设为起点：优化器把这一天从这个地点出发。 */
  function setStartPlace(dayId: string, placeId: string) {
    useSocketStore().sendOp(Ops.DAY_UPDATE, { day_id: dayId, patch: { start_place_id: placeId } })
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
  }

  /** 收进想去清单。条目 id 由服务端生成，靠 stash_added 回广播落地。 */
  function stashAdd(input: StashAddInput) {
    useSocketStore().sendOp(Ops.STASH_ADD, { ...input, added_by: useClientIdentity().name })
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
    })
    stashRemove(item.id)
  }

  /** 跨天移动：把地点移到目标天末尾（place_move op，两天顺序由服务端权威重排）。 */
  function movePlaceToDay(placeId: string, toDayId: string) {
    const place = places.value.find((p) => p.id === placeId)
    if (!place || place.day_id === toDayId) return
    removeLocalRow(placeId)
    const maxIndex = places.value.reduce(
      (max, p) => (p.day_id === toDayId ? Math.max(max, p.sort_index) : max),
      -1,
    )
    places.value.push({ ...place, day_id: toDayId, sort_index: maxIndex + 1 })
    useSocketStore().sendOp(Ops.PLACE_MOVE, { place_id: placeId, day_id: toDayId })
  }


  // -- optimization --------------------------------------------------------------------

  /** Run the optimizer over the current day over HTTP (it can take seconds on a cold
   * cache); the resulting order/schedule also arrives as a route_optimized broadcast
   * for everyone else in the room. */
  async function optimize(costModel: 'haversine' | 'amap' = 'haversine'): Promise<void> {
    const dayId = currentDayId.value
    if (!dayId || !trip.value || optimizing.value) return
    optimizing.value = true
    opError.value = null
    try {
      const result = await apiFetch<OptimizeResult>(
        `/api/trips/${trip.value.id}/days/${dayId}/optimize`,
        postJson({ cost_model: costModel, apply: true }),
      )
      applyOptimizeResult(result)
    } catch (err) {
      const e = err as { message?: string; hint?: string }
      opError.value = { message: e?.message ?? '优化失败', hint: e?.hint ?? '' }
    } finally {
      optimizing.value = false
    }
  }

  function applyOptimizeResult(result: OptimizeResult) {
    applyOrder(result.day_id, result.place_ids)
    const incoming = (result as unknown as { places?: Place[] }).places ?? []
    for (const place of incoming) upsertPlaceIfNewer(place)
    optimizeResult.value = result
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

  /** The echo or another client's op. `pendingOps` distinguishes the two. */
  function applyRemoteOp(frame: OpBroadcastFrame) {
    const data = frame.data as Record<string, unknown>
    opError.value = null

    switch (frame.op) {
      case 'place_added': {
        const place = data.place as Place
        const pending = pendingOps.get(frame.op_id)
        pendingOps.delete(frame.op_id)
        if (pending?.kind === 'place_add') {
          removeLocalRow(pending.tempId)
        }
        upsertPlaceIfNewer(place)
        applyOrder(String(data.day_id), data.place_ids as string[])
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
      opError.value = { message: '顺序已被别人改动，已同步到最新', hint: '' }
      return
    }
    if (pending?.kind === 'place_add') {
      removeLocalRow(pending.tempId)
    }
    const messages: Record<string, string> = {
      place_not_found: '该地点已被删除',
      day_not_found: '目标天不存在',
      bad_patch: '修改内容无效',
      bad_payload: '提交的内容无效',
      stash_not_found: '这条想去清单已被删除',
    }
    opError.value = {
      message: messages[reason] ?? `操作被拒绝（${reason}）`,
      hint: '',
    }
  }

  function clearPendingOps() {
    // A fresh snapshot supersedes every optimistic guess; temp rows would linger.
    places.value = places.value.filter((p) => !p.id.startsWith('tmp-'))
    pendingOps.clear()
  }

  // -- presence ------------------------------------------------------------------------

  /** 别人（非自己）正在拖动某天的顺序时给出提示；本人拖动的 presence 自己也会收到，
   * 按client_id 过滤掉。 */
  const remoteDragger = computed(() => {
    if (!currentDayId.value) return null
    const other = presence.value.find(
      (p) => p.client_id !== getClientId() && p.dragging_day_id === currentDayId.value,
    )
    return other ? { name: other.name, color: other.color } : null
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
    currentDayId,
    selectedPlaceId,
    loading,
    loadError,
    opError,
    optimizing,
    optimizeResult,
    currentDay,
    currentPlaces,
    selectedPlace,
    selectPlace,
    applySnapshot,
    load,
    registerSelf,
    addPlace,
    updatePlace,
    setPlaceLocked,
    deletePlace,
    reorderDay,
    updateTripFields,
    addDay,
    updateDay,
    deleteDay,
    setStartPlace,
    stash,
    stashAdd,
    stashRemove,
    promoteFromStash,
    movePlaceToDay,
    optimize,
    undoOptimize,
    dismissOptimizeResult,
    applyRemoteOp,
    applyReject,
    applyPresence,
    removePresence,
    remoteDragger,
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
