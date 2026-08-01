<template>
  <div v-if="runId" class="run-status-bar" aria-live="polite">
    <div><StatusBadge :status="status || 'RUNNING'" /><span class="run-id">{{ runId }}</span></div>
    <div>
      <button class="text-button" type="button" :disabled="loading" @click="$emit('refresh')">{{ loading ? '刷新中…' : '刷新状态' }}</button>
      <button class="text-button" type="button" @click="$emit('trace')">查看运行过程 →</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import StatusBadge from './StatusBadge.vue'
defineProps<{ runId: string | null; status: string; loading: boolean }>()
defineEmits<{ refresh: []; trace: [] }>()
</script>
