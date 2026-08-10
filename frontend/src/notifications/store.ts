import { reactive } from 'vue'

import { apiRequest } from '@/api/client'
import type { NotificationUnreadCountResult } from '@/api/types'

const state = reactive({ unreadCount: 0, loading: false })

async function refresh(): Promise<void> {
  if (state.loading) return
  state.loading = true
  try {
    const result = await apiRequest<NotificationUnreadCountResult>('/notifications/unread-count')
    state.unreadCount = result.unreadCount
  } finally {
    state.loading = false
  }
}

function setUnreadCount(value: number): void {
  state.unreadCount = Math.max(0, value)
}

export const notificationStore = { state, refresh, setUnreadCount }
