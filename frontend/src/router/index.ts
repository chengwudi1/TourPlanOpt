import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import TripView from '@/views/TripView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/trip/:tripId', name: 'trip', component: TripView, props: true },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
