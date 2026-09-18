import { ref } from 'vue'

import type { AssistantMood } from '@/types/assistant'

/**
 * 小精灵的形象（M29c）。
 *
 * 三条约定写在最前面，因为它们决定了这个文件为什么长这样：
 *
 * 1. **形象是本机偏好，不进库、不进 op。** 行程是多人共用的，精灵不是——同伴不该因为我
 *    换了个头像就被改了界面。所以这里只有 `localStorage`，没有 socket。
 * 2. **换的是"画法 + 配色"，不换"表情协议"。** 每个形象都必须画出 `.eye`、`.brow-l/-r`、
 *    `.beak-lo`、`.bob`、`.think` 这几处，`PetArt.vue` 里那套 `data-mood` 规则才认得它。
 *    少一个部位，那个形象就会在助手说话时面无表情。
 * 3. **画身色是插画配色，不是界面配色。** 深色态刻意不反色（跟 `--photo-scrim` 同一个理由）：
 *    一只会眨眼的图在两种主题下是同一张图。跟着主题走的只有描边，它用 `--ink`。
 */

export type PetSkinId = 'bird' | 'cloud' | 'seal' | 'turtle'

/**
 * 'talk' 只在这台机器上存在：后端线协议里没有这个值，它由 Sprite.vue 在每句话开口的那
 * 900ms 里临时给。`types/assistant.ts` 是 pydantic 的镜像，为了一个表情去改它不值。
 */
export type PetMood = AssistantMood | 'talk'

export interface PetSkin {
  id: PetSkinId
  /** 形象名同时是精灵的名字：面板标题、气泡署名、aria-label 都从这里取。 */
  name: string
  /** 形象选择条上的一句话说明。 */
  note: string
  body: string
  shade: string
  /** 嘴（非鸟类形象即口部主色）。 */
  beak: string
  beakDeep: string
  blush: string
}

export const PET_SKINS: readonly PetSkin[] = [
  {
    id: 'bird',
    name: '游游',
    note: '冠羽和三角喙，会歪头',
    body: '#f6d36b',
    shade: '#e8bd43',
    beak: '#e8763c',
    beakDeep: '#cf5f2a',
    blush: '#e88a5a',
  },
  {
    id: 'cloud',
    name: '团团',
    note: '三个鼓包一块平底，脚下挂一滴雨',
    body: '#dcecf7',
    shade: '#a9cde3',
    beak: '#d98a7a',
    beakDeep: '#a85a4c',
    blush: '#e88a5a',
  },
  {
    id: 'seal',
    name: '海达',
    note: '光头无耳，一副口鼻垫和六根须',
    body: '#c9d0d6',
    shade: '#a3adb6',
    beak: '#8a7b83',
    beakDeep: '#4a4147',
    blush: '#e88a5a',
  },
  {
    id: 'turtle',
    name: '小满',
    note: '背着一块六格龟甲，走路探头',
    body: '#7fae72',
    shade: '#4e8352',
    beak: '#e0a94a',
    beakDeep: '#b07c22',
    blush: '#e88a5a',
  },
] as const

const STORE_KEY = 'tourplanopt.pet.skin'

/** 隐私模式下 localStorage 本身就会抛，不是只有读写会失败（同 useAmapHealth）。 */
function readStoredId(): string {
  try {
    return localStorage.getItem(STORE_KEY) ?? ''
  } catch {
    return ''
  }
}

function writeStoredId(id: PetSkinId): void {
  try {
    localStorage.setItem(STORE_KEY, id)
  } catch {
    /* 存不下就算了：最坏结果是下次回到默认形象 */
  }
}

const stored = readStoredId()

export const petSkin = ref<PetSkin>(
  PET_SKINS.find((s) => s.id === stored) ?? PET_SKINS[0],
)

/** 选不存在的 id 就当没说过这句话：绝不让 petSkin 落到 undefined。 */
export function setPetSkin(id: PetSkinId): void {
  const next = PET_SKINS.find((s) => s.id === id)
  if (!next || next.id === petSkin.value.id) return
  petSkin.value = next
  writeStoredId(next.id)
}

/** 把一只形象摊成 CSS 变量。选择条上要画没被选中的形象，所以它是按入参算的纯函数。 */
export function petSkinVars(skin: PetSkin): Record<string, string> {
  return {
    '--pet-body': skin.body,
    '--pet-shade': skin.shade,
    '--pet-beak': skin.beak,
    '--pet-beak-deep': skin.beakDeep,
    '--pet-blush': skin.blush,
  }
}
