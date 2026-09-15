import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
// 拉丁与数字走本地打包的 variable 字体，中文仍用系统栈。
// wght.css 声明了全部子集，但 unicode-range 决定浏览器只下 latin 那一个文件。
import '@fontsource-variable/manrope/wght.css'
import '@fontsource-variable/bricolage-grotesque/wght.css'
import './styles/main.css'

createApp(App).use(createPinia()).use(router).mount('#app')

// PWA：只在生产构建注册 service worker，开发期 HMR 不受影响。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // 注册失败不致命：应用照常运行，只是没有离线缓存。
    })
  })
}
