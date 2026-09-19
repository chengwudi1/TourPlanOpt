import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import LoginView from '@/views/LoginView.vue'
import TripView from '@/views/TripView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/trip/:tripId', name: 'trip', component: TripView, props: true },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
