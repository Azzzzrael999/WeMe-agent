<template>
  <main class="auth-page">
    <section class="auth-card" aria-labelledby="login-title">
      <p class="eyebrow">企业会议智能调度系统</p>
      <h1 id="login-title">登录</h1>
      <p class="muted">使用企业账号进入会议室查询。</p>

      <form class="stack" @submit.prevent="submitLogin">
        <label>
          <span>用户名</span>
          <input
            v-model.trim="form.username"
            name="username"
            autocomplete="username"
            required
            :disabled="submitting"
          />
        </label>

        <label>
          <span>密码</span>
          <input
            v-model="form.password"
            name="password"
            type="password"
            autocomplete="current-password"
            required
            :disabled="submitting"
          />
        </label>

        <p class="demo-hint">演示账号：<code>zhangsan</code> / <code>demo-password</code></p>
        <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>

        <button class="primary-button" type="submit" :disabled="submitting">
          {{ submitting ? '登录中…' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import { authStore } from '../auth/store'

const route = useRoute()
const router = useRouter()

const form = reactive({
  username: 'zhangsan',
  password: 'demo-password',
})
const submitting = ref(false)
const errorMessage = ref('')

function safeRedirect(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
    return redirect
  }
  return '/rooms'
}

async function submitLogin(): Promise<void> {
  if (submitting.value) {
    return
  }

  submitting.value = true
  errorMessage.value = ''

  try {
    await authStore.login({ username: form.username, password: form.password })
    await router.replace(safeRedirect())
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : '登录失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>
