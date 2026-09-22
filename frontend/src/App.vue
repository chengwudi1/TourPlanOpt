<script setup lang="ts">
import { RouterView } from 'vue-router'

import DialogHost from '@/components/DialogHost.vue'
import SettingsPanel from '@/components/SettingsPanel.vue'
import ToastHost from '@/components/ToastHost.vue'
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
  <!-- 回执、对话框与设置面板属于应用，不属于某个视图：换页时不该跟着闪掉，
       也不能落进上面那个带 transform 的过渡容器里。 -->
  <ToastHost />
  <DialogHost />
  <SettingsPanel />
</template>
