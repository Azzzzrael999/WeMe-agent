import { createRouter, createWebHistory } from 'vue-router'

import { authStore } from '../auth/store'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { guestOnly: true },
    },
    {
      path: '/rooms',
      name: 'rooms',
      component: () => import('../views/RoomsView.vue'),
      meta: { requiresAuth: true },
    },
    { path: '/', redirect: '/rooms' },
    { path: '/:pathMatch(.*)*', redirect: '/rooms' },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.requiresAuth) {
    if (!authStore.isAuthenticated.value) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    if (authStore.state.user === null) {
      try {
        await authStore.loadCurrentUser()
      } catch {
        authStore.clearSession()
        return { name: 'login', query: { redirect: to.fullPath } }
      }
    }
  }

  if (to.meta.guestOnly && authStore.isAuthenticated.value) {
    return { name: 'rooms' }
  }

  return true
})
