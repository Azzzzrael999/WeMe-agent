<template>
  <div class="resource-timeline" :aria-label="label">
    <div v-if="slots.length" class="timeline-grid">
      <div v-for="slot in slots" :key="`${slot.startAt}-${slot.endAt}`" class="timeline-slot" :class="slot.available ? 'timeline-slot--free' : 'timeline-slot--busy'" :title="slot.available ? '可用' : '已占用'">
        <span>{{ time(slot.startAt) }}</span><strong>{{ slot.available ? '可用' : '占用' }}</strong>
      </div>
    </div>
    <EmptyState v-else title="暂无资源时间轴" description="选择会议室和时间窗口后，以 30 分钟 [start, end) 槽位展示。" icon="▤" />
  </div>
</template>
<script setup lang="ts">
import type { RoomAvailabilitySlot } from '@/api/types'; import EmptyState from './EmptyState.vue'
withDefaults(defineProps<{ slots: readonly RoomAvailabilitySlot[]; label?: string }>(), { label: '30 分钟资源时间轴' })
function time(value: string): string { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) }
</script>
