<template>
  <div v-if="runId" class="run-status-bar" aria-live="polite">
    <div><StatusBadge :status="status || 'RUNNING'" /></div>
    <div>
      <button class="text-button" type="button" :disabled="loading" @click="$emit('refresh')">{{ loading ? '刷新中…' : '刷新状态' }}</button>
      <button class="text-button" type="button" @click="$emit('trace')">查看运行过程 <ArrowRight :size="14" aria-hidden="true" /></button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowRight } from '@lucide/vue'
import StatusBadge from './StatusBadge.vue'
defineProps<{ runId: string | null; status: string; loading: boolean }>()
defineEmits<{ refresh: []; trace: [] }>()
</script>
