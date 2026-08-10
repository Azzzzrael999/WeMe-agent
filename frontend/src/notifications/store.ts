import { reactive } from 'vue'

import { apiRequest } from '@/api/client'
import type { NotificationListResult } from '@/api/types'
import { isTechnicalDemoNotification } from '@/utils/labels'

const state = reactive({ unreadCount: 0, loading: false })

async function refresh(): Promise<void> {
  if (state.loading) return
  state.loading = true
  try {
    const result = await apiRequest<NotificationListResult>('/notifications?unreadOnly=true&page=1&size=100')
    state.unreadCount = result.items.filter((item) => !isTechnicalDemoNotification(item.title, item.content)).length
  } finally {
    state.loading = false
  }
}

function setUnreadCount(value: number): void {
  state.unreadCount = Math.max(0, value)
}

export const notificationStore = { state, refresh, setUnreadCount }
