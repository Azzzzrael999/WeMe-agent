<template>
  <div class="workspace-shell" :class="{ 'workspace-shell--collapsed': collapsed }">
    <aside class="workspace-sidebar" :class="{ 'workspace-sidebar--mobile-open': mobileOpen }">
      <div class="workspace-brand">
        <div class="brand-mark" aria-hidden="true">M</div>
        <div class="workspace-brand__copy"><strong>MeetOps</strong><span>协作编排助手</span></div>
        <button class="icon-button mobile-close" type="button" aria-label="关闭导航" @click="mobileOpen = false">×</button>
      </div>
      <nav class="workspace-nav" aria-label="主要功能">
        <section v-for="group in groups" :key="group.label">
          <p class="nav-group-label">{{ group.label }}</p>
          <RouterLink v-for="item in group.items" :key="item.to.name" :to="item.to" :title="item.label" @click="mobileOpen = false">
            <span class="nav-icon" aria-hidden="true">{{ item.icon }}</span><span class="nav-label">{{ item.label }}</span>
          </RouterLink>
        </section>
      </nav>
      <div class="workspace-user">
        <div class="user-avatar">{{ initials }}</div>
        <div class="workspace-user__copy"><strong>{{ authStore.state.user?.displayName }}</strong><span>{{ authStore.state.user?.departmentName }} · {{ roleLabel }}</span></div>
        <button class="icon-button" type="button" aria-label="退出登录" title="退出登录" @click="logout">↪</button>
      </div>
    </aside>
    <button v-if="mobileOpen" class="mobile-backdrop" type="button" aria-label="关闭导航" @click="mobileOpen = false" />
    <div class="workspace-main">
      <header class="workspace-topbar">
        <button class="icon-button desktop-toggle" type="button" :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'" @click="collapsed = !collapsed">☰</button>
        <button class="icon-button mobile-toggle" type="button" aria-label="打开导航" @click="mobileOpen = true">☰</button>
        <div class="topbar-title"><span>MeetOps Workspace</span><small>Asia/Shanghai</small></div>
        <div class="topbar-actions"><span class="live-indicator"><span />业务服务已连接</span></div>
      </header>
      <main class="workspace-content"><slot /></main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { authStore } from '@/auth/store'

const router = useRouter()
const collapsed = ref(window.localStorage.getItem('meetops.sidebar') === 'collapsed')
const mobileOpen = ref(false)
const groups = [
  { label: '工作台', items: [{ label: '智能编排', icon: '✦', to: { name: 'chat' } }, { label: '待我确认', icon: '✓', to: { name: 'approvals' } }] },
  { label: '协作', items: [{ label: '我的会议', icon: '▦', to: { name: 'meetings' } }, { label: '会议室资源', icon: '⌂', to: { name: 'rooms' } }] },
  { label: '产品预览', items: [{ label: '异常重排', icon: '↻', to: { name: 'preview-replan' } }, { label: '会前会后', icon: '◫', to: { name: 'preview-lifecycle' } }] },
]
const roleLabel = computed(() => authStore.state.user?.roles.includes('ADMIN') ? '管理员' : '员工')
const initials = computed(() => authStore.state.user?.displayName.slice(0, 1) ?? 'M')

async function logout(): Promise<void> {
  authStore.clearSession()
  await router.replace({ name: 'login' })
}
</script>
