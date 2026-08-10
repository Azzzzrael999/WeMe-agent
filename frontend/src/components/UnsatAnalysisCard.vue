<template>
  <section class="unsat-card" aria-label="未找到可行方案的具体原因">
    <header>
      <span><TriangleAlert :size="16" aria-hidden="true" /></span>
      <div>
        <strong>未找到满足全部硬约束的方案</strong>
        <small>{{ categoryLabel }}</small>
      </div>
    </header>
    <dl class="unsat-card__request">
      <div>
        <dt>请求时间</dt>
        <dd>{{ formatRange(analysis.requestedWindow.start, analysis.requestedWindow.end) }}</dd>
      </div>
      <div>
        <dt>连续时长</dt>
        <dd>{{ analysis.durationMinutes }} 分钟</dd>
      </div>
    </dl>
    <div v-if="analysis.blockingIntervals.length" class="unsat-card__blockers">
      <strong>具体冲突</strong>
      <ul>
        <li v-for="(blocker, index) in analysis.blockingIntervals" :key="`${blocker.resourceType}-${blocker.resourceId}-${blocker.meetingId}-${index}`">
          <UserRound v-if="blocker.resourceType === 'EMPLOYEE'" :size="14" aria-hidden="true" />
          <Clock3 v-else :size="14" aria-hidden="true" />
          <span>
            <b>{{ blocker.resourceName ?? resourceFallback(blocker.resourceType, blocker.resourceId) }}</b>
            {{ formatRange(blocker.startAt, blocker.endAt) }}：{{ blocker.reason }}
          </span>
        </li>
      </ul>
    </div>
    <div v-if="analysis.relaxationSuggestions.length" class="unsat-card__suggestions">
      <strong>可选调整</strong>
      <ul>
        <li v-for="suggestion in analysis.relaxationSuggestions" :key="suggestion">{{ suggestion }}</li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Clock3, TriangleAlert, UserRound } from '@lucide/vue'
import { computed } from 'vue'

import type { AgentUnsatAnalysis } from '@/api/types'

const props = defineProps<{ analysis: AgentUnsatAnalysis }>()

const categoryLabel = computed(() => ({
  REQUIRED_AVAILABILITY: '必需参会者时间冲突',
  FACILITY_CAPACITY: '会议室容量或设备不满足',
  TIME_WINDOW_DURATION: '时间窗口无法容纳会议',
  POLICY: '会议制度硬约束冲突',
}[props.analysis.category] ?? props.analysis.category))

function formatRange(start: string, end: string): string {
  const startAt = new Date(start)
  const endAt = new Date(end)
  if (Number.isNaN(startAt.getTime()) || Number.isNaN(endAt.getTime())) {
    return `${start} — ${end}`
  }
  const date = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
  }).format(startAt)
  const time = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  return `${date} ${time.format(startAt)}–${time.format(endAt)}`
}

function resourceFallback(type: string, id: number | null): string {
  return `${type === 'ROOM' ? '会议室' : '资源'}${id === null ? '' : ` ${id}`}`
}
</script>
