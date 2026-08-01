<template>
  <AppShell title="待我确认" description="只展示当前浏览器已知并可恢复的 Agent Run；系统尚无跨 Run 待确认列表接口。" eyebrow="工作台 / 待我确认">
    <section class="content-panel"><EmptyState title="暂无可恢复的待确认项" description="从智能编排进入 HITL 后，可通过 Run 链接返回确认。这里不会伪造后端任务队列。" icon="✓"><RouterLink class="ui-button ui-button--default" :to="{ name: 'chat' }">前往智能编排</RouterLink></EmptyState></section>
  </AppShell>
</template>
<script setup lang="ts">
import { RouterLink } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
</script>
