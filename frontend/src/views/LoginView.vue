<template>
  <main class="auth-page">
    <div class="auth-grid" aria-hidden="true"></div>
    <section class="auth-card" aria-labelledby="login-title">
      <div class="auth-brand"><div class="brand-mark">M</div><div><strong>MeetOps</strong><span>企业协作编排助手</span></div></div>
      <div class="auth-heading"><h1 id="login-title">欢迎回来</h1><p>使用企业账号进入协作工作台</p></div>

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

        <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>

        <button class="primary-button" type="submit" :disabled="submitting">
          {{ submitting ? '正在验证…' : '登录 MeetOps' }}
        </button>
      </form>
      <p class="auth-security">安全会话仅保存在当前浏览器标签页</p>
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
  return '/chat'
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
