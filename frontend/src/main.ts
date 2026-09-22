import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { useSettingsStore } from './stores/settings'
import { router } from './router'
// 拉丁与数字走本地打包的 variable 字体，中文仍用系统栈。
// wght.css 声明了全部子集，但 unicode-range 决定浏览器只下 latin 那一个文件。
import '@fontsource-variable/manrope/wght.css'
import '@fontsource-variable/bricolage-grotesque/wght.css'
import './styles/main.css'

const pinia = createPinia()
const app = createApp(App).use(pinia).use(router)

// 主题/字号/动效由这个 store 一手落地（含「跟随系统」时系统偏好中途变化的监听），
// 所以在挂载前就点着它：不依赖哪个页面恰好 import 了它，首帧之后 <html> 上的已解析值
// 立刻有人负责。挂载前那一次由 index.html 的内联脚本写好，两者是同一份算式。
useSettingsStore(pinia)

app.mount('#app')

// PWA：只在生产构建注册 service worker，开发期 HMR 不受影响。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // 注册失败不致命：应用照常运行，只是没有离线缓存。
    })
  })
}
