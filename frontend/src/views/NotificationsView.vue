<template>
  <AppShell title="消息中心" description="查看会议确认、变更与取消通知。消息仅对当前登录用户可见。" eyebrow="协作 / 消息">
    <template #actions>
      <button class="ui-button ui-button--outline" type="button" :disabled="markingAll || unreadCount === 0" @click="markAllRead">
        <CheckCheck :size="16" aria-hidden="true" />{{ markingAll ? '正在处理…' : '全部标为已读' }}
      </button>
    </template>

    <section class="notification-toolbar content-panel" aria-label="消息筛选">
      <div class="segmented-control" aria-label="阅读状态">
        <button type="button" :class="{ active: !unreadOnly }" :aria-pressed="!unreadOnly" @click="setUnreadOnly(false)">全部</button>
        <button type="button" :class="{ active: unreadOnly }" :aria-pressed="unreadOnly" @click="setUnreadOnly(true)">未读 <span v-if="unreadCount > 0">{{ unreadCount }}</span></button>
      </div>
      <label><span>消息类型</span><select v-model="typeFilter" @change="applyTypeFilter"><option value="">全部类型</option><option value="MEETING_CONFIRMED">会议已确认</option><option value="MEETING_CHANGED">会议已变更</option><option value="MEETING_CANCELLED">会议已取消</option></select></label>
      <button class="icon-button" type="button" title="刷新消息" aria-label="刷新消息" :disabled="loading" @click="loadNotifications"><RefreshCw :size="17" aria-hidden="true" /></button>
    </section>

    <p v-if="actionError" class="error-message notification-action-error" role="alert">{{ actionError }}</p>
    <ErrorState v-if="listError" :message="listError" retryable @retry="loadNotifications" />
    <div v-else-if="loading" class="feedback-state" aria-live="polite"><span class="spinner" aria-hidden="true" />正在加载消息…</div>
    <EmptyState v-else-if="notifications.length === 0" :title="unreadOnly ? '没有未读消息' : '消息中心还是空的'" :description="unreadOnly ? '当前消息都已处理，可以切换到“全部”查看历史。' : '会议确认、变更或取消后，相关通知会显示在这里。'" icon="check" />
    <section v-else class="notification-list" aria-label="消息列表">
      <article v-for="notification in notifications" :key="notification.id" class="notification-card" :class="{ 'notification-card--unread': notification.readAt === null }">
        <div class="notification-icon" :class="`notification-icon--${notificationTone(notification.type)}`" aria-hidden="true"><component :is="notificationIcon(notification.type)" :size="18" /></div>
        <div class="notification-card__body">
          <header><div><p class="eyebrow">{{ notificationTypeLabel(notification.type) }}</p><h2>{{ notification.title }}</h2></div><span v-if="notification.readAt === null" class="unread-dot"><span class="sr-only">未读</span></span></header>
          <p>{{ notification.content }}</p>
          <footer><time :datetime="notification.createdAt">{{ formatDateTime(notification.createdAt) }}</time><div class="notification-actions"><button v-if="notification.readAt === null" class="text-button" type="button" :disabled="pendingIds.has(notification.id)" @click="markRead(notification)">标为已读</button><button v-if="notification.relatedMeetingId !== null" class="text-button" type="button" @click="openMeeting(notification)">查看会议<ArrowUpRight :size="14" aria-hidden="true" /></button></div></footer>
        </div>
      </article>

      <footer class="pagination-bar">
        <span>共 {{ total }} 条消息 · 第 {{ page }} / {{ totalPages }} 页</span>
        <div><button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="15" aria-hidden="true" />上一页</button><button class="ui-button ui-button--outline ui-button--sm" type="button" :disabled="page >= totalPages" @click="changePage(page + 1)">下一页<ChevronRight :size="15" aria-hidden="true" /></button></div>
      </footer>
    </section>
  </AppShell>
</template>

<script setup lang="ts">
import { ArrowUpRight, CalendarCheck2, CalendarClock, CalendarX2, CheckCheck, ChevronLeft, ChevronRight, RefreshCw } from '@lucide/vue'
import { computed, onMounted, ref, type Component } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, apiRequest } from '@/api/client'
import type { NotificationItem, NotificationListResult, NotificationReadAllResult, NotificationType } from '@/api/types'
import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import { notificationStore } from '@/notifications/store'
import { formatDateTime } from '@/utils/format'

const PAGE_SIZE = 20
const router = useRouter()
const notifications = ref<NotificationItem[]>([])
const total = ref(0)
const unreadCount = ref(0)
const page = ref(1)
const unreadOnly = ref(false)
const typeFilter = ref('')
const loading = ref(true)
const markingAll = ref(false)
const listError = ref('')
const actionError = ref('')
const pendingIds = ref(new Set<number>())
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function message(error: unknown, fallback: string): string { return error instanceof ApiError ? error.message : fallback }
function notificationTypeLabel(type: NotificationType): string { return ({ MEETING_CONFIRMED: '会议已确认', MEETING_CHANGED: '会议已变更', MEETING_CANCELLED: '会议已取消' })[type] }
function notificationTone(type: NotificationType): string { return type === 'MEETING_CONFIRMED' ? 'success' : type === 'MEETING_CHANGED' ? 'info' : 'danger' }
function notificationIcon(type: NotificationType): Component { return type === 'MEETING_CONFIRMED' ? CalendarCheck2 : type === 'MEETING_CHANGED' ? CalendarClock : CalendarX2 }

async function loadNotifications(): Promise<void> {
  loading.value = true; listError.value = ''; actionError.value = ''
  const query = new URLSearchParams({ unreadOnly: String(unreadOnly.value), page: String(page.value), size: String(PAGE_SIZE) })
  if (typeFilter.value) query.set('type', typeFilter.value)
  try {
    const result = await apiRequest<NotificationListResult>(`/notifications?${query.toString()}`)
    notifications.value = result.items; total.value = result.total; unreadCount.value = result.unreadCount
    notificationStore.setUnreadCount(result.unreadCount)
  } catch (error) { listError.value = message(error, '消息加载失败，请稍后重试。') }
  finally { loading.value = false }
}
function setUnreadOnly(value: boolean): void { unreadOnly.value = value; page.value = 1; void loadNotifications() }
function applyTypeFilter(): void { page.value = 1; void loadNotifications() }
function changePage(value: number): void { page.value = value; void loadNotifications() }

async function markRead(notification: NotificationItem): Promise<void> {
  if (pendingIds.value.has(notification.id)) return
  pendingIds.value = new Set(pendingIds.value).add(notification.id); actionError.value = ''
  try {
    const updated = await apiRequest<NotificationItem>(`/notifications/${notification.id}/read`, { method: 'PATCH' })
    const index = notifications.value.findIndex((item) => item.id === notification.id)
    if (unreadOnly.value) { notifications.value = notifications.value.filter((item) => item.id !== notification.id); total.value = Math.max(0, total.value - 1) }
    else if (index >= 0) notifications.value[index] = updated
    unreadCount.value = Math.max(0, unreadCount.value - (notification.readAt === null ? 1 : 0)); notificationStore.setUnreadCount(unreadCount.value)
    if (notifications.value.length === 0 && page.value > 1) { page.value -= 1; await loadNotifications() }
  } catch (error) { actionError.value = message(error, '标记已读失败。') }
  finally { const next = new Set(pendingIds.value); next.delete(notification.id); pendingIds.value = next }
}
async function markAllRead(): Promise<void> {
  if (markingAll.value || unreadCount.value === 0) return
  markingAll.value = true; actionError.value = ''
  try {
    await apiRequest<NotificationReadAllResult>('/notifications/read-all', { method: 'PATCH' })
    notificationStore.setUnreadCount(0); unreadCount.value = 0
    await loadNotifications()
  } catch (error) { actionError.value = message(error, '全部标记已读失败。') }
  finally { markingAll.value = false }
}
async function openMeeting(notification: NotificationItem): Promise<void> {
  if (notification.readAt === null) await markRead(notification)
  if (notification.relatedMeetingId !== null) await router.push({ name: 'meetings', query: { meetingId: String(notification.relatedMeetingId) } })
}

onMounted(() => { void loadNotifications() })
</script>
