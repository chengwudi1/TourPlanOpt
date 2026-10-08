import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import LoginView from '@/views/LoginView.vue'
import TripView from '@/views/TripView.vue'

export const router = createRouter({
  // BASE_URL 即 vite.config.ts 的 base（/tourplanopt/）。少了这个参数，深链
  // /tourplanopt/trip/xxx 会撞上下面那条 catch-all 被静默兜回首页——不报错，但打不开。
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/trip/:tripId', name: 'trip', component: TripView, props: true },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
