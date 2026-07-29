<template>
  <main class="app-page">
    <header class="topbar app-topbar">
      <div>
        <p class="eyebrow">企业会议智能调度系统 · Day 6</p>
        <h1>{{ title }}</h1>
      </div>
      <div class="user-actions">
        <div v-if="authStore.state.user" class="user-summary">
          <strong>{{ authStore.state.user.displayName }}</strong>
          <span>{{ authStore.state.user.departmentName }} · {{ roleLabel }}</span>
        </div>
        <button class="secondary-button" type="button" @click="logout">退出登录</button>
      </div>
    </header>

    <nav class="app-nav" aria-label="主要功能">
      <RouterLink :to="{ name: 'chat' }">智能调度</RouterLink>
      <RouterLink :to="{ name: 'meetings' }">我的会议</RouterLink>
      <RouterLink :to="{ name: 'rooms' }">会议室</RouterLink>
    </nav>

    <slot />
  </main>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { authStore } from '../auth/store'

defineProps<{
  title: string
}>()

const router = useRouter()
const roleLabel = computed(() => authStore.state.user?.roles.join(' / ') ?? '')

async function logout(): Promise<void> {
  authStore.clearSession()
  await router.replace({ name: 'login' })
}
</script>
