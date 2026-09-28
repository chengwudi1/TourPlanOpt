<script setup lang="ts">
import { Mesh, Program, Renderer, Texture, Triangle } from 'ogl'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { MOTION } from '@/composables/useMotion'

/**
 * 封面的 WebGL 档（lab D3 的落地版）：照片由 shader 画，不再由 `<img>` 画。
 *
 * 它只接管**照片自己**的三件事，其余动效仍归 CSS，两边不许同时跑同一条：
 * 22s 呼吸（取代 `.mast__img` 的 `ken-burns`）、噪声溶解揭幕、指针视差。整块那句
 * 「照片从 1.12 落回 1.0」还是 CSS 的 `mast-settle` 在做——它带着画布一起落，两边各做一半
 * 才不会中途跳一刀。宿主收到 `live` 才关掉 img 那条呼吸，收到 `dead` 就一切照旧——
 * `<img>` 一直在底下画着，所以回落不是「换一张图」，是本来就有。
 *
 * 为什么要 1x1 读回探测：纹理来自 `<img>` 那张位图，自定义封面是用户给的 URL，跨域时
 * `texImage2D` 在 vendor 里抛 SecurityError，界面上只剩一块黑。探测脏了就当场判死，
 * 不把异常留给渲染循环（实验台踩过这一次）。
 *
 * 生命周期是硬要求，不是优化：离屏 / 标签页隐藏 / contextlost 三种情况都得停 rAF，
 * 卸载时得 `loseContext()`——ogl 的 Renderer 没有 dispose，浏览器对 WebGL 上下文又有
 * 数量上限（约 16 个），行程页来回进几次就会把上限吃掉，之后**全站**的 WebGL 都起不来。
 */
const props = defineProps<{ src: string }>()

const emit = defineEmits<{
  /** shader 真的在画了：宿主可以关掉那两条 CSS 动画。 */
  live: []
  /** 判死：回落 `<img>`。原因只进控制台，不进界面——用户不该看见诊断文字。 */
  dead: [reason: string]
}>()

const canvasEl = ref<HTMLCanvasElement | null>(null)

/** 照抄 `.mast__img` 的 `object-position: 50% 60%`（顶原点）。翻到 texture 坐标是
 *  shader 里最后一步的事，常量本身不预先换算——预换算过一次，改 CSS 的那次提交就把它作废了。 */
const OBJ_POS = [0.5, 0.6]

const VERT = `precision highp float;
attribute vec2 position;
varying vec2 vP;
void main() { vP = position; gl_Position = vec4(position, 0.0, 1.0); }`

const FRAG = `precision highp float;
varying vec2 vP;
uniform sampler2D tMap;
uniform vec2 uRes;
uniform vec2 uPos;
uniform vec2 uMouse;
uniform vec3 uBase;
uniform float uTime;
uniform float uProg;
uniform float uScale;
uniform float uArImg;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void main() {
  float arBox = uRes.x / max(1.0, uRes.y);
  // cover 取景：只沿装不下的那个轴裁。win 是"看得见的那一段占图的多大比例"。
  vec2 win = arBox > uArImg ? vec2(1.0, uArImg / arBox) : vec2(arBox / uArImg, 1.0);
  // object-position 的百分比不是"裁窗中心在图的 60%"，而是"图的 60% 点对齐框的 60% 点"，
  // 所以裁窗起点是 pos*(1-win)。按中心算会整体偏 4% 的图高，照片会往下滑一刀。
  vec2 scr = vec2(vP.x * 0.5 + 0.5, 0.5 - vP.y * 0.5);   // 转成顶原点的框内分数
  vec2 f = uPos * (1.0 - win) + scr * win;               // 顶原点的图内分数
  // 呼吸与揭幕收拢绕裁窗中心，否则照片会边放大边平移。
  vec2 c = uPos * (1.0 - win) + 0.5 * win;
  f = (f - c) / uScale + c;
  // 指针视差：底层的照片走 0.35 倍、表层走满，一次提交里分层。
  f += uMouse * 0.008 * (0.35 + (1.0 - f.y) * 0.65) * vec2(1.0, -1.0);
  vec3 col = texture2D(tMap, clamp(vec2(f.x, 1.0 - f.y), 0.001, 0.999)).rgb;
  // 噪声溶解：底色取主题那一面，深色态才不是闪一道白。
  float n = hash(floor(f * 900.0) / 900.0);
  float p = clamp(uProg * 1.35 - n * 0.35, 0.0, 1.0);
  col = mix(uBase, col, p);
  // 溶解边界那一线微亮：让「显影」有一条走动的边，而不是整块淡入。
  col += (1.0 - smoothstep(0.0, 0.06, abs(p - 0.5))) * 0.05;
  // 颗粒按 12fps 步进：逐帧随机读起来是电视雪花，隔十几帧换一次才像胶片。
  col += (hash(f * uRes + floor(uTime * 12.0)) - 0.5) * 0.012;
  gl_FragColor = vec4(col, 1.0);
}`

let renderer: Renderer | null = null
let program: Program | null = null
let texture: Texture | null = null
let mesh: Mesh | null = null
let raf = 0
let observer: IntersectionObserver | null = null
let resizeObs: ResizeObserver | null = null
let running = false
let dead = false
/** 只在实际在跑的那段时间里累加的秒表。用 `performance.now() - t0` 的话，离屏或切后台
 *  回来时 t 会瞬间跳一大截：揭幕当成已经走完（或重播一遍），呼吸直接从某一帧跳到另一帧。 */
let clock = 0
let last = 0
/** 揭幕的起跑点（秒表读数）与是否走完。 */
let revealFrom = 0
let revealed = false
/** 指针目标与当前值：目标由事件写，当前值每帧追一点，所以手一甩不会抖。 */
let mx = 0
let my = 0
let tx = 0
let ty = 0

function onPointerMove(e: PointerEvent) {
  const el = canvasEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  if (!r.width || !r.height) return
  tx = Math.min(1, Math.max(-1, ((e.clientX - r.left) / r.width) * 2 - 1))
  ty = Math.min(1, Math.max(-1, -(((e.clientY - r.top) / r.height) * 2 - 1)))
}

function onPointerOut() {
  tx = 0
  ty = 0
}

/** `1 - 2^-10p`：就是 `--ease-out` 那条曲线，CSS 侧同名令牌读不到只能按定义算。 */
function expoOut(p: number) {
  return p >= 1 ? 1 : 1 - 2 ** (-10 * p)
}

/** 帧间隔（毫秒）。30fps 是算过的：这一屏最快的是指针跟随（阻尼 0.12/帧，约 0.25s 收敛），
 *  其余是 22s 的呼吸与十几帧才换一次的颗粒——60fps 只是把整块照片像素每帧多染一遍。 */
const FRAME_MS = 33

let prev = 0

function frame(now: number) {
  const el = canvasEl.value
  if (!renderer || !program || !el || dead) return
  raf = requestAnimationFrame(frame)
  // 没到一格的帧直接让出去：秒表不累加，时间不会因此跳。
  if (now - prev < FRAME_MS) return
  const u = program.uniforms
  // 恢复后的第一帧不推进秒表：那一帧的间隔是「离开多久」，不是「这一帧多久」。
  const dt = last ? (now - last) / 1000 : 0
  prev = now
  last = now
  clock += dt
  u.uTime.value = clock

  if (!revealed) {
    const p = Math.min(1, (clock - revealFrom) / MOTION.reveal)
    u.uProg.value = expoOut(p)
    if (p >= 1) revealed = true
  }
  // 尺度这一路只留 22s 呼吸（与 `--dur-drift` 同周期、幅度 1.4%）：整块的入场收拢 1.12→1.0
  // 仍由 CSS 的 `mast-settle` 带着画布一起落。两边各做一半，谁也不会中途跳一刀。
  u.uScale.value = 1 + 0.014 * Math.sin((clock * Math.PI * 2) / 22)

  // 0.12 是按 30fps 标的收敛速度（≈0.25s 走完），换帧率就要重算这一格。
  mx += (tx - mx) * 0.12
  my += (ty - my) * 0.12
  u.uMouse.value = [mx, my]

  try {
    renderer.render({ scene: mesh as Mesh })
  } catch (err) {
    kill(`渲染抛错：${(err as Error).message}`)
  }
}

function start() {
  if (running || dead || !renderer) return
  running = true
  last = 0
  prev = 0
  raf = requestAnimationFrame(frame)
}

function stop() {
  running = false
  last = 0
  cancelAnimationFrame(raf)
  raf = 0
}

/** 判死：停循环、摘画布、通知宿主。任何一条失败路径都走这里，不留半块黑。 */
function kill(reason: string) {
  if (dead) return
  dead = true
  stop()
  const el = canvasEl.value
  if (el) el.style.display = 'none'
  emit('dead', reason)
}

/** 1x1 读回：脏纹理（跨域）在这里就露出来，不等 texImage2D 在 vendor 里抛。 */
function readable(im: HTMLImageElement) {
  try {
    const c = document.createElement('canvas')
    c.width = 1
    c.height = 1
    const x = c.getContext('2d')
    if (!x) return false
    x.drawImage(im, 0, 0)
    x.getImageData(0, 0, 1, 1)
    return true
  } catch {
    return false
  }
}

/** `#rrggbb` / `rgb()` → [0,1]³。溶解底色必须跟着主题走，所以从令牌现读，不写死。 */
function toRgb3(raw: string): [number, number, number] {
  const hex = raw.trim().match(/^#([\da-f]{3}|[\da-f]{6})$/i)
  if (hex) {
    const s = hex[1]
    const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s
    return [0, 2, 4].map((i) => Number.parseInt(full.slice(i, i + 2), 16) / 255) as [
      number,
      number,
      number,
    ]
  }
  const nums = raw.match(/[\d.]+/g)?.slice(0, 3).map((v) => Number(v) / 255)
  return nums && nums.length === 3
    ? (nums as [number, number, number])
    : [0.92, 0.92, 0.91]
}

function syncSize() {
  const el = canvasEl.value
  const host = el?.parentElement
  if (!host || !renderer || dead) return
  // 量宿主不是多此一举：ogl 的 `setSize` 会把 style 写成死数，构造时先按默认的
  // 300×150 来了一发，画布自己的 `inset: 0` 与 `width: 100%` 顶不回去。
  const w = host.clientWidth
  const h = host.clientHeight
  if (!w || !h) return
  renderer.setSize(w, h)
  if (program) program.uniforms.uRes.value = [w, h]
  // 停下之后（离屏、隐藏标签页）尺寸变了也得补一帧，否则回到前台先看见旧的裁切。
  if (!running) {
    try {
      renderer.render({ scene: mesh as Mesh })
    } catch {
      /* 补帧失败不影响回落：img 一直在底下 */
    }
  }
}

function loadTexture(src: string) {
  if (dead || !texture || !program) return
  const im = new Image()
  im.decoding = 'async'
  // 故意不设 crossOrigin：设了之后那些不发 CORS 头的自定义封面会直接 onerror，
  // 反而把「能显示的回落图」也弄没了。脏不脏交给 readable() 判。
  im.onload = () => {
    if (dead || !im.naturalWidth) return
    if (!readable(im)) {
      kill('纹理跨域不可读')
      return
    }
    // 这两个是模块级的 `let`，闭包里 TS 不认函数开头那次判空，只能就地再取一次。
    const prog = program
    const tex = texture
    if (!prog || !tex) return
    prog.uniforms.uArImg.value = im.naturalWidth / im.naturalHeight
    tex.image = im
    tex.needsUpdate = true
    emit('live')
    start()
  }
  im.onerror = () => kill('封面图加载失败')
  im.src = src
}

function onContextLost(e: Event) {
  e.preventDefault()
  kill('webglcontextlost')
}

onMounted(() => {
  const el = canvasEl.value
  const host = el?.parentElement
  if (!el || !host) return
  // 触屏不开：照片的视差与溶解是指针的回应，摸不到指针时它就是纯粹多花一次 GPU 功耗。
  if (!window.matchMedia('(pointer: fine)').matches) {
    kill('非指针设备')
    return
  }
  const w0 = host.clientWidth || 1
  const h0 = host.clientHeight || 1
  try {
    renderer = new Renderer({
      canvas: el,
      width: w0,
      height: h0,
      alpha: false,
      antialias: false,
      powerPreference: 'low-power',
      // 上限 1.5 不是抠门：这一屏全是软内容加 1.4% 的颗粒，2 倍采样买不到看得见的东西，
      // 但每帧多染三倍像素。照片本来就被压暗层和票券盖着，不需要锐到能数清叶脉。
      dpr: Math.min(window.devicePixelRatio || 1, 1.5),
    })
  } catch (err) {
    kill(`WebGL 初始化失败：${(err as Error).message}`)
    return
  }
  const gl = renderer.gl
  const base = toRgb3(
    getComputedStyle(document.documentElement).getPropertyValue('--surface-2') || '#ebe9e6',
  )
  texture = new Texture(gl, { flipY: true })
  program = new Program(gl, {
    vertex: VERT,
    fragment: FRAG,
    uniforms: {
      tMap: { value: texture },
      uRes: { value: [w0, h0] },
      uPos: { value: OBJ_POS },
      uMouse: { value: [0, 0] },
      uBase: { value: base },
      uTime: { value: 0 },
      uProg: { value: 0 },
      uScale: { value: 1 },
      uArImg: { value: 16 / 9 },
    },
  })
  mesh = new Mesh(gl, { geometry: new Triangle(gl), program })

  el.addEventListener('webglcontextlost', onContextLost)
  window.addEventListener('pointermove', onPointerMove, { passive: true })
  window.addEventListener('pointerout', onPointerOut, { passive: true })

  // 盯宿主而不是画布：画布的 style 由 setSize 写死，它自己永远不会"变尺寸"。
  resizeObs = new ResizeObserver(syncSize)
  resizeObs.observe(host)
  // 离屏与标签页隐藏都停 rAF：照片不在眼前时没有东西需要被画。
  observer = new IntersectionObserver(
    (entries) => {
      const seen = entries[0]?.isIntersecting ?? false
      if (seen && !document.hidden) start()
      else stop()
    },
    { threshold: 0.01 },
  )
  observer.observe(el)

  loadTexture(props.src)
})

// 换封面：同一张画布换纹理，不重开上下文（重开要付一次初始化的钱，还可能撞上数量上限）。
watch(
  () => props.src,
  (src) => {
    if (dead) return
    revealFrom = clock
    revealed = false
    if (program) program.uniforms.uProg.value = 0
    loadTexture(src)
  },
)

function onVisibility() {
  if (!document.hidden) start()
  else stop()
}
document.addEventListener('visibilitychange', onVisibility)

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerout', onPointerOut)
  const el = canvasEl.value
  if (el) el.removeEventListener('webglcontextlost', onContextLost)
  stop()
  observer?.disconnect()
  resizeObs?.disconnect()
  observer = null
  resizeObs = null
  mesh = null
  program = null
  texture = null
  // Renderer 没有 dispose：显式交出上下文，不然行程页每进一次留一个，
  // 十几个之后连不相干的页面都起不了 WebGL。
  const gl = renderer?.gl
  gl?.getExtension('WEBGL_lose_context')?.loseContext()
  renderer = null
})
</script>

<template>
  <canvas ref="canvasEl" class="mast__gl" aria-hidden="true"></canvas>
</template>

<style scoped>
/* 压在 `<img>` 上：图先画着，shader 起得来就盖住它，起不来就在这儿消失，
   两条路都不是黑块。 */
.mast__gl {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: block;
  width: 100%;
  height: 100%;
}
</style>
