<template><span class="status-badge" :class="`status-badge--${tone}`"><span class="status-dot" />{{ label }}</span></template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string; label?: string }>()
const labels: Record<string, string> = {
  SUCCESS: '成功', CONFIRMED: '已确认', COMPLETED: '已完成', SUCCEEDED: '已完成',
  RUNNING: '运行中', PENDING: '等待中', PROCESSING: '处理中', WAITING_CONFIRMATION: '待确认',
  WAITING_BUSINESS_RESULT: '业务处理中', WAITING_USER_INPUT: '待补充', FAILED: '失败',
  CONFLICT: '冲突', CANCELLED: '已取消', ACTIVE: '启用', INACTIVE: '停用',
}
const label = computed(() => props.label ?? labels[props.status] ?? props.status)
const tone = computed(() => {
  if (['SUCCESS', 'CONFIRMED', 'COMPLETED', 'SUCCEEDED', 'ACTIVE'].includes(props.status)) return 'success'
  if (props.status === 'RUNNING') return 'info'
  if (props.status.startsWith('WAITING') || ['PENDING', 'PROCESSING'].includes(props.status)) return 'warning'
  if (['FAILED', 'CONFLICT', 'CANCELLED', 'INACTIVE'].includes(props.status)) return 'destructive'
  return 'secondary'
})
</script>
