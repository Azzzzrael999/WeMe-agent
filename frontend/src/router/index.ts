import { createRouter, createWebHistory } from 'vue-router'

import { authStore } from '../auth/store'

const CHAT_ACTIVE_RUN_STORAGE_KEY = 'meetops.chat-active-run.v1'
const SAFE_RUN_ID = /^[A-Za-z0-9_-]{1,64}$/

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

router.beforeEach(async (to, from) => {
  const leavingRunId = from.query.runId
  if (
    from.name === 'chat'
    && to.name !== 'chat'
    && typeof leavingRunId === 'string'
    && SAFE_RUN_ID.test(leavingRunId)
  ) {
    window.sessionStorage.setItem(CHAT_ACTIVE_RUN_STORAGE_KEY, leavingRunId)
  }

  if (to.name === 'chat' && to.query.runId === undefined) {
    const activeRunId = window.sessionStorage.getItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
    if (activeRunId !== null && SAFE_RUN_ID.test(activeRunId)) {
      return { name: 'chat', query: { ...to.query, runId: activeRunId } }
    }
  }

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
