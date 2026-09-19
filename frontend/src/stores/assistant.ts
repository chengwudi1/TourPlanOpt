/**
 * 助手状态（M27）。
 *
 * 分工要说清楚：解析在服务端（`app/assistant/`），**落地在这里**，而且只有一条路 ——
 * 人点了确认卡才调用 `useTripStore` 的既有方法，走既有 WS op。助手自己不写库、
 * 不发 op、不消耗 seq，所以「精灵偷偷改了同伴的界面」这件事在结构上就不成立。
 *
 * 三条接口约定值得在代码里留住：
 * 1. 没有「整批应用」。`destructive` 为真的两种删除必须逐条手点，其它动作同样一条一卡，
 *    因为这批候选里混着只由规则/模型认出来的东西。
 * 2. `warnings` 与 `questions` 全部上界面，不挑第一条。
 * 3. 解析失败与 429 都算正常对话：精灵说一句话解释，不弹红条，行程一动不动。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type {
  ActionCard,
  AssistantAction,
  AssistantEngine,
  AssistantMood,
  AssistantReply,
  AssistantStatus,
  ChatLine,
} from '@/types/assistant'
import type { Trip } from '@/types/domain'
import { ApiError, apiFetch, postJson } from '@/utils/api'
import { useTripStore } from '@/stores/trip'

/** 优化默认走直线距离：真实路况矩阵要烧高德配额，不该由一句顺手的话替用户决定。 */
const OPTIMIZE_COST_MODEL = 'haversine' as const

function lineId(): string {
  return `l-${crypto.randomUUID()}`
}

export const useAssistantStore = defineStore('assistant', () => {
  const open = ref(false)
  const lines = ref<ChatLine[]>([])
  const busy = ref(false)
  const status = ref<AssistantStatus | null>(null)
  /** 上一次解析的 questions：下一句把它当 history 带回去，服务端不存会话。 */
  const lastQuestions = ref<string[]>([])
  const lastEngine = ref<AssistantEngine>('rules')

  const mood = computed<AssistantMood>(() => {
    if (busy.value) return 'thinking'
    const last = [...lines.value].reverse().find((l) => l.from === 'pet')
    if (!last) return 'idle'
    if (last.cards?.some((c) => c.state === 'failed')) return 'warn'
    // 追问优先：有问题要问的时候不该笑 —— 「没听懂还很高兴」是吉祥物最招人嫌的表情。
    if (last.cards?.some((c) => c.state === 'pending') || last.notes?.length) return 'asking'
    if (last.cards?.some((c) => c.state === 'done')) return 'happy'
    // 走到这里只剩全被忽略的一轮：它没话要问，也不该摆出一副在等的脸。
    return 'idle'
  })

  /** 面板关着时，精灵身上的角标：还有几条没落地。 */
  const pendingCount = computed(
    () =>
      lines.value.reduce(
        (sum, l) => sum + (l.cards?.filter((c) => c.state === 'pending').length ?? 0),
        0,
      ),
  )

  const endpointReady = computed(() => status.value !== null)

  async function loadStatus(): Promise<void> {
    if (status.value) return
    try {
      status.value = await apiFetch<AssistantStatus>('/api/assistant/status')
    } catch {
      // 状态拿不到只影响两件事：精灵第一句话怎么说，以及话筒走哪条识别路（退回浏览器）。
      // 解析接口自己会兜底。
      status.value = {
        llm_ready: false,
        model: '',
        endpoint: '',
        max_chars: 600,
        speech_ready: false,
        speech_endpoint: '',
        speech_max_seconds: 90,
      }
    }
  }

  function toggle(value?: boolean): void {
    open.value = value ?? !open.value
    if (open.value) void loadStatus()
  }

  function reset(): void {
    lines.value = []
    lastQuestions.value = []
  }

  function push(from: ChatLine['from'], text: string, extra: Partial<ChatLine> = {}): void {
    lines.value.push({ id: lineId(), from, text, ...extra })
    // 记录只会越长越占内存，也不会有人往上翻三十条：留最近 40 条。
    if (lines.value.length > 40) lines.value.splice(0, lines.value.length - 40)
  }

  const GREETING =
    '说一句话就能改行程：加地点、补清单、记开销、优化顺序都行。动手前会先列出要做的事，逐条确认。'

  function greeting(): string {
    // 没配模型时也要说清这次是谁在出力——把兜底说成故障，用户就再也不点这里了。
    return status.value?.llm_ready === false
      ? `${GREETING}当前为基础理解，请带上「哪天、哪个地点、多少钱」这类明确信息。`
      : GREETING
  }

  async function say(text: string): Promise<void> {
    const utterance = text.trim()
    const store = useTripStore()
    if (!utterance || !store.trip || busy.value) return
    push('me', utterance)
    busy.value = true
    try {
      const reply = await apiFetch<AssistantReply>(
        `/api/trips/${store.trip.id}/assistant/parse`,
        postJson({
          text: utterance,
          day_id: store.currentDayId,
          history: lastQuestions.value,
        }),
      )
      lastEngine.value = reply.engine
      lastQuestions.value = reply.questions
      const cards: ActionCard[] = reply.actions.map((action) => ({
        action,
        state: 'pending' as const,
        error: '',
      }))
      push('pet', reply.reply || '没有生成可执行的改动', {
        cards: cards.length ? cards : undefined,
        notes: [...reply.questions, ...reply.warnings],
        engine: reply.engine,
      })
    } catch (err) {
      const e = err as ApiError
      lastQuestions.value = []
      push('pet', e?.message ? `这次没有成功：${e.message}` : '这次没有成功，行程没有被改动')
    } finally {
      busy.value = false
    }
  }

  /** 一句候选指令落到既有的 store 方法上 = 一条既有的 WS op。除此之外什么都不做。 */
  async function runAction(action: AssistantAction): Promise<void> {
    const store = useTripStore()
    switch (action.kind) {
      case 'place_add':
        store.addPlace(
          {
            name: action.name,
            lng: action.lng,
            lat: action.lat,
            address: action.address,
            amap_poi_id: action.amap_poi_id,
            photo_url: action.photo_url,
            duration_min: action.duration_min,
            note: action.note,
            after_place_id: action.after_place_id,
          },
          action.day_id,
        )
        return
      case 'place_move':
        store.movePlaceToDay(action.place_id, action.day_id)
        return
      case 'place_update':
        store.updatePlace(action.place_id, action.patch)
        return
      case 'place_delete':
        // 确认卡上的「执行」也会按错，同样给一次反悔的机会（S2）。
        store.deletePlaceWithUndo(action.place_id)
        return
      case 'place_lock':
        store.setPlaceLocked(action.place_id, action.locked)
        return
      case 'day_add':
        store.addDay(action.title, action.date)
        return
      case 'checklist_add':
        store.checklistAdd(action.texts)
        return
      case 'checklist_update':
        store.updateChecklist(action.item_id, action.patch)
        return
      case 'checklist_delete':
        store.removeChecklist(action.item_id)
        return
      case 'expense_add':
        store.addExpense({
          title: action.title,
          amount_cents: action.amount_cents,
          category: action.category,
        })
        return
      case 'trip_update':
        store.updateTripFields(action.patch as Partial<Trip>)
        return
      case 'run_optimize': {
        // optimize 把失败写进 store.opError 而不是抛错。它进入时会先清空，
        // 所以「变了」既可能是这次失败、也可能是上一次的错误被这次的成功带走。
        const before = store.opError
        await store.optimize(OPTIMIZE_COST_MODEL, action.day_id)
        if (store.opError && store.opError !== before) throw new Error(store.opError.message)
        return
      }
    }
  }

  async function confirm(cardLineId: string, index: number): Promise<void> {
    const line = lines.value.find((l) => l.id === cardLineId)
    const card = line?.cards?.[index]
    if (!card || card.state === 'done') return
    try {
      await runAction(card.action)
      card.state = 'done'
      card.error = ''
    } catch (err) {
      card.state = 'failed'
      card.error = (err as Error)?.message ?? '执行失败'
    }
  }

  function dismiss(cardLineId: string, index: number): void {
    const card = lines.value.find((l) => l.id === cardLineId)?.cards?.[index]
    if (card && card.state === 'pending') card.state = 'skipped'
  }

  return {
    open,
    lines,
    busy,
    status,
    mood,
    pendingCount,
    endpointReady,
    lastEngine,
    loadStatus,
    toggle,
    reset,
    greeting,
    say,
    confirm,
    dismiss,
  }
})
