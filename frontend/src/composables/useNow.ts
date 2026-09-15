import { onScopeDispose, readonly, ref } from 'vue'

/**
 * 共享时钟：倒计时与「当前时刻」游标都读同一个走针，而不是各自 setInterval。
 *
 * 一个 interval 一份订阅计数，最后一个消费者卸载才清表——首页能同时挂十几张行程卡，
 * 一人一块表会在切到后台时被浏览器成倍节流，读数反而更旧。
 */
interface Clock {
  now: { value: number }
  timer: ReturnType<typeof setInterval>
  subscribers: number
}

const clocks = new Map<number, Clock>()

export function useNow(intervalMs = 1000) {
  let clock = clocks.get(intervalMs)
  if (!clock) {
    const now = ref(Date.now())
    clock = {
      now,
      subscribers: 0,
      timer: setInterval(() => {
        now.value = Date.now()
      }, intervalMs),
    }
    clocks.set(intervalMs, clock)
  }
  clock.subscribers += 1
  const bound = clock
  onScopeDispose(() => {
    bound.subscribers -= 1
    if (bound.subscribers <= 0) {
      clearInterval(bound.timer)
      clocks.delete(intervalMs)
    }
  })
  return readonly(clock.now)
}
