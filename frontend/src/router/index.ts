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
    {
      path: '/chat',
      name: 'chat',
      component: () => import('../views/ChatView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/meetings',
      name: 'meetings',
      component: () => import('../views/MeetingsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/agent/runs/:runId',
      name: 'agent-run',
      component: () => import('../views/AgentRunView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/approvals',
      name: 'approvals',
      component: () => import('../views/ApprovalsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/preview/replan',
      name: 'preview-replan',
      component: () => import('../views/ReplanPreviewView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/preview/meeting-lifecycle',
      name: 'preview-lifecycle',
      component: () => import('../views/MeetingLifecyclePreviewView.vue'),
      meta: { requiresAuth: true },
    },
    { path: '/', redirect: '/chat' },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
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
    return { name: 'chat' }
  }

  return true
})
