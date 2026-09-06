import { acceptHMRUpdate, defineStore } from 'pinia'
import { ref } from 'vue'

import { apiFetch, postJson } from '@/utils/api'

export interface User {
  id: string
  name: string
}

export interface MyTrip {
  id: string
  title: string
  city: string
  last_seen: string
  place_count: number
  owned: number
}

/**
 * Optional accounts. Guests keep the full share-link flow; an account buys the
 * 「我的行程」 history. The session is a httpOnly cookie -- this store just mirrors
 * GET /api/auth/me so the UI can react.
 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loaded = ref(false)
  const busy = ref(false)
  const error = ref<{ message: string } | null>(null)

  async function load() {
    try {
      user.value = (await apiFetch<{ user: User | null }>('/api/auth/me')).user
    } catch {
      user.value = null
    } finally {
      loaded.value = true
    }
  }

  async function register(name: string, password: string) {
    busy.value = true
    error.value = null
    try {
      user.value = (await apiFetch<{ user: User }>('/api/auth/register', postJson({ name, password }))).user
    } catch (err) {
      error.value = { message: (err as Error).message }
      throw err
    } finally {
      busy.value = false
    }
  }

  async function login(name: string, password: string) {
    busy.value = true
    error.value = null
    try {
      user.value = (await apiFetch<{ user: User }>('/api/auth/login', postJson({ name, password }))).user
    } catch (err) {
      error.value = { message: (err as Error).message }
      throw err
    } finally {
      busy.value = false
    }
  }

  async function logout() {
    try {
      await apiFetch('/api/auth/logout', postJson({}))
    } finally {
      user.value = null
    }
  }

  async function myTrips(): Promise<MyTrip[]> {
    return (await apiFetch<{ trips: MyTrip[] }>('/api/auth/trips')).trips
  }

  return { user, loaded, busy, error, load, register, login, logout, myTrips }
})

if (import.meta.hot) {
  acceptHMRUpdate(useAuthStore, import.meta.hot)
}
