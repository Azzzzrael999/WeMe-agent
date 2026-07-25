import { computed, reactive } from 'vue'

import { apiRequest } from '../api/client'
import type { CurrentUser, LoginRequest, LoginResult } from '../api/types'
import { clearAccessToken, readAccessToken, writeAccessToken } from './token'

const state = reactive<{
  accessToken: string | null
  user: CurrentUser | null
}>({
  accessToken: readAccessToken(),
  user: null,
})

async function login(credentials: LoginRequest): Promise<void> {
  clearSession()

  const result = await apiRequest<LoginResult>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(credentials),
  })

  writeAccessToken(result.accessToken)
  state.accessToken = result.accessToken
  state.user = result.user
}

async function loadCurrentUser(): Promise<CurrentUser> {
  const user = await apiRequest<CurrentUser>('/auth/me')
  state.user = user
  state.accessToken = readAccessToken()
  return user
}

function clearSession(): void {
  clearAccessToken()
  state.accessToken = null
  state.user = null
}

export const authStore = {
  state,
  isAuthenticated: computed(() => state.accessToken !== null),
  login,
  loadCurrentUser,
  clearSession,
}
