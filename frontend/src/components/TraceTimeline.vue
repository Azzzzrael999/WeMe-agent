<template>
  <div class="trace-timeline">
    <nav class="trace-filters" aria-label="运行活动筛选">
      <button
        v-for="filter in filters"
        :key="filter.value"
        type="button"
        :class="{ active: activeFilter === filter.value }"
        :aria-pressed="activeFilter === filter.value"
        @click="activeFilter = filter.value"
      >
        {{ filter.label }}
        <span>{{ filterCount(filter.value) }}</span>
      </button>
    </nav>

    <EmptyState
      v-if="filteredActivities.length === 0"
      title="没有匹配的运行活动"
      description="只有服务端返回的脱敏 Agent、Tool 与 Loop 事件会显示在这里。"
      icon="search"
    />

    <ol v-else class="activity-feed">
      <li v-for="activity in filteredActivities" :key="activity.id">
        <button class="activity-item" type="button" @click="selected = activity">
          <span class="activity-item__icon" :class="`activity-item__icon--${activity.kind.toLowerCase()}`">
            <Bot v-if="activity.kind === 'AGENT'" :size="16" aria-hidden="true" />
            <Wrench v-else-if="activity.kind === 'TOOL'" :size="16" aria-hidden="true" />
            <Repeat2 v-else :size="16" aria-hidden="true" />
          </span>
          <span class="activity-item__body">
            <span class="activity-item__heading">
              <strong>{{ activity.title }}</strong>
              <StatusBadge :status="activity.status" />
            </span>
            <span>{{ activity.summary }}</span>
            <small>
              {{ activity.category }}
              <template v-if="activity.createdAt"> · {{ formatDateTime(activity.createdAt) }}</template>
              <template v-if="activity.durationMs !== null"> · {{ formatDuration(activity.durationMs) }}</template>
            </small>
          </span>
          <ChevronRight :size="17" aria-hidden="true" />
        </button>
      </li>
    </ol>

    <TraceDetailSheet v-model:open="detailOpen" :activity="selected" />
  </div>
</template>

<script setup lang="ts">
import { Bot, ChevronRight, Repeat2, Wrench } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import type {
  AgentLoopEvent,
  AgentRunSummary,
  AgentStepEvent,
  AgentToolEvent,
  AgentTraceStep,
  AgentTraceToolCall,
} from '@/api/types'
import { formatDateTime, formatDuration } from '@/utils/format'
import EmptyState from './EmptyState.vue'
import StatusBadge from './StatusBadge.vue'
import TraceDetailSheet, { type TraceActivity } from './TraceDetailSheet.vue'

type Step = AgentStepEvent | AgentTraceStep
type Tool = AgentToolEvent | AgentTraceToolCall
type FilterValue = 'ALL' | 'AGENT' | 'TOOL' | 'LOOP' | 'ERROR'

const props = defineProps<{
  steps: readonly Step[]
  tools: readonly Tool[]
  loops?: readonly AgentLoopEvent[]
  run?: Partial<AgentRunSummary> | null
}>()
const filters: { value: FilterValue; label: string }[] = [
  { value: 'ALL', label: '全部' },
  { value: 'AGENT', label: 'Agent' },
  { value: 'TOOL', label: 'Tool' },
  { value: 'LOOP', label: 'Loop' },
  { value: 'ERROR', label: '错误' },
]
const activeFilter = ref<FilterValue>('ALL')
const selected = ref<TraceActivity | null>(null)
const detailOpen = ref(false)

const activities = computed<TraceActivity[]>(() => {
  const result: TraceActivity[] = []
  props.steps.forEach((step) => result.push({
    id: `agent:${step.stepId}`,
    kind: 'AGENT',
    title: agentLabel(step.agentName),
    category: step.nodeName,
    status: step.status,
    summary: step.summary,
    createdAt: 'createdAt' in step ? step.createdAt : null,
    durationMs: step.durationMs,
    errorCode: 'errorCode' in step ? step.errorCode : null,
    riskLevel: null,
    inputSummary: null,
    outputSummary: step.summary,
    idempotencySummary: null,
    sanitizedArgs: null,
  }))
  props.tools.forEach((tool) => result.push({
    id: `tool:${tool.toolCallId}`,
    kind: 'TOOL',
    title: tool.toolName,
    category: tool.riskLevel,
    status: tool.status,
    summary: toolSummary(tool),
    createdAt: 'createdAt' in tool ? tool.createdAt : null,
    durationMs: tool.durationMs,
    errorCode: tool.status === 'FAILED' ? 'TOOL_CALL_FAILED' : null,
    riskLevel: tool.riskLevel,
    inputSummary: toolArgs(tool) === null ? null : '仅展示服务端脱敏后的参数摘要。',
    outputSummary: toolSummary(tool),
    idempotencySummary: null,
    sanitizedArgs: toolArgs(tool),
  }))
  ;(props.loops ?? []).forEach((loop, index) => result.push({
    id: `loop:${loop.runId}:${loop.iteration}:${loop.phase}:${loop.createdAt ?? index}`,
    kind: 'LOOP',
    title: `${loop.phase} · 第 ${loop.iteration} 轮`,
    category: loop.replanCount > 0 ? `重规划 ${loop.replanCount}` : '受控循环',
    status: loopStatus(loop),
    summary: loop.decision || loop.stopReason || '执行受控循环阶段。',
    createdAt: loop.createdAt ?? null,
    durationMs: null,
    errorCode: loop.feedbackCodes.length > 0 ? loop.feedbackCodes.join('、') : null,
    riskLevel: null,
    inputSummary: null,
    outputSummary: loop.stopReason ?? loop.decision ?? null,
    idempotencySummary: null,
    sanitizedArgs: null,
  }))
  return result.sort((left, right) => {
    const leftTime = left.createdAt === null ? Number.MAX_SAFE_INTEGER : Date.parse(left.createdAt)
    const rightTime = right.createdAt === null ? Number.MAX_SAFE_INTEGER : Date.parse(right.createdAt)
    return leftTime - rightTime
  })
})
const filteredActivities = computed(() => activeFilter.value === 'ALL'
  ? activities.value
  : activeFilter.value === 'ERROR'
    ? activities.value.filter((activity) => activity.errorCode !== null || activity.status === 'FAILED')
    : activities.value.filter((activity) => activity.kind === activeFilter.value))

watch(selected, (value) => { detailOpen.value = value !== null })
watch(detailOpen, (value) => { if (!value) selected.value = null })

function filterCount(filter: FilterValue): number {
  if (filter === 'ALL') return activities.value.length
  if (filter === 'ERROR') return activities.value.filter((item) => item.errorCode !== null || item.status === 'FAILED').length
  return activities.value.filter((item) => item.kind === filter).length
}

function agentLabel(value: string): string {
  return ({
    supervisor: 'Supervisor', requirement: 'Requirement Agent', policy: 'Policy Agent',
    scheduling: 'Scheduling Agent', deterministic: '确定性节点',
  } as Record<string, string>)[value] ?? value
}
function toolArgs(tool: Tool): Record<string, unknown> | null {
  return 'sanitizedArgs' in tool ? tool.sanitizedArgs : null
}
function toolSummary(tool: Tool): string {
  return 'resultSummary' in tool ? tool.resultSummary : tool.summary
}
function loopStatus(loop: AgentLoopEvent): string {
  if (loop.stopReason === 'COMPLETED' || loop.stopReason === 'READY_FOR_CONFIRMATION') return 'SUCCEEDED'
  if (loop.stopReason !== null && loop.stopReason !== undefined && loop.stopReason !== 'WAITING_BUSINESS_RESULT') return 'FAILED'
  return 'RUNNING'
}
</script>
