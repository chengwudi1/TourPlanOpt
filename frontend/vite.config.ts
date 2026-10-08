import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Dev only: proxy the API and the WebSocket to the FastAPI backend so the
// browser sees a single origin and no CORS is involved. In production the
// backend serves frontend/dist directly, so there is nothing to proxy.
//
// `base` 与 backend/app/config.py 的 frontend_prefix 是同一条决定的两处表达，必须一起改
// （test_url_prefix.py 盯着它们一致）。开发期同样带前缀：本地地址是
// http://127.0.0.1:5173/tourplanopt/，这样开发和线上验的是同一件事。
// /api、/ws、/uploads 走的是根绝对路径，不进前缀，所以 proxy 表和 fetch 调用都不用动。
export default defineConfig({
  base: '/tourplanopt/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // Bind every interface so a phone on the same Wi-Fi can open this. Windows
    // Defender will prompt on first bind; it must be allowed on Private.
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 本机上传的封面走 `/uploads/...`：不代理的话这张图会撞上 SPA 兜底页（200 + HTML），
      // 海报头只看到一个加载失败的 img，于是退回装饰——上传成功却看不见效果。
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
