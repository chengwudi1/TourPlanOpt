import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
// Poppins 只带拉丁与数字（中文走系统栈），按需引 4 个字重，不拉无关子集。
import '@fontsource/poppins/latin-400.css'
import '@fontsource/poppins/latin-500.css'
import '@fontsource/poppins/latin-600.css'
import '@fontsource/poppins/latin-700.css'
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
