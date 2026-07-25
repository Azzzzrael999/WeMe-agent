<template>
  <main class="app-page">
    <header class="topbar">
      <div>
        <p class="eyebrow">Day 1</p>
        <h1>会议室</h1>
      </div>
      <div class="user-actions">
        <div v-if="authStore.state.user" class="user-summary">
          <strong>{{ authStore.state.user.displayName }}</strong>
          <span>{{ authStore.state.user.departmentName }}</span>
        </div>
        <button class="secondary-button" type="button" @click="logout">退出登录</button>
      </div>
    </header>

    <section class="content-panel" aria-labelledby="room-list-title">
      <div class="section-heading">
        <div>
          <h2 id="room-list-title">可查询会议室</h2>
          <p class="muted">显示位置、容量和已配置设备。</p>
        </div>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadRooms">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>

      <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
      <p v-else-if="loading" class="status-message" aria-live="polite">正在加载会议室…</p>
      <div v-else-if="rooms.length === 0" class="empty-state">暂无可显示的会议室。</div>

      <div v-else class="room-grid">
        <article v-for="room in rooms" :key="room.id" class="room-card">
          <div class="room-card-heading">
            <div>
              <p class="room-code">{{ room.code }}</p>
              <h3>{{ room.name }}</h3>
            </div>
            <div class="badges">
              <span v-if="room.isHot" class="badge badge-hot">热门</span>
              <span class="badge">{{ room.status }}</span>
            </div>
          </div>

          <dl class="room-facts">
            <div>
              <dt>位置</dt>
              <dd>{{ room.building }} · {{ room.floor }}</dd>
            </div>
            <div>
              <dt>容量</dt>
              <dd>{{ room.capacity }} 人</dd>
            </div>
            <div>
              <dt>类型</dt>
              <dd>{{ room.roomType }}</dd>
            </div>
          </dl>

          <div class="feature-list" aria-label="会议室设备">
            <span v-for="feature in room.features" :key="feature.code" class="feature-chip">
              {{ feature.name }}
            </span>
            <span v-if="room.features.length === 0" class="muted">暂无设备标签</span>
          </div>
        </article>
      </div>

      <p v-if="!loading && !errorMessage" class="result-count">共 {{ total }} 间会议室</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, apiRequest } from '../api/client'
import type { MeetingRoom, RoomListResult } from '../api/types'
import { authStore } from '../auth/store'

const router = useRouter()
const rooms = ref<MeetingRoom[]>([])
const total = ref(0)
const loading = ref(true)
const errorMessage = ref('')

async function loadRooms(): Promise<void> {
  loading.value = true
  errorMessage.value = ''

  try {
    const result = await apiRequest<RoomListResult>('/rooms')
    rooms.value = result.items
    total.value = result.total
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : '会议室加载失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function logout(): Promise<void> {
  authStore.clearSession()
  await router.replace({ name: 'login' })
}

onMounted(() => {
  void loadRooms()
})
</script>
