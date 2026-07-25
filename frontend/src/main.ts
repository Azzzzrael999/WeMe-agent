import { createApp } from 'vue'

import App from './App.vue'
import { setUnauthorizedHandler } from './api/client'
import { authStore } from './auth/store'
import { router } from './router'
import './styles.css'

setUnauthorizedHandler(() => {
  authStore.clearSession()

  if (router.currentRoute.value.name !== 'login') {
    void router.replace({
      name: 'login',
      query: { redirect: router.currentRoute.value.fullPath },
    })
  }
})

createApp(App).use(router).mount('#app')
