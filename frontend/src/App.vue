<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'

import DialogHost from '@/components/DialogHost.vue'
import PhotoBackdrop from '@/components/PhotoBackdrop.vue'
import SettingsPanel from '@/components/SettingsPanel.vue'
import ToastHost from '@/components/ToastHost.vue'

const route = useRoute()
</script>

<template>
  <!-- 照片背景在路由出口之外：换页时它照常走自己的 144s 时间表，不闪不断
       （同下面几个 host 的理由）。 -->
  <PhotoBackdrop />
  <!-- out-in：两个视图都是撑满一屏的 flex 布局，交叉淡入会瞬间顶出双份高度。
       进场只淡不做位移——根节点带 transform 会让页面里 position: fixed 的浮层
       （FAB、通知）改以页面为参照，滚动一下就跑到文档底部去了。 -->
  <RouterView v-slot="{ Component }">
    <Transition name="view" mode="out-in">
      <!-- 按 path 加 key：/trip/A↔/trip/B 只差参数时 Vue 默认复用同一 TripView 实例，
           onMounted/onBeforeUnmount 不重跑，房间连接和 store 数据会停在上一趟行程。
           刻意不用 fullPath：行程页把当前页签写进 ?pane=，按 fullPath 加 key 会让换一次
           页签整页重建一次（重拉快照、重连 WS、地图销毁重建、进场动画重播），手机上读起来
           就是「每点一下都刷了一次」。页签只是显示切换，路由参数留在地址栏里供深链，
           由 TripView 自己 watch 回来。 -->
      <component :is="Component" :key="route.path" />
    </Transition>
  </RouterView>
  <!-- 回执、对话框与设置面板属于应用，不属于某个视图：换页时不该跟着闪掉，
       也不能落进上面那个带 transform 的过渡容器里。 -->
  <ToastHost />
  <DialogHost />
  <SettingsPanel />
</template>
