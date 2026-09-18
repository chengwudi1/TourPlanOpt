<script setup lang="ts">
/**
 * 小精灵的画法（M29c，第二版）。
 *
 * 第一版是闭着眼写的，看一眼就露馅：三个形象共用一张脸，云看着像兔子（那两片叶子是耳朵）、
 * 海豹看着像灰小鸡（头顶一根毛），而 idle 的眉毛往中间下压——默认表情是「生气」。
 * 这一版按「先看见再改」重画：每个形象有自己的轮廓、自己的五官比例、自己的动作签名。
 * 审校台 `docs/_sprite-sheet.mjs` 从本文件现抽 SVG 出图，改完跑一遍就有截图。
 *
 * **协议**（缺一个那个形象就没表情，CSS 只认类名不认形象）：
 * `.bob` 呼吸、`.eye` 眨眼、`.brow-l/-r` 挑眉、`.beak-lo` 说话时开合、`.think` 思考时冒点。
 *
 * 每个形象另外带一个**只有它有**的动作：鸟扇翅 `.wing`、云下沉 `.drop`、海豹摆尾 `.fluke`
 * 与抖须 `.whisker`、龟探头 `.head`。这是「换了个形象」和「换了个颜色」的分界。
 */
import type { PetMood, PetSkinId } from '@/components/pet/skins'

defineProps<{ skin: PetSkinId; mood: PetMood }>()
</script>

<template>
  <svg class="pet" viewBox="0 0 100 100" :data-mood="mood">
    <ellipse class="shadow" cx="50" cy="94" rx="23" ry="4.5" fill="var(--ink)" />

    <!-- 游游 · 雏鸟：蛋形身 + 三角喙 + 两撮冠羽 + 两只三趾脚。 -->
    <g v-if="skin === 'bird'" class="bob">
      <g class="tuft" fill="var(--pet-shade)" stroke="var(--ink)" stroke-width="2.4" stroke-linejoin="round">
        <path d="M45 20 C 42 10 47 3 55 5 C 49 7 46 12 47 20Z" />
        <path d="M55 20 C 57 12 63 8 68 10 C 63 12 60 15 60 21Z" />
      </g>
      <path
        d="M50 17 C 71 17 84 36 84 57 C 84 78 69 91 50 91 C 31 91 16 78 16 57 C 16 36 29 17 50 17Z"
        fill="var(--pet-body)"
        stroke="var(--ink)"
        stroke-width="3"
      />
      <path
        d="M50 53 C 62 53 71 63 71 73 C 71 83 61 88 50 88 C 39 88 29 83 29 73 C 29 63 38 53 50 53Z"
        fill="var(--surface)"
        opacity=".3"
      />
      <path
        class="wing"
        d="M19 58 C 11 62 10 77 19 81 C 24 73 24 62 19 58Z"
        fill="var(--pet-shade)"
        stroke="var(--ink)"
        stroke-width="2.4"
      />
      <path
        d="M81 58 C 89 62 90 77 81 81 C 76 73 76 62 81 58Z"
        fill="var(--pet-shade)"
        stroke="var(--ink)"
        stroke-width="2.4"
      />
      <g
        class="feet"
        stroke="var(--pet-beak-deep)"
        stroke-width="3"
        stroke-linecap="round"
        fill="none"
      >
        <path d="M42 86 L42 93 M42 93 L37.5 96 M42 93 L46.5 96" />
        <path d="M58 86 L58 93 M58 93 L53.5 96 M58 93 L62.5 96" />
      </g>
      <path class="brow brow-l" d="M30 36 Q36.5 32.5 43 35.5" stroke="var(--ink)" stroke-width="3" stroke-linecap="round" fill="none" />
      <path class="brow brow-r" d="M70 36 Q63.5 32.5 57 35.5" stroke="var(--ink)" stroke-width="3" stroke-linecap="round" fill="none" />
      <g class="eye">
        <ellipse cx="37" cy="45" rx="6.4" ry="6.8" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
        <circle cx="37.8" cy="45.6" r="3" fill="var(--ink)" />
        <circle cx="39.2" cy="43.6" r="1.2" fill="var(--surface)" />
      </g>
      <g class="eye">
        <ellipse cx="63" cy="45" rx="6.4" ry="6.8" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
        <circle cx="63.8" cy="45.6" r="3" fill="var(--ink)" />
        <circle cx="65.2" cy="43.6" r="1.2" fill="var(--surface)" />
      </g>
      <g class="beak">
        <path
          d="M41 57 Q50 52.5 59 57 Q50 63 41 57Z"
          fill="var(--pet-beak)"
          stroke="var(--ink)"
          stroke-width="2.2"
          stroke-linejoin="round"
        />
        <path
          class="beak-lo"
          d="M44 60.5 Q50 66 56 60.5 Q50 62.5 44 60.5Z"
          fill="var(--pet-beak-deep)"
          stroke="var(--ink)"
          stroke-width="1.8"
          stroke-linejoin="round"
        />
      </g>
      <circle cx="27" cy="52" r="4.6" fill="var(--pet-blush)" opacity=".42" />
      <circle cx="73" cy="52" r="4.6" fill="var(--pet-blush)" opacity=".42" />
    </g>

    <!-- 团团 · 云：三个鼓包一块平底 + 一滴雨。没有耳朵、没有脚，轮廓必须一眼是云。
         240px 下看清的两处错：右侧那道风纹糊成一坨（删掉），嘴用了橙色三角（那是喙，
         云上该是一张玫瑰色的圆口）。 -->
    <g v-else-if="skin === 'cloud'" class="bob bob--puff">
      <path
        d="M27 66 C 15 66 10 53 20 47 C 15 35 27 26 36 32 C 40 19 58 17 63 28 C 74 21 86 31 81 43 C 91 46 90 61 79 62 C 76 68 66 69 62 66Z"
        fill="var(--pet-body)"
        stroke="var(--ink)"
        stroke-width="3"
        stroke-linejoin="round"
      />
      <path
        d="M36 32 C 40 19 58 17 63 28 C 55 25 43 27 36 32Z"
        fill="var(--surface)"
        opacity=".5"
      />
      <g class="drop">
        <path
          d="M50 70 C 55 77 55 82 50 84.5 C 45 82 45 77 50 70Z"
          fill="var(--pet-shade)"
          stroke="var(--ink)"
          stroke-width="2.2"
          stroke-linejoin="round"
        />
      </g>
      <path class="brow brow-l" d="M31 41 Q36.5 37.5 42 40.5" stroke="var(--ink)" stroke-width="2.8" stroke-linecap="round" fill="none" />
      <path class="brow brow-r" d="M69 41 Q63.5 37.5 58 40.5" stroke="var(--ink)" stroke-width="2.8" stroke-linecap="round" fill="none" />
      <g class="eye">
        <ellipse cx="37" cy="49" rx="5.4" ry="5.8" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
        <circle cx="37.8" cy="49.6" r="2.8" fill="var(--ink)" />
        <circle cx="39" cy="47.9" r="1.1" fill="var(--surface)" />
      </g>
      <g class="eye">
        <ellipse cx="63" cy="49" rx="5.4" ry="5.8" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
        <circle cx="63.8" cy="49.6" r="2.8" fill="var(--ink)" />
        <circle cx="65" cy="47.9" r="1.1" fill="var(--surface)" />
      </g>
      <g class="beak">
        <path
          d="M45.5 56.5 Q50 54.5 54.5 56.5 Q50 62.5 45.5 56.5Z"
          fill="var(--pet-beak)"
          stroke="var(--ink)"
          stroke-width="2.2"
          stroke-linejoin="round"
        />
        <path
          class="beak-lo"
          d="M47 58.6 Q50 62.6 53 58.6 Q50 60 47 58.6Z"
          fill="var(--pet-beak-deep)"
          stroke="var(--ink)"
          stroke-width="1.6"
          stroke-linejoin="round"
        />
      </g>
      <circle cx="28" cy="56" r="4.2" fill="var(--pet-blush)" opacity=".4" />
      <circle cx="72" cy="56" r="4.2" fill="var(--pet-blush)" opacity=".4" />
    </g>

    <!-- 海达 · 海豹：水滴身 + 无耳光头 + 口鼻垫 + 尾鳍。光滑的头是它和鸟的分界。 -->
    <g v-else-if="skin === 'seal'" class="bob bob--waddle">
      <g class="fluke">
        <path
          d="M50 80 C 41 84 34 88 33 93 C 40 94 47 91 50 87 C 53 91 60 94 67 93 C 66 88 59 84 50 80Z"
          fill="var(--pet-shade)"
          stroke="var(--ink)"
          stroke-width="2.4"
          stroke-linejoin="round"
        />
      </g>
      <path
        d="M50 15 C 68 15 79 27 79 42 C 79 55 73 64 66 73 C 61 80 55 85 50 85 C 45 85 39 80 34 73 C 27 64 21 55 21 42 C 21 27 32 15 50 15Z"
        fill="var(--pet-body)"
        stroke="var(--ink)"
        stroke-width="3"
      />
      <path
        d="M50 15 C 64 15 74 24 77 36 C 68 30 58 28 50 28 C 42 28 32 30 23 36 C 26 24 36 15 50 15Z"
        fill="var(--surface)"
        opacity=".3"
      />
      <path
        class="wing"
        d="M24 50 C 16 56 16 70 25 73 C 28 65 28 56 24 50Z"
        fill="var(--pet-shade)"
        stroke="var(--ink)"
        stroke-width="2.4"
      />
      <path
        d="M76 50 C 84 56 84 70 75 73 C 72 65 72 56 76 50Z"
        fill="var(--pet-shade)"
        stroke="var(--ink)"
        stroke-width="2.4"
      />
      <ellipse cx="50" cy="57" rx="16" ry="11" fill="var(--surface)" opacity=".42" />
      <g class="whisker" stroke="var(--ink)" stroke-width="1.7" stroke-linecap="round" opacity=".55">
        <path d="M36 55 L27 52.5" />
        <path d="M36 58.5 L26 59" />
        <path d="M36 62 L28 65" />
        <path d="M64 55 L73 52.5" />
        <path d="M64 58.5 L74 59" />
        <path d="M64 62 L72 65" />
      </g>
      <path class="brow brow-l" d="M32 35 Q37 31.5 42.5 34" stroke="var(--ink)" stroke-width="2.8" stroke-linecap="round" fill="none" />
      <path class="brow brow-r" d="M68 35 Q63 31.5 57.5 34" stroke="var(--ink)" stroke-width="2.8" stroke-linecap="round" fill="none" />
      <g class="eye">
        <ellipse cx="41" cy="43" rx="5.6" ry="6" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
        <circle cx="41.6" cy="43.6" r="3.2" fill="var(--ink)" />
        <circle cx="43" cy="41.7" r="1.2" fill="var(--surface)" />
      </g>
      <g class="eye">
        <ellipse cx="59" cy="43" rx="5.6" ry="6" fill="var(--surface)" stroke="var(--ink)" stroke-width="2.2" />
        <circle cx="59.6" cy="43.6" r="3.2" fill="var(--ink)" />
        <circle cx="61" cy="41.7" r="1.2" fill="var(--surface)" />
      </g>
      <path
        d="M47 51.5 Q50 49.5 53 51.5 Q50 54.5 47 51.5Z"
        fill="var(--pet-beak-deep)"
        stroke="var(--ink)"
        stroke-width="1.8"
        stroke-linejoin="round"
      />
      <g class="beak">
        <path
          d="M42 56 Q46 60 50 56.5 Q54 60 58 56"
          fill="none"
          stroke="var(--ink)"
          stroke-width="2.4"
          stroke-linecap="round"
        />
        <path
          class="beak-lo"
          d="M46 58 Q50 63.5 54 58 Q50 60 46 58Z"
          fill="var(--pet-beak)"
          stroke="var(--ink)"
          stroke-width="1.8"
          stroke-linejoin="round"
        />
      </g>
      <circle cx="33" cy="55" r="4.4" fill="var(--pet-blush)" opacity=".38" />
      <circle cx="67" cy="55" r="4.4" fill="var(--pet-blush)" opacity=".38" />
    </g>

    <!-- 小满 · 海龟：龟甲 + 六块甲片 + 从甲下伸出的头与四只鳍脚。
         240px 下看清的三处错：头太大太高地坐在甲上面（像个气球）、嘴被甲沿盖住
         （说话时看不见）、甲片线太浅（读不出是龟甲）。 -->
    <g v-else class="bob bob--crawl">
      <g class="legs" fill="var(--pet-shade)" stroke="var(--ink)" stroke-width="2.4" stroke-linejoin="round">
        <path d="M24 68 C 14 71 11 80 17 84 C 24 81 28 74 29 69Z" />
        <path d="M76 68 C 86 71 89 80 83 84 C 76 81 72 74 71 69Z" />
        <path d="M36 74 C 32 82 34 90 41 89 C 43 83 41 77 39 73Z" />
        <path d="M64 74 C 68 82 66 90 59 89 C 57 83 59 77 61 73Z" />
      </g>
      <g class="head">
        <ellipse cx="50" cy="26" rx="9.6" ry="8.6" fill="var(--pet-shade)" stroke="var(--ink)" stroke-width="2.8" />
        <path class="brow brow-l" d="M42.5 21 Q45 19.2 47.5 20.6" stroke="var(--ink)" stroke-width="2" stroke-linecap="round" fill="none" />
        <path class="brow brow-r" d="M57.5 21 Q55 19.2 52.5 20.6" stroke="var(--ink)" stroke-width="2" stroke-linecap="round" fill="none" />
        <g class="eye">
          <ellipse cx="46" cy="25.5" rx="3.2" ry="3.6" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.7" />
          <circle cx="46.5" cy="25.9" r="1.9" fill="var(--ink)" />
        </g>
        <g class="eye">
          <ellipse cx="54" cy="25.5" rx="3.2" ry="3.6" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.7" />
          <circle cx="54.5" cy="25.9" r="1.9" fill="var(--ink)" />
        </g>
        <circle cx="42.5" cy="29" r="2.6" fill="var(--pet-blush)" opacity=".42" />
        <circle cx="57.5" cy="29" r="2.6" fill="var(--pet-blush)" opacity=".42" />
        <g class="beak">
          <path
            class="beak-lo"
            d="M46.5 29.6 Q50 33 53.5 29.6 Q50 31 46.5 29.6Z"
            fill="var(--pet-beak)"
            stroke="var(--ink)"
            stroke-width="1.6"
            stroke-linejoin="round"
          />
        </g>
      </g>
      <path
        d="M18 64 C 18 44 32 34 50 34 C 68 34 82 44 82 64 C 82 72 74 76 50 76 C 26 76 18 72 18 64Z"
        fill="var(--pet-body)"
        stroke="var(--ink)"
        stroke-width="3"
      />
      <g class="shell" fill="none" stroke="var(--ink)" stroke-width="2.4" stroke-linejoin="round" opacity=".34">
        <path d="M50 37 L60 46 L55 58 L45 58 L40 46Z" />
        <path d="M40 46 L28 50 L26 62 L45 58Z" />
        <path d="M60 46 L72 50 L74 62 L55 58Z" />
        <path d="M45 58 L43 72 M55 58 L57 72" />
      </g>
      <path
        d="M18 64 C 28 72 72 72 82 64"
        fill="none"
        stroke="var(--ink)"
        stroke-width="2.4"
        opacity=".5"
      />
    </g>

    <g class="think">
      <circle cx="76" cy="20" r="3.2" fill="var(--accent)" />
      <circle cx="85" cy="14" r="3.2" fill="var(--accent)" />
      <circle cx="93" cy="8" r="3.2" fill="var(--accent)" />
    </g>
  </svg>
</template>

<style scoped>
.pet {
  overflow: visible;
}

.pet .shadow {
  opacity: 0.16;
  transform-box: fill-box;
  transform-origin: center;
  animation: pet-shadow 3.4s ease-in-out infinite;
}

.pet .bob {
  transform-box: fill-box;
  transform-origin: center bottom;
  animation: pet-bob 3.4s ease-in-out infinite;
}

/* 动作签名：每个形象只有它自己那一个，节奏和形变方式都不同。
   「换了形象」要连呼吸一起换，否则只是换了色。 */
.pet .bob--puff {
  animation: pet-puff 5.2s ease-in-out infinite;
}

.pet .bob--waddle {
  animation: pet-waddle 4.1s ease-in-out infinite;
}

.pet .bob--crawl {
  animation: pet-crawl 6s ease-in-out infinite;
}

.pet .eye {
  transform-box: fill-box;
  transform-origin: center;
  animation: pet-blink 5.4s infinite;
}

.pet .tuft {
  transform-box: fill-box;
  transform-origin: bottom center;
  animation: pet-sway 2.6s ease-in-out infinite;
}

.pet .wing {
  transform-box: fill-box;
  transform-origin: top center;
  animation: pet-flap 3.4s ease-in-out infinite;
}

.pet .brow {
  transform-box: fill-box;
  transition: transform var(--dur) var(--ease-pop);
}

.pet .beak-lo {
  transform-box: fill-box;
  transform-origin: top center;
}

.pet .drop {
  transform-box: fill-box;
  transform-origin: top center;
  animation: pet-dangle 2.9s ease-in-out infinite;
}

.pet .fluke {
  transform-box: fill-box;
  transform-origin: top center;
  animation: pet-flick 3.2s ease-in-out infinite;
}

.pet .whisker {
  transform-box: fill-box;
  transform-origin: center;
  animation: pet-twitch 4.8s ease-in-out infinite;
}

.pet .head {
  transform-box: fill-box;
  transform-origin: center bottom;
  animation: pet-poke 6s ease-in-out infinite;
}

.pet .think {
  opacity: 0;
  transition: opacity var(--dur);
}

.pet .think circle {
  transform-box: fill-box;
}

@keyframes pet-bob {
  0%,
  100% {
    transform: translateY(0) scaleY(1);
  }
  50% {
    transform: translateY(-4px) scaleY(1.03);
  }
}

@keyframes pet-puff {
  0%,
  100% {
    transform: translateY(0) scale(1, 1);
  }
  40% {
    transform: translateY(-5px) scale(0.975, 1.045);
  }
  70% {
    transform: translateY(2px) scale(1.03, 0.965);
  }
}

@keyframes pet-waddle {
  0%,
  100% {
    transform: rotate(-2.4deg) translateY(0);
  }
  50% {
    transform: rotate(2.4deg) translateY(-3px);
  }
}

@keyframes pet-crawl {
  0%,
  100% {
    transform: translateY(0) rotate(0);
  }
  35% {
    transform: translateY(-2px) rotate(-1.6deg);
  }
  70% {
    transform: translateY(-1px) rotate(1.6deg);
  }
}

@keyframes pet-shadow {
  0%,
  100% {
    transform: scaleX(1);
    opacity: 0.16;
  }
  50% {
    transform: scaleX(0.84);
    opacity: 0.1;
  }
}

@keyframes pet-blink {
  0%,
  93%,
  100% {
    transform: scaleY(1);
  }
  96% {
    transform: scaleY(0.08);
  }
}

@keyframes pet-sway {
  0%,
  100% {
    transform: rotate(-7deg);
  }
  50% {
    transform: rotate(9deg);
  }
}

@keyframes pet-flap {
  0%,
  100% {
    transform: rotate(0);
  }
  50% {
    transform: rotate(-9deg);
  }
}

@keyframes pet-dangle {
  0%,
  100% {
    transform: translateY(0) rotate(-4deg);
  }
  50% {
    transform: translateY(3px) rotate(4deg);
  }
}

@keyframes pet-breathe {
  0%,
  100% {
    transform: scale(1);
    opacity: 0.75;
  }
  50% {
    transform: scale(1.14);
    opacity: 1;
  }
}

@keyframes pet-flick {
  0%,
  62%,
  100% {
    transform: rotate(0);
  }
  72% {
    transform: rotate(-11deg);
  }
  84% {
    transform: rotate(8deg);
  }
}

@keyframes pet-twitch {
  0%,
  70%,
  100% {
    transform: translateX(0);
  }
  78% {
    transform: translateX(1.4px);
  }
  88% {
    transform: translateX(-1.2px);
  }
}

@keyframes pet-poke {
  0%,
  100% {
    transform: translateY(0);
  }
  30% {
    transform: translateY(-3.5px);
  }
  55% {
    transform: translateY(0.5px);
  }
}

@keyframes pet-orb {
  0%,
  100% {
    opacity: 0.25;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-4px);
  }
}

@keyframes pet-say {
  0%,
  100% {
    transform: scaleY(0.35);
  }
  50% {
    transform: scaleY(1.5);
  }
}

@keyframes pet-jitter {
  0%,
  100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-2.5px);
  }
  75% {
    transform: translateX(2.5px);
  }
}

[data-mood='thinking'] .think {
  opacity: 1;
}

[data-mood='thinking'] .think circle {
  animation: pet-orb 1.1s ease-in-out infinite;
}

[data-mood='thinking'] .think circle:nth-child(2) {
  animation-delay: 0.18s;
}

[data-mood='thinking'] .think circle:nth-child(3) {
  animation-delay: 0.36s;
}

[data-mood='thinking'] .brow-l {
  transform: rotate(-16deg) translateY(-3px);
}

[data-mood='thinking'] .brow-r {
  transform: rotate(12deg) translateY(-2px);
}

[data-mood='talk'] .beak-lo {
  animation: pet-say 0.28s ease-in-out infinite;
}

[data-mood='happy'] .brow-l {
  transform: rotate(9deg) translateY(1px);
}

[data-mood='happy'] .brow-r {
  transform: rotate(-9deg) translateY(1px);
}

[data-mood='happy'] .bob {
  animation-duration: 0.9s;
}

[data-mood='asking'] .brow-l {
  transform: rotate(-10deg) translateY(-2px);
}

[data-mood='warn'] .brow-l {
  transform: rotate(-24deg) translateY(-4px);
}

[data-mood='warn'] .brow-r {
  transform: rotate(24deg) translateY(-4px);
}

[data-mood='warn'] .bob {
  animation: pet-jitter 0.42s ease-in-out infinite;
}
</style>
