<template>
  <div class="workspace-shell" :class="{ 'workspace-shell--collapsed': collapsed }">
    <aside
      ref="sidebarRef"
      id="workspace-navigation"
      class="workspace-sidebar"
      :class="{ 'workspace-sidebar--mobile-open': mobileOpen }"
      :aria-hidden="!mobileOpen && mobileViewport ? 'true' : undefined"
      :inert="!mobileOpen && mobileViewport"
    >
      <div class="workspace-brand">
        <div class="brand-mark" aria-hidden="true">W</div>
        <div class="workspace-brand__copy">
          <strong>WeMe</strong>
          <span>会议协作编排</span>
        </div>
        <button class="icon-button mobile-close" type="button" aria-label="关闭导航" @click="closeMobileNavigation()">
          <X :size="19" aria-hidden="true" />
        </button>
      </div>

      <nav class="workspace-nav" aria-label="主要功能">
        <section v-for="group in navigationGroups" :key="group.label">
          <p class="nav-group-label">{{ group.label }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="String(item.to.name)"
            :to="item.to"
            :title="item.label"
            @click="closeMobileNavigation()"
          >
            <component :is="item.icon" :size="18" aria-hidden="true" />
            <span class="nav-label">{{ item.label }}</span>
            <span
              v-if="item.to.name === 'notifications' && notificationStore.state.unreadCount > 0"
              class="nav-unread-badge"
              :aria-label="`${notificationStore.state.unreadCount} 条未读消息`"
            >{{ unreadBadgeLabel }}</span>
          </RouterLink>
        </section>

        <section v-if="activeRunId !== null">
          <p class="nav-group-label">系统</p>
          <RouterLink
            :to="{ name: 'agent-run', params: { runId: activeRunId } }"
            title="当前运行记录"
            @click="closeMobileNavigation()"
          >
            <Activity :size="18" aria-hidden="true" />
            <span class="nav-label">当前运行记录</span>
          </RouterLink>
        </section>

        <section class="conversation-navigation">
          <p class="nav-group-label">会话</p>
          <div class="workspace-primary-actions">
            <button
              class="new-orchestration-button"
              type="button"
              title="新建编排"
              @click="newOrchestration"
            >
              <Plus :size="18" aria-hidden="true" />
              <span class="nav-label">新建编排</span>
            </button>
            <label
              class="conversation-search"
              :class="{ 'conversation-search--collapsed': collapsed }"
              title="搜索会话"
            >
              <Search :size="17" aria-hidden="true" />
              <span class="sr-only">搜索最近任务</span>
              <input v-model.trim="searchQuery" type="search" placeholder="搜索会话" autocomplete="off" />
            </label>
          </div>

          <div v-if="recentTasks.length > 0" class="recent-task-section">
            <p class="recent-task-heading nav-label">最近任务</p>
            <RouterLink
              v-for="task in filteredRecentTasks"
              :key="task.threadId"
              class="recent-task-link"
              :to="{ name: 'chat', query: { runId: task.runId } }"
              :title="task.question"
              @click="closeMobileNavigation()"
            >
              <MessageCircle :size="17" aria-hidden="true" />
              <span class="recent-task-copy nav-label">
                <strong>{{ task.question }}</strong>
                <small>{{ taskStatusLabel(task.status) }}</small>
              </span>
              <ChevronRight class="recent-task-chevron" :size="15" aria-hidden="true" />
            </RouterLink>
            <p v-if="filteredRecentTasks.length === 0" class="recent-task-empty nav-label">没有匹配的任务</p>
          </div>
        </section>
      </nav>

      <div class="workspace-user">
        <div class="user-avatar" aria-hidden="true">{{ initials }}</div>
        <div class="workspace-user__copy">
          <strong>{{ authStore.state.user?.displayName }}</strong>
          <span>{{ authStore.state.user?.departmentName }} · {{ roleLabel }}</span>
        </div>
        <button class="icon-button" type="button" aria-label="退出登录" title="退出登录" @click="logout">
          <LogOut :size="18" aria-hidden="true" />
        </button>
      </div>
    </aside>

    <button
      v-if="mobileOpen"
      class="mobile-backdrop"
      type="button"
      aria-label="关闭导航"
      @click="closeMobileNavigation()"
    />

    <div class="workspace-main" :inert="mobileOpen && mobileViewport">
      <header class="workspace-topbar">
        <button
          class="icon-button desktop-toggle"
          type="button"
          :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'"
          :title="collapsed ? '展开侧边栏' : '折叠侧边栏'"
          @click="collapsed = !collapsed"
        >
          <PanelLeftOpen v-if="collapsed" :size="19" aria-hidden="true" />
          <PanelLeftClose v-else :size="19" aria-hidden="true" />
        </button>
        <button
          ref="mobileToggleRef"
          class="icon-button mobile-toggle"
          type="button"
          aria-label="打开导航"
          aria-controls="workspace-navigation"
          :aria-expanded="mobileOpen"
          @click="openMobileNavigation"
        >
          <Menu :size="20" aria-hidden="true" />
        </button>
        <div class="topbar-title">
          <span>{{ currentSectionTitle }}</span>
        </div>
      </header>
      <main class="workspace-content"><slot /></main>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  Activity,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  DoorOpen,
  ListChecks,
  LogOut,
  Menu,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RotateCcw,
  Search,
  Sparkles,
  UserRoundCog,
  Bell,
  BookOpenText,
  X,
} from '@lucide/vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { apiRequest } from '@/api/client'
import type { AgentThreadList } from '@/api/types'
import { authStore } from '@/auth/store'
import { notificationStore } from '@/notifications/store'

const CHAT_ACTIVE_RUN_STORAGE_KEY = 'weme.chat-active-run.v1'
const CHAT_ACTIVE_THREAD_STORAGE_KEY = 'weme.chat-active-thread.v1'
const CHAT_SUPPRESS_RESTORE_STORAGE_KEY = 'weme.chat-suppress-restore.v1'
const CHAT_RUN_CONTEXT_STORAGE_KEY = 'weme.chat-run-context.v1'
const CHAT_CONTEXT_EVENT = 'weme:chat-context-updated'
const NEW_CONVERSATION_EVENT = 'weme:new-conversation'
const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/

interface NavigationItem {
  label: string
  icon: Component
  to: { name: string }
}

interface RecentTask {
  runId: string
  threadId: string
  question: string
  searchText: string
  status: string
  updatedAt: number
}

const router = useRouter()
const route = useRoute()
const collapsed = ref(window.localStorage.getItem('weme.sidebar') === 'collapsed')
const mobileOpen = ref(false)
const mobileToggleRef = ref<HTMLButtonElement | null>(null)
const sidebarRef = ref<HTMLElement | null>(null)
const mobileViewport = ref(window.matchMedia('(max-width: 1024px)').matches)
const searchQuery = ref('')
const recentTasks = ref<RecentTask[]>([])
const activeRunId = ref<string | null>(null)

const navigationGroups = computed<{ label: string; items: NavigationItem[] }[]>(() => [
  {
    label: '工作台',
    items: [
      { label: '智能编排', icon: Sparkles, to: { name: 'chat' } },
      { label: '待我确认', icon: CheckCircle2, to: { name: 'approvals' } },
    ],
  },
  {
    label: '协作',
    items: [
      { label: '我的会议', icon: CalendarDays, to: { name: 'meetings' } },
      { label: '会议室', icon: DoorOpen, to: { name: 'rooms' } },
      { label: '消息中心', icon: Bell, to: { name: 'notifications' } },
      { label: '异常重排', icon: RotateCcw, to: { name: 'replan' } },
      { label: '会前会后', icon: ListChecks, to: { name: 'meeting-lifecycle' } },
      { label: '知识库', icon: BookOpenText, to: { name: 'knowledge-documents' } },
    ],
  },
  ...(authStore.state.user?.roles.includes('ADMIN') === true ? [{
    label: '管理',
    items: [{ label: '员工管理', icon: UserRoundCog, to: { name: 'admin-employees' } }],
  }] : []),
])

const roleLabel = computed(() => authStore.state.user?.roles.includes('ADMIN') ? '管理员' : '员工')
const initials = computed(() => authStore.state.user?.displayName.slice(0, 1) ?? 'M')
const unreadBadgeLabel = computed(() => notificationStore.state.unreadCount > 99 ? '99+' : String(notificationStore.state.unreadCount))
const filteredRecentTasks = computed(() => {
  const keyword = searchQuery.value.toLocaleLowerCase('zh-CN')
  if (keyword.length === 0) {
    return recentTasks.value
  }
  return recentTasks.value.filter((task) =>
    task.searchText.toLocaleLowerCase('zh-CN').includes(keyword)
    || task.runId.toLocaleLowerCase('zh-CN').includes(keyword),
  )
})
const currentSectionTitle = computed(() => {
  const labels: Record<string, string> = {
    chat: '智能编排',
    approvals: '待我确认',
    meetings: '我的会议',
    rooms: '会议室',
    notifications: '消息中心',
    'admin-employees': '员工管理',
    'agent-run': '运行记录',
    replan: '异常重排',
    'meeting-lifecycle': '会前会后',
    'knowledge-documents': '知识库',
  }
  return typeof route.name === 'string' ? labels[route.name] ?? 'WeMe' : 'WeMe'
})

function readLocalRecentTasks(): void {
  const storedActiveRun = window.sessionStorage.getItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
  activeRunId.value = storedActiveRun !== null && SAFE_ID.test(storedActiveRun) ? storedActiveRun : null

  try {
    const raw = window.sessionStorage.getItem(CHAT_RUN_CONTEXT_STORAGE_KEY)
    const parsed: unknown = raw === null ? {} : JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) {
      recentTasks.value = []
      return
    }
    const entries = Object.entries(parsed as Record<string, unknown>).flatMap(([runId, value], index) => {
      if (!SAFE_ID.test(runId) || typeof value !== 'object' || value === null) {
        return []
      }
      const context = value as Record<string, unknown>
      if (
        typeof context.threadId !== 'string'
        || !SAFE_ID.test(context.threadId)
        || typeof context.question !== 'string'
        || context.question.trim().length === 0
      ) {
        return []
      }
      return [{
        runId,
        threadId: context.threadId,
        question: context.question.trim(),
        searchText: context.question.trim(),
        status: typeof context.status === 'string' ? context.status : '',
        updatedAt: typeof context.updatedAt === 'number' ? context.updatedAt : index,
      }]
    }).sort((left, right) => left.updatedAt - right.updatedAt)

    const byThread = new Map<string, RecentTask>()
    for (const entry of entries) {
      const existing = byThread.get(entry.threadId)
      if (existing === undefined) {
        byThread.set(entry.threadId, entry)
        continue
      }
      byThread.set(entry.threadId, {
        ...existing,
        runId: entry.runId,
        status: entry.status,
        updatedAt: entry.updatedAt,
        searchText: `${existing.searchText}\n${entry.question}`,
      })
    }
    recentTasks.value = [...byThread.values()]
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .slice(0, 4)
  } catch {
    recentTasks.value = []
  }
}

async function readRecentTasks(): Promise<void> {
  const storedActiveRun = window.sessionStorage.getItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
  activeRunId.value = storedActiveRun !== null && SAFE_ID.test(storedActiveRun)
    ? storedActiveRun
    : null
  try {
    const result = await apiRequest<AgentThreadList>('/agent/threads?page=1&size=20')
    recentTasks.value = result.items.map((thread) => {
      const question = thread.title.trim() || thread.questionPreview.trim() || '未命名智能编排'
      const updatedAt = Date.parse(thread.updatedAt)
      return {
        runId: thread.latestRunId,
        threadId: thread.threadId,
        question,
        searchText: [thread.title, thread.questionPreview, thread.answerPreview ?? ''].join('\n'),
        status: thread.latestStatus,
        updatedAt: Number.isFinite(updatedAt) ? updatedAt : 0,
      }
    }).slice(0, 4)
    const suppressRestore = window.sessionStorage.getItem(CHAT_SUPPRESS_RESTORE_STORAGE_KEY) === 'true'
    if (activeRunId.value === null && !suppressRestore) {
      activeRunId.value = result.items[0]?.latestRunId ?? null
    }
  } catch {
    readLocalRecentTasks()
  }
}

function taskStatusLabel(status: string): string {
  if (status === 'RUNNING') return '运行中'
  if (status === 'WAITING_CONFIRMATION') return '待确认'
  if (status === 'WAITING_BUSINESS_RESULT') return '业务处理中'
  if (['SUCCESS', 'SUCCEEDED', 'COMPLETED'].includes(status)) return '已完成'
  if (['FAILED', 'CONFLICT'].includes(status)) return status === 'CONFLICT' ? '存在冲突' : '运行失败'
  return '可恢复任务'
}

async function newOrchestration(): Promise<void> {
  const alreadyOnChat = route.name === 'chat'
  window.sessionStorage.removeItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
  window.sessionStorage.removeItem(CHAT_ACTIVE_THREAD_STORAGE_KEY)
  window.sessionStorage.setItem(CHAT_SUPPRESS_RESTORE_STORAGE_KEY, 'true')
  closeMobileNavigation()
  if (alreadyOnChat) {
    window.dispatchEvent(new CustomEvent(NEW_CONVERSATION_EVENT))
    await router.replace({ name: 'chat' })
  } else {
    await router.push({ name: 'chat' })
  }
  void readRecentTasks()
}

function updateViewport(): void {
  mobileViewport.value = window.matchMedia('(max-width: 1024px)').matches
  if (!mobileViewport.value) {
    closeMobileNavigation(false)
  }
}

async function openMobileNavigation(): Promise<void> {
  mobileOpen.value = true
  document.body.classList.add('modal-open')
  await nextTick()
  sidebarRef.value?.querySelector<HTMLElement>('button, a[href], input')?.focus()
}

function closeMobileNavigation(restoreFocus = true): void {
  if (!mobileOpen.value) return
  mobileOpen.value = false
  document.body.classList.remove('modal-open')
  if (restoreFocus) {
    void nextTick(() => mobileToggleRef.value?.focus())
  }
}

function handleNavigationKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && mobileOpen.value) {
    event.preventDefault()
    closeMobileNavigation()
    return
  }
  if (event.key !== 'Tab' || !mobileOpen.value) return
  const focusable = [...(sidebarRef.value?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled])') ?? [])]
    .filter((item) => item.offsetParent !== null)
  if (focusable.length === 0) {
    event.preventDefault()
    return
  }
  const first = focusable[0]
  const last = focusable.at(-1)!
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

async function logout(): Promise<void> {
  authStore.clearSession()
  notificationStore.setUnreadCount(0)
  await router.replace({ name: 'login' })
}

watch(collapsed, (value) => {
  window.localStorage.setItem('weme.sidebar', value ? 'collapsed' : 'expanded')
})
watch(() => route.fullPath, () => void readRecentTasks())

onMounted(() => {
  void readRecentTasks()
  window.addEventListener(CHAT_CONTEXT_EVENT, readRecentTasks)
  window.addEventListener('storage', readRecentTasks)
  window.addEventListener('resize', updateViewport)
  document.addEventListener('keydown', handleNavigationKeydown)
  void notificationStore.refresh().catch(() => undefined)
})

onUnmounted(() => {
  window.removeEventListener(CHAT_CONTEXT_EVENT, readRecentTasks)
  window.removeEventListener('storage', readRecentTasks)
  window.removeEventListener('resize', updateViewport)
  document.removeEventListener('keydown', handleNavigationKeydown)
  if (mobileOpen.value) document.body.classList.remove('modal-open')
})
</script>
