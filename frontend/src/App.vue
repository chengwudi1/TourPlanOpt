<script setup lang="ts">
import { RouterView } from 'vue-router'
</script>

<template>
  <!-- out-in：两个视图都是撑满一屏的 flex 布局，交叉淡入会瞬间顶出双份高度。
       进场只淡不做位移——根节点带 transform 会让页面里 position: fixed 的浮层
       （FAB、通知）改以页面为参照，滚动一下就跑到文档底部去了。 -->
  <RouterView v-slot="{ Component }">
    <Transition name="view" mode="out-in">
      <component :is="Component" />
    </Transition>
  </RouterView>
</template>
