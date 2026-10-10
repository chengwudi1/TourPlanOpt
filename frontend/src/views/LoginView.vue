<script setup lang="ts">
/**
 * 登录 / 注册整页（docs/LOGIN-PAGE.md）。左半是一张「夜航图」：一段真实的周末行程
 * 以贴纸的形式挂在路线上；右半是票据式表单。
 *
 * 四条硬约束记在这里，改动画前先读：
 * 1. 入场动画一律「自然态 = 终态」+ fill-mode: backwards。全局那条
 *    prefers-reduced-motion 会把所有 animation 打成 none，若像常见写法那样把初态
 *    写在声明里、靠 forwards 收尾，减少动效的用户就永远看不见路线和贴纸。
 * 2. 常驻循环只有两项（光点沿路线、罗盘针微摆），标签页不可见时整页暂停。
 * 3. 指针联动是「回声」不是循环：三层视差、光带、罗盘转向全部只往根元素写
 *    CSS 变量（幅度分配留在 CSS），「减少动效」下三个监听一个都不挂——那一档现在既
 *    来自系统，也来自设置面板，判断收在 composables/useReduceMotion，见该文件。
 *    根节点本身永远不加 transform——视图根上挂 transform 会把页内的 fixed 件掀出视口。
 * 4. 贴纸是插画不是界面，所以它的纸色与文字不随主题反色（同 --pet-body 的口径）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ArrowLeft, Compass, Eye, EyeOff, LoaderCircle, LogIn, Lock, User } from '@/components/icons'
import NoticeBar from '@/components/NoticeBar.vue'
import { useReduceMotion } from '@/composables/useReduceMotion'
import { useAuthStore } from '@/stores/auth'

type Mode = 'login' | 'register'

/** 路线与贴纸共用这一个百分比坐标系（viewBox 0 0 100 100 + preserveAspectRatio=none）。 */
const ROUTE_D = 'M 10 80 C 20 76, 22 66, 30 58 C 38 50, 40 42, 50 34 C 60 26, 60 18, 70 12'

/** day 是 ramp 档号：贴纸左边的色条与时间胶囊按它取色。 */
const PINS = [
  { time: '09:20', name: '茅家埠茶肆', meta: '落脚 · 本地人座位', day: 1, cls: 'pin--d1' },
  { time: '10:50', name: '杨公堤 · 里虹桥', meta: '步行 18 分钟', day: 2, cls: 'pin--d2' },
  { time: '13:00', name: '花港观鱼', meta: '红鱼池 · 免票', day: 3, cls: 'pin--d3' },
  { time: '15:30', name: '雷峰塔 · 夕照', meta: '末班 17:00', day: 6, cls: 'pin--d6' },
]

/** 站点锚点：与 ROUTE_D 的端点/拐点逐个对上，贴纸也挂在同一批坐标上。 */
const NODES: [number, number][] = [
  [10, 80],
  [30, 58],
  [50, 34],
  [70, 12],
]

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const mode = ref<Mode>('login')
const name = ref('')
const password = ref('')
const showPwd = ref(false)
const done = ref(false)
const frozen = ref(false)
const wide = ref(true)
const cardEl = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)
/** 指针光带的开关：只在真的能跟随指针时打开，减少动效下这一层根本不出现。 */
const live = ref(false)
/** 印章落定到跳转之间的延时句柄。 */
let stampTimer = 0

const isRegister = computed(() => mode.value === 'register')
const heading = computed(() => (isRegister.value ? '注册' : '登录'))
const sub = computed(() =>
  isRegister.value
    ? '注册即创建账号，行程与「最近打开」随账号跨设备找回。'
    : '登录用于跨设备找回行程；不登录也可创建与协同。',
)
const goLabel = computed(() => (isRegister.value ? '注册并登录' : '登录'))
const pwdLabel = computed(() => (isRegister.value ? '密码（至少 6 位）' : '密码'))
const errText = computed(() => auth.error?.message ?? '')
const canSubmit = computed(
  () => name.value.trim() !== '' && password.value.length >= 6 && !auth.busy && !done.value,
)
// 窄屏只有 38vh 的图区，画得下两张贴纸。「4 个地点」配两张贴纸是文案与画面打架。
const shownPins = computed(() => (wide.value ? PINS : PINS.slice(0, 2)))
// 窄屏不画路线（贴纸另起一套位置），锚点圆点跟着一起收，否则会变成无主的点。
const shownNodes = computed(() => (wide.value ? NODES : []))

/** next 只接受站内绝对路径：//host 是协议相对地址，直接交给路由等于开放重定向。 */
function safeNext(): string {
  const raw = route.query.next
  const v = Array.isArray(raw) ? raw[0] : raw
  return typeof v === 'string' && v.startsWith('/') && !v.startsWith('//') ? v : ''
}

function leave() {
  const next = safeNext()
  if (next) {
    router.replace(next)
    return
  }
  // 从哪来回哪去；没有来路（直接打开 /login）才落首页。
  if (typeof router.options.history.state.back === 'string') router.back()
  else router.replace({ name: 'home' })
}

async function submit() {
  if (!canSubmit.value) return
  try {
    if (isRegister.value) await auth.register(name.value.trim(), password.value)
    else await auth.login(name.value.trim(), password.value)
    password.value = ''
    done.value = true
    // 印章落下去那一格再走：让「已登录」被看见，否则成功反馈等于没有反馈。
    stampTimer = window.setTimeout(leave, 620)
  } catch {
    /* auth.error 带着服务端的话，下面的 watch 负责抖一下卡片 */
  }
}

/* 每次失败都要重新抖一次：类名一直在的话，第二次同样的错就没有动画了。 */
watch(
  () => auth.error,
  (err) => {
    const el = cardEl.value
    if (!err || !el) return
    el.classList.remove('card--shake')
    void el.offsetWidth
    el.classList.add('card--shake')
  },
)

/* 换模式时清掉上一模式的报错：它说的是「用户名或密码」，跟当前表单已经无关。 */
watch(mode, () => {
  auth.error = null
})

function onVisibility() {
  frozen.value = document.hidden
}

function onResize(e: MediaQueryListEvent) {
  wide.value = e.matches
}

/* ---------- 指针联动：视差 + 光带 + 罗盘指向 ----------
   派生值一律写成根元素上的 CSS 变量（--mx/--my 是无单位的 −1..1，--gx/--gy 是光带
   层自己的像素坐标，--aim 是角度），各层的幅度留给 CSS 的 calc 分配：JS 不碰
   transform，贴纸自身的定位（--dx/--dy）与视差就是天然相加，不必两处维护同一条式子。
   减少动效下整套不挂载——这条通道是「给鼠标回音」，不给不该动的人制造动。 */

/** rAF 缓动系数：约 6 帧走完 60%，指针停下后不到 200ms 收住。 */
const EASE = 0.14
/** 光带层比画布四周各宽 28px（inset: -28px），换算指针坐标要用到。 */
const BEAM_BLEED = 28

const ptr = { tx: 0, ty: 0, x: 0, y: 0 }
const beam = { tx: 0, ty: 0, x: 0, y: 0 }
const needle = { t: 0, v: 0 }
let canvasBox: DOMRect | null = null
let compassBox: DOMRect | null = null
let raf = 0

function clamp(v: number, lo: number, hi: number) {
  return v < lo ? lo : v > hi ? hi : v
}

function measure() {
  const root = rootEl.value
  canvasBox = root?.querySelector<HTMLElement>('.canvas')?.getBoundingClientRect() ?? null
  compassBox = root?.querySelector<SVGSVGElement>('.compass')?.getBoundingClientRect() ?? null
  if (canvasBox && !beam.x) {
    beam.tx = beam.x = canvasBox.width / 2 + BEAM_BLEED
    beam.ty = beam.y = canvasBox.height / 2 + BEAM_BLEED
  }
}

function tick() {
  const el = rootEl.value
  if (!el) {
    raf = 0
    return
  }
  ptr.x += (ptr.tx - ptr.x) * EASE
  ptr.y += (ptr.ty - ptr.y) * EASE
  beam.x += (beam.tx - beam.x) * EASE
  beam.y += (beam.ty - beam.y) * EASE
  needle.v += (needle.t - needle.v) * EASE
  el.style.setProperty('--mx', ptr.x.toFixed(3))
  el.style.setProperty('--my', ptr.y.toFixed(3))
  el.style.setProperty('--gx', `${beam.x.toFixed(1)}px`)
  el.style.setProperty('--gy', `${beam.y.toFixed(1)}px`)
  el.style.setProperty('--aim', `${needle.v.toFixed(2)}deg`)
  const settled =
    Math.abs(ptr.tx - ptr.x) < 0.002 &&
    Math.abs(ptr.ty - ptr.y) < 0.002 &&
    Math.abs(needle.t - needle.v) < 0.05 &&
    Math.abs(beam.tx - beam.x) < 0.5 &&
    Math.abs(beam.ty - beam.y) < 0.5
  raf = settled ? 0 : requestAnimationFrame(tick)
}

function start() {
  if (!raf) raf = requestAnimationFrame(tick)
}

function onPointerEnter() {
  measure()
}

function onPointerMove(e: PointerEvent) {
  const el = rootEl.value
  if (!el) return
  if (!canvasBox || !compassBox) measure()
  ptr.tx = clamp((e.clientX / (el.clientWidth || 1) - 0.5) * 2, -1, 1)
  ptr.ty = clamp((e.clientY / (el.clientHeight || 1) - 0.5) * 2, -1, 1)

  const box = canvasBox
  const inside =
    !!box && e.clientX >= box.left && e.clientX <= box.right && e.clientY >= box.top && e.clientY <= box.bottom
  live.value = inside
  if (box) {
    beam.tx = e.clientX - box.left + BEAM_BLEED
    beam.ty = e.clientY - box.top + BEAM_BLEED
  }

  const comp = compassBox
  if (comp) {
    const dx = e.clientX - (comp.left + comp.width / 2)
    const dy = e.clientY - (comp.top + comp.height / 2)
    // 针尖离指针太近时角度会抖成噪声，让它保持在原地。
    if (Math.hypot(dx, dy) > 60) {
      // 静止那根针朝东北（屏幕坐标 −45°）；把「指向指针」换算成相对它的偏角并限幅，
      // 免得绕到另一侧时整根针翻 180°。
      let a = (Math.atan2(dy, dx) * 180) / Math.PI + 45
      if (a > 180) a -= 360
      else if (a < -180) a += 360
      needle.t = clamp(a, -38, 38)
    }
  }
  start()
}

function onPointerLeave() {
  ptr.tx = 0
  ptr.ty = 0
  needle.t = 0
  live.value = false
  start()
}

const reduceMotion = useReduceMotion()

function bindPointer(on: boolean) {
  const el = rootEl.value
  if (!el) return
  if (on) {
    el.addEventListener('pointerenter', onPointerEnter)
    el.addEventListener('pointermove', onPointerMove, { passive: true })
    el.addEventListener('pointerleave', onPointerLeave)
    return
  }
  el.removeEventListener('pointerenter', onPointerEnter)
  el.removeEventListener('pointermove', onPointerMove)
  el.removeEventListener('pointerleave', onPointerLeave)
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  live.value = false
  canvasBox = null
  compassBox = null
  el.style.setProperty('--mx', '0')
  el.style.setProperty('--my', '0')
  el.style.removeProperty('--gx')
  el.style.removeProperty('--gy')
  el.style.removeProperty('--aim')
}

/** 减少动效一变，这条通道就整条接上或整条断开——指针回声也是动效，不是例外。 */
function syncPointer() {
  bindPointer(!reduceMotion.value)
}

watch(reduceMotion, syncPointer)

let mql: MediaQueryList | null = null

/** 窗口一变形，缓存的两个盒子就作废；立刻重量一次，静止的指针才不会指着旧位置。 */
function onWindowResize() {
  canvasBox = null
  compassBox = null
  measure()
}

onMounted(async () => {
  mql = window.matchMedia('(min-width: 961px)')
  wide.value = mql.matches
  mql.addEventListener('change', onResize)
  window.addEventListener('resize', onWindowResize)
  syncPointer()
  document.addEventListener('visibilitychange', onVisibility)
  onVisibility()
  if (!auth.loaded) await auth.load()
  // 已登录的人不该停在登录页。
  if (auth.user) leave()
})

onBeforeUnmount(() => {
  window.clearTimeout(stampTimer)
  mql?.removeEventListener('change', onResize)
  window.removeEventListener('resize', onWindowResize)
  bindPointer(false)
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <div ref="rootEl" class="login" :class="{ 'login--frozen': frozen, 'login--live': live }">
    <!-- ============ 左：夜航图 ============ -->
    <section class="canvas">
      <!-- 指针光带：等高线在光标经过处被照亮一圈，是「有东西在跟着你」的那一层。 -->
      <div class="beam" aria-hidden="true"></div>
      <div class="brand">
        <button class="brand__back" type="button" title="返回" aria-label="返回上一页" @click="leave">
          <ArrowLeft :size="14" />
        </button>
        <span class="brand__rule" aria-hidden="true"></span>
        <Compass :size="16" />
        <span class="brand__name">TourPlanOpt · 夜航图</span>
      </div>

      <h1 class="pitch">
        让一趟周末行程安排明白
        <em>分享链接即可协同，无需注册；账号只用于跨设备找回。</em>
      </h1>

      <div class="chart">
        <svg
          v-if="wide"
          class="chart__route"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path class="route-line" pathLength="100" vector-effect="non-scaling-stroke" :d="ROUTE_D" />
          <circle class="runner" vector-effect="non-scaling-stroke" r="0.6" />
        </svg>

        <!-- 站点标记画在 HTML 上：SVG 被 preserveAspectRatio=none 非等比拉过，
             里面的 <circle> 会变成椭圆饼；只有按同一套百分比定位的圆点是正圆。 -->
        <span
          v-for="(n, i) in shownNodes"
          :key="i"
          class="node"
          :class="{ 'node--live': i === shownNodes.length - 1 }"
          :style="{ left: `${n[0]}%`, top: `${n[1]}%`, '--i': i }"
          aria-hidden="true"
        ></span>

        <div
          v-for="(p, i) in shownPins"
          :key="p.time"
          class="pin"
          :class="[`pin--${i + 1}`, p.cls]"
          :style="{ '--i': i }"
        >
          <span class="pin__day" aria-hidden="true"></span>
          <div class="pin__body">
            <span class="pin__time">{{ p.time }}</span>
            <div class="pin__name">{{ p.name }}</div>
            <div class="pin__meta">{{ p.meta }}</div>
          </div>
        </div>

        <svg class="compass" viewBox="0 0 100 100" fill="none" stroke="currentColor" aria-hidden="true">
          <circle cx="50" cy="50" r="34" stroke-width="1.5" />
          <!-- 外层管「指向指针」，内层管常驻微摆：两层 transform 相加，不必为此起第二条动画。 -->
          <g class="compass__aim">
            <g class="compass__needle" stroke-width="2.5" stroke-linecap="round">
              <path d="M50 50 66 34" stroke="var(--cr-3)" />
              <path d="M50 50 34 66" />
            </g>
          </g>
          <path d="M50 10v8M50 82v8M10 50h8M82 50h8" stroke-width="1.5" />
        </svg>
      </div>

      <div class="stat">
        <span><b>{{ shownPins.length }}</b> 个地点</span>
        <span class="stat__rule" aria-hidden="true"></span>
        <span><b>1</b> 天</span>
        <span class="stat__rule" aria-hidden="true"></span>
        <span><b>3</b> 人协同</span>
      </div>
    </section>

    <!-- ============ 右：表单 ============ -->
    <section class="panel">
      <!-- 公告排在卡片上方、与它同宽：政策说的是「数据还能留几天」，正是这一页要交代的事。 -->
      <NoticeBar class="panel__notice" />
      <div ref="cardEl" class="card">
        <div class="seg" role="radiogroup" aria-label="账号操作">
          <button
            class="seg__opt"
            :class="{ 'seg__opt--on': !isRegister }"
            type="button"
            role="radio"
            :aria-checked="!isRegister"
            @click="mode = 'login'"
          >
            登录
          </button>
          <button
            class="seg__opt"
            :class="{ 'seg__opt--on': isRegister }"
            type="button"
            role="radio"
            :aria-checked="isRegister"
            @click="mode = 'register'"
          >
            注册
          </button>
        </div>

        <h2 class="head">{{ heading }}</h2>
        <p class="sub">{{ sub }}</p>

        <form @submit.prevent="submit">
          <div class="field">
            <input
              id="login-name"
              v-model="name"
              class="field__input"
              type="text"
              placeholder=" "
              maxlength="20"
              autocomplete="username"
              :disabled="done"
            />
            <label class="field__label" for="login-name">昵称</label>
            <span class="field__icon" aria-hidden="true"><User :size="16" /></span>
          </div>

          <div class="field" :class="{ 'field--error': !!errText }">
            <input
              id="login-pwd"
              v-model="password"
              class="field__input"
              :type="showPwd ? 'text' : 'password'"
              placeholder=" "
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
              :disabled="done"
            />
            <label class="field__label" for="login-pwd">{{ pwdLabel }}</label>
            <span class="field__icon" aria-hidden="true"><Lock :size="16" /></span>
            <button
              class="field__eye"
              type="button"
              :title="showPwd ? '隐藏密码' : '显示密码'"
              :aria-label="showPwd ? '隐藏密码' : '显示密码'"
              @click="showPwd = !showPwd"
            >
              <Eye v-if="showPwd" :size="16" />
              <EyeOff v-else :size="16" />
            </button>
          </div>

          <p v-if="errText" class="err" role="alert">{{ errText }}</p>

          <button class="btn btn--primary go" type="submit" :disabled="auth.busy || done">
            <LoaderCircle v-if="auth.busy" class="go__spin" :size="15" />
            <LogIn v-else :size="15" />
            <span>{{ goLabel }}</span>
          </button>
          <p class="exit"><RouterLink class="exit__link" :to="{ name: 'home' }">暂不登录，返回行程列表</RouterLink></p>
        </form>

        <div v-if="done" class="stamp" aria-hidden="true"><span>已登录</span></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ---------- 本页专属件（不进全局 token，理由见 main.css 例外清单） ----------
   夜航图是一张插画：贴在它上面的贴纸永远是亮面纸 + 固定墨字，描边永远是亮墨。
   两档画布底分开配（亮 #0d2033 / 暗 #16293c）：右半自 F 换肤起没有自己的底，
   玻璃卡直接压在全站照片背景上，画布与照片纱罩之间要留出可辨的分界线。
   逐组合实测见 docs/_contrast-login.mjs。 */
.login {
  --canvas-bg: #0d2033;
  --canvas-title: #f2f6fa;
  --canvas-meta: #a9bccd;
  --canvas-stat: #c3d2df;
  --canvas-ink: #dbe6ef;
  --canvas-paper: #fdfdfd;
  --canvas-paper-text: #26282a;
  --canvas-paper-meta: #616265;
  /* 画布自带的六色亮档：界面 ramp 压深底不到 3:1，图里另配一套（实测最低 4.47）。 */
  --cr-1: #5591ce;
  --cr-2: #58b45a;
  --cr-3: #f0a35c;
  --cr-4: #e3c04a;
  --cr-5: #a99df0;
  --cr-6: #ef7466;
  --canvas-shadow: 3px 3px 0 rgba(3, 10, 18, 0.55);
  --canvas-shadow-hover: 5px 5px 0 rgba(3, 10, 18, 0.6);
  --canvas-edge: rgba(255, 255, 255, 0.055);
  --canvas-beam: rgba(196, 224, 250, 0.3);

  /* 指针联动由 JS 写值、CSS 分配幅度：--mx/--my 是 −1..1 的归一化位置，
     --gx/--gy 是光带层内的坐标，--aim 是罗盘指向角。默认值就是「没动」，
     所以减少动效下（JS 一个监听都不挂）这一页停在的自然态与不接指针时完全一致。 */
  --mx: 0;
  --my: 0;
  --gx: 50%;
  --gy: 50%;
  --aim: 0deg;

  display: grid;
  grid-template-columns: 45fr 55fr;
  min-height: 100%;
}

:root[data-theme='dark'] .login {
  --canvas-bg: #16293c;
  --canvas-shadow: 3px 3px 0 rgba(3, 8, 14, 0.6);
  --canvas-shadow-hover: 5px 5px 0 rgba(3, 8, 14, 0.65);
  --canvas-edge: rgba(255, 255, 255, 0.07);
  --canvas-beam: rgba(206, 230, 250, 0.34);
}

/* ---------- 左：画布 ---------- */
.canvas {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: var(--s6) var(--s6) var(--s5);
  color: var(--canvas-title);
  background: var(--canvas-bg);
  /* 视差让等高线层向四周外扩 28px；不裁掉就会凭空多一条滚动条。 */
  overflow: clip;
}

/* 等高线：两簇同心圆 + 一层极淡路网斜纹，全走 --canvas-edge，两种主题下都是
   「有但不出声」那一档。外扩 28px 是给指针视差留的余量（最深层反向 −12px）。 */
.canvas::before {
  position: absolute;
  inset: -28px;
  pointer-events: none;
  content: '';
  background: repeating-radial-gradient(circle at 22% 24%, transparent 0 26px, var(--canvas-edge) 26px 27px),
    repeating-radial-gradient(circle at 82% 66%, transparent 0 34px, var(--canvas-edge) 34px 35px),
    repeating-linear-gradient(58deg, transparent 0 34px, var(--canvas-edge) 34px 35px);
  mask-image: linear-gradient(200deg, #000 0%, rgba(0, 0, 0, 0.55) 55%, transparent 92%);
  translate: calc(var(--mx) * -12px) calc(var(--my) * -10px);
}

/* 指针光带：与等高线同一套底纹、同一个盒子、同一个位移，所以被照亮的那圈线
   和暗着的那圈严丝合缝——观感上是「手电扫过图纸」，不是「多了一层贴图」。
   只靠 mask 跟着指针走，不逐帧重画渐变。 */
.beam {
  position: absolute;
  inset: -28px;
  z-index: 0;
  pointer-events: none;
  opacity: 0;
  background: radial-gradient(260px 260px at var(--gx) var(--gy), rgba(150, 195, 240, 0.16), transparent 72%),
    repeating-radial-gradient(circle at 22% 24%, transparent 0 26px, var(--canvas-beam) 26px 27px),
    repeating-radial-gradient(circle at 82% 66%, transparent 0 34px, var(--canvas-beam) 34px 35px),
    repeating-linear-gradient(58deg, transparent 0 34px, var(--canvas-beam) 34px 35px);
  mask-image: radial-gradient(270px 270px at var(--gx) var(--gy), #000 0%, rgba(0, 0, 0, 0.5) 55%, transparent 100%);
  translate: calc(var(--mx) * -12px) calc(var(--my) * -10px);
  transition: opacity var(--dur-slow) var(--ease-out);
}

.login--live .beam {
  opacity: 1;
}

.brand {
  position: relative;
  display: flex;
  gap: var(--s2);
  align-items: center;
  color: var(--canvas-meta);
  font-size: var(--t-meta);
  letter-spacing: var(--ls-label);
  white-space: nowrap;
}

.brand svg {
  display: block;
  flex: 0 0 auto;
}

.brand__name {
  letter-spacing: var(--ls-label);
}

.brand__back {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  margin-right: 2px;
  border: 1.5px solid var(--canvas-ink);
  border-radius: 8px;
  background: transparent;
  color: var(--canvas-ink);
  cursor: pointer;
  transition:
    transform var(--dur) var(--ease-pop),
    background var(--dur-fast) linear;
}

.brand__back:hover {
  background: rgba(219, 230, 239, 0.12);
  transform: translateX(-2px);
}

.brand__rule {
  width: 1px;
  height: 16px;
  margin: 0 var(--s2) 0 var(--s1);
  background: var(--canvas-edge);
}

.pitch {
  position: relative;
  max-width: 15em;
  margin: var(--s6) 0 0;
  font-family: var(--font-display);
  font-size: clamp(30px, 3.4vw, 46px);
  font-weight: 700;
  line-height: var(--lh-tight);
  letter-spacing: var(--ls-display);
  color: var(--canvas-title);
  translate: calc(var(--mx) * -6px) calc(var(--my) * -5px);
}

.pitch em {
  display: block;
  margin-top: var(--s3);
  font-family: var(--font);
  font-size: var(--t-meta);
  font-style: normal;
  font-weight: 500;
  letter-spacing: var(--ls-label);
  color: var(--canvas-meta);
}

/* 整块图区是近景：路线、站点、罗盘一起跟着指针走，贴纸在它们之上再各自加一档
   （见 .pin--n 的 --pd），前后景就分开了。幅度压到 10px 以内——登录页左侧只有
   一张图，晃大了就成了马戏团。 */
.chart {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  margin-top: var(--s4);
  translate: calc(var(--mx) * 10px) calc(var(--my) * 8px);
  will-change: translate;
}

.chart__route {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

/* 自然态是「画完了」：dashoffset 0。延迟那一段由 backwards 兜住，
   减少动效下动画被全局关掉时，留下的就是这条已经成型的路。 */
.route-line {
  fill: none;
  stroke: var(--cr-1);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 100;
  stroke-dashoffset: 0;
  animation: draw 1000ms var(--ease-out) 220ms backwards;
}

@keyframes draw {
  from {
    stroke-dashoffset: 100;
  }
}

.node {
  position: absolute;
  width: 9px;
  height: 9px;
  border: 2px solid var(--cr-1);
  border-radius: 50%;
  background: var(--canvas-bg);
  transform: translate(-50%, -50%);
  animation: fade 300ms var(--ease-out) calc(220ms + var(--i, 0) * 260ms) backwards;
}

.node--live {
  width: 11px;
  height: 11px;
  border-color: var(--cr-3);
  background: var(--cr-3);
}

/* 常驻微循环①：光点沿路线慢移，24s 一圈。offset-path 与模板里的 :d 必须同一条路。 */
.runner {
  fill: var(--cr-4);
  offset-path: path("M 10 80 C 20 76, 22 66, 30 58 C 38 50, 40 42, 50 34 C 60 26, 60 18, 70 12");
  offset-rotate: 0deg;
  animation: run 24s linear infinite;
}

@keyframes run {
  from {
    offset-distance: 0%;
  }

  to {
    offset-distance: 100%;
  }
}

/* 定位与视差都走独立的 translate 属性：指针那一档直接加在 --dx/--dy 后面，
   而 hover 的抬起走 scale——两件事各占一个属性，才能各自过渡、互不打架。 */
.pin {
  position: absolute;
  display: flex;
  gap: var(--s2);
  align-items: stretch;
  padding: 7px 10px 7px 7px;
  border: 1.5px solid var(--canvas-ink);
  border-radius: var(--radius-sm);
  background: var(--canvas-paper);
  color: var(--canvas-paper-text);
  box-shadow: var(--canvas-shadow);
  white-space: nowrap;
  translate: calc(var(--dx, 14px) + var(--mx) * var(--pd, 5px)) calc(var(--dy, -50%) + var(--my) * var(--pd, 5px));
  scale: 1;
  transition:
    scale var(--dur) var(--ease-pop),
    box-shadow var(--dur) var(--ease);
  animation: drop 420ms var(--ease-pop) calc(480ms + var(--i, 0) * 220ms) backwards;
}

/* 悬停抬起一档：阴影变硬、贴纸变大一点点。指针动效需要「碰到实物」的落点，
   不然整张图只是散散地飘。 */
.pin:hover {
  box-shadow: var(--canvas-shadow-hover);
  scale: 1.03;
}

@keyframes drop {
  from {
    opacity: 0;
    scale: 1.06;
  }
}

.pin--1 {
  left: 10%;
  top: 80%;
  --pd: 4px;
}

.pin--2 {
  left: 30%;
  top: 58%;
  --pd: 8px;
}

.pin--3 {
  left: 50%;
  top: 34%;
  --pd: 5px;
}

/* 最后一段路线往右上走，贴纸再往右就出画了，所以这一枚往左收。 */
.pin--4 {
  left: 70%;
  top: 12%;
  --pd: 10px;
  --dx: calc(-100% - 16px);
}

/* 贴纸是亮面纸，所以胶囊里的字永远用亮色主题那一对 ramp：反色后
   （深底 ramp-deep 回指亮实心色）压在白纸上只有 3.0:1。 */
.pin--d1 {
  --day: var(--cr-1);
  --day-soft: #dceafb;
  --day-deep: #1b5e93;
}

.pin--d2 {
  --day: var(--cr-2);
  --day-soft: #ddeec3;
  --day-deep: #165c0a;
}

.pin--d3 {
  --day: var(--cr-3);
  --day-soft: #f8d291;
  --day-deep: #8a4a0c;
}

.pin--d6 {
  --day: var(--cr-6);
  --day-soft: #f7ddd9;
  --day-deep: #8a2a24;
}

.pin__day {
  flex: 0 0 3px;
  border-radius: 2px;
  background: var(--day, var(--cr-1));
}

.pin__body {
  min-width: 0;
}

.pin__time {
  display: inline-block;
  padding: 1px 5px;
  border-radius: 6px;
  background: var(--day-soft);
  color: var(--day-deep);
  font-family: var(--mono);
  font-size: var(--t-micro);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}

.pin__name {
  margin-top: 2px;
  font-size: var(--t-meta);
  font-weight: 600;
  letter-spacing: var(--ls-tight);
}

.pin__meta {
  margin-top: 1px;
  font-size: var(--t-micro);
  color: var(--canvas-paper-meta);
}

/* 常驻微循环②：罗盘针 ±7° 慢摆一个来回（内层）；外层只管跟随指针转向，
   两层 transform 叠加，所以「指着你的光标」和「自己在呼吸」不冲突。 */
.compass {
  position: absolute;
  right: 4%;
  bottom: 6%;
  width: 74px;
  height: 74px;
  color: var(--canvas-meta);
  opacity: 0.9;
  translate: calc(var(--mx) * 4px) calc(var(--my) * 3px);
}

.compass__aim {
  transform: rotate(var(--aim));
  transform-origin: 50% 50%;
}

.compass__needle {
  transform-origin: 50% 50%;
  animation: swing 9s var(--ease-inout) infinite alternate;
}

@keyframes swing {
  from {
    transform: rotate(-7deg);
  }

  to {
    transform: rotate(6deg);
  }
}

.stat {
  position: relative;
  display: flex;
  gap: var(--s3);
  align-items: center;
  margin-top: var(--s4);
  color: var(--canvas-stat);
  font-size: var(--t-meta);
  font-variant-numeric: tabular-nums;
  animation: fade 520ms var(--ease-out) 1500ms backwards;
}

@keyframes fade {
  from {
    opacity: 0;
  }
}

.stat b {
  color: var(--canvas-title);
  font-weight: 700;
}

.stat__rule {
  flex: 1 1 auto;
  height: 1px;
  background: var(--canvas-ink);
  opacity: 0.24;
}

/* ---------- 右：表单 ---------- */
.panel {
  position: relative;
  /* 一列居中而不是网格：公告与卡片要同宽（430）同列，
     place-items: center 会把两条各自居中、宽度各随内容。
     没有自己的底色：F 的玻璃卡要压在照片背景上才有玻璃可言。 */
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  align-items: center;
  justify-content: center;
  padding: var(--s6) var(--s6) var(--s7);
}

/* 公告读的是子组件根节点上的这个类（子组件根的 scoped 属性由父级补上）。 */
.panel__notice {
  width: min(430px, 100%);
}

/* F 玻璃卡：密档玻璃（卡里有 --text-3 的浮动 label 与线性图标，标准档放不下它们）。
   它是这一页唯一直接压在照片上的大件，blur 只此一处。 */
.card {
  position: relative;
  width: min(430px, 100%);
  padding: var(--s6) var(--s6) var(--s5);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  background: var(--glass-dense);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  box-shadow: var(--shadow-pop), var(--edge);
}

.card--shake {
  animation: shake 320ms var(--ease-out);
}

@keyframes shake {
  0%,
  100% {
    transform: translateX(0);
  }

  20% {
    transform: translateX(-6px);
  }

  40% {
    transform: translateX(6px);
  }

  60% {
    transform: translateX(-4px);
  }

  80% {
    transform: translateX(3px);
  }
}

/* 分段控件不再自成一派：底色/描边/选中芯片全部对齐共享的 SegmentedControl
   （玻璃井 + 近黑芯片），只保留本页自己的 DOM 与 ARIA（radiogroup / radio / aria-checked）。 */
.seg {
  display: flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-pill);
  background: var(--glass);
}

.seg__opt {
  flex: 1 1 0;
  padding: 5px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: var(--t-meta);
  letter-spacing: var(--ls-label);
  cursor: pointer;
  transition:
    color var(--dur-fast) linear,
    background var(--dur-fast) linear,
    border-color var(--dur-fast) linear;
}

.seg__opt:hover:not(.seg__opt--on) {
  color: var(--text);
  background: color-mix(in srgb, var(--text) 8%, transparent);
}

/* 未选中也留着透明边：否则选中那一枚长出边框，两枚就差了 2px 高。 */
.seg__opt--on {
  border-color: transparent;
  background: var(--chip-on);
  color: var(--chip-on-ink);
  font-weight: 700;
}

.head {
  margin: var(--s5) 0 var(--s1);
  font-family: var(--font-display);
  font-size: var(--t-h1);
  font-weight: 700;
  line-height: var(--lh-tight);
  letter-spacing: var(--ls-display);
}

.sub {
  margin: 0 0 var(--s5);
  color: var(--text-2);
  font-size: var(--t-meta);
  line-height: var(--lh-body);
}

.field {
  position: relative;
  margin-bottom: var(--s4);
}

.field__input {
  width: 100%;
  padding: 21px 44px 7px 40px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  background: var(--glass);
  box-shadow: var(--edge);
  color: var(--text);
  font: inherit;
  font-size: var(--t-body);
  transition:
    border-color var(--dur-fast) linear,
    box-shadow var(--dur-fast) linear;
}

.field__input:hover {
  border-color: var(--text-faint);
}

.field__input:focus {
  border-color: var(--accent);
  outline: none;
  box-shadow: 0 0 0 3px var(--accent-soft), var(--edge);
}

.field__input:disabled {
  opacity: 0.6;
}

/* 浮动 label：靠 placeholder=" " + :not(:placeholder-shown) 判定「已经填了」。 */
.field__label {
  position: absolute;
  top: 50%;
  left: 41px;
  color: var(--text-3);
  font-size: var(--t-body);
  pointer-events: none;
  transform: translateY(-50%);
  transition:
    top var(--dur-fast) var(--ease),
    transform var(--dur-fast) var(--ease),
    font-size var(--dur-fast) var(--ease),
    color var(--dur-fast) linear;
}

/* 聚焦/已填的 label 与图标走深一档的珊瑚：卡是玻璃合成，最坏底（压纯黑照片）上
   亮珊瑚当正文只有 3.5:1，--accent-strong 才到 5.8:1（判据 docs/_contrast-login.mjs）。 */
.field__input:focus + .field__label,
.field__input:not(:placeholder-shown) + .field__label {
  top: 9px;
  color: var(--accent-strong);
  font-size: var(--t-micro);
  letter-spacing: var(--ls-label);
  transform: none;
}

.field__icon {
  position: absolute;
  top: 50%;
  left: 13px;
  color: var(--text-3);
  pointer-events: none;
  transform: translateY(-50%);
}

.field__input:focus ~ .field__icon {
  color: var(--accent-strong);
}

.field__eye {
  position: absolute;
  top: 50%;
  right: 6px;
  display: grid;
  place-items: center;
  padding: 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  transform: translateY(-50%);
}

.field__eye:hover {
  background: color-mix(in srgb, var(--text) 8%, transparent);
  color: var(--text-2);
}

.field--error .field__input {
  border-color: var(--danger);
}

/* 错误正文退到一块不透的红浅底上：红字压玻璃卡的最坏底只有 4.49:1（差一丝），
   压 --danger-soft 是两档主题下都恒定的 4.60 / 5.69（判据 docs/_contrast-login.mjs）。 */
.err {
  margin: -6px 0 var(--s4);
  padding: 7px 11px;
  border-radius: var(--radius-xs);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: var(--t-meta);
}

/* 形制全部来自全局 .btn--primary（珊瑚果冻 + 4px 深珊瑚底影、按下沉 2px）；
   这里只定尺寸——它是整张卡唯一的提交口，占满卡宽。 */
.go {
  width: 100%;
  justify-content: center;
  padding: 12px 14px;
  font-size: calc(15px * var(--fs-scale));
}

.go__spin {
  animation: spin 900ms linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.exit {
  margin-top: var(--s4);
  text-align: center;
  font-size: var(--t-meta);
}

.exit__link {
  color: var(--accent-strong);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--accent-strong) 40%, transparent);
}

/* 悬停不改色只把虚线下沿填实：基色已是「玻璃上最深的珊瑚」，再往亮里走会掉回 3.5:1。 */
.exit__link:hover {
  border-bottom-color: currentColor;
}

/* 成功印章：落在卡角上，不盖住分段控件。 */
.stamp {
  position: absolute;
  bottom: 22px;
  right: -12px;
  display: grid;
  place-items: center;
  width: 66px;
  height: 52px;
  border: 2px solid var(--accent-strong);
  border-radius: 12px;
  background: color-mix(in srgb, var(--glass) 78%, transparent);
  color: var(--accent-strong);
  font-family: var(--font-display);
  font-size: calc(15px * var(--fs-scale));
  font-weight: 700;
  letter-spacing: var(--ls-label);
  pointer-events: none;
  transform: rotate(-12deg);
  animation: stamp-in 200ms var(--ease-pop) backwards;
}

@keyframes stamp-in {
  from {
    opacity: 0;
    transform: rotate(-12deg) scale(1.4);
  }
}

/* ---------- 窄屏：图上收下 ---------- */
@media (max-width: 960px) {
  .login {
    grid-template-columns: 1fr;
    grid-template-rows: 38vh 1fr;
    /* svh = 工具栏展开时的「小视口」：画带只是门面，按大视口给高会把表单整段推出首屏。 */
    grid-template-rows: 38svh 1fr;
  }

  .canvas {
    padding: var(--s5) var(--s5) var(--s4);
  }

  .pitch {
    margin-top: var(--s2);
    font-size: calc(26px * var(--fs-scale));
  }

  .chart {
    margin-top: var(--s3);
  }

  /* 38vh 里放四张会互相压着；两张分置对角，罗盘挪去左上让位。 */
  .pin--1 {
    left: 2%;
    top: 74%;
    --dx: 10px;
  }

  .pin--2 {
    left: 56%;
    top: 26%;
    --dx: 10px;
  }

  .compass {
    top: 4%;
    right: auto;
    bottom: auto;
    left: 5%;
    width: 44px;
    height: 44px;
  }

  .panel {
    padding: var(--s5) var(--s4) calc(var(--s6) + var(--sab));
  }

  .card {
    width: 100%;
    padding: var(--s5) var(--s5) var(--s4);
  }
}

/* 标签页不可见时把两个常驻循环停下来：看不见的地方不该继续烧帧。 */
.login--frozen .runner,
.login--frozen .compass__needle,
.login--frozen .go__spin {
  animation-play-state: paused;
}
</style>
