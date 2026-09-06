import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
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
