<template>
  <section class="agent-loop" aria-labelledby="agent-loop-title">
    <div class="agent-loop__heading">
      <div>
        <h3 id="agent-loop-title">受控执行循环</h3>
        <p>只展示结构化决策与预算，不包含模型隐藏推理。</p>
      </div>
      <StatusBadge v-if="latest?.stopReason" :status="stopStatus(latest.stopReason)" :label="latest.stopReason" />
    </div>

    <dl v-if="hasMetrics" class="loop-metrics">
      <div><dt>模型</dt><dd>{{ modelName }}</dd></div>
      <div><dt>模型调用</dt><dd>{{ modelCalls }}</dd></div>
      <div><dt>Tool 调用</dt><dd>{{ toolCalls }}</dd></div>
      <div><dt>Token</dt><dd>{{ totalTokens }}</dd></div>
      <div><dt>输入 / 输出</dt><dd>{{ inputTokens }} / {{ outputTokens }}</dd></div>
      <div><dt>耗时</dt><dd>{{ formatDuration(run?.durationMs ?? null) }}</dd></div>
    </dl>

    <EmptyState v-if="events.length === 0" title="暂无 Loop 事件" description="PLAN、ACT、OBSERVE、VERIFY 或 REPLAN 发生后会显示在这里。" icon="search" />
    <ol v-else class="loop-event-list">
      <li v-for="(event, index) in events" :key="eventKey(event, index)" class="loop-event">
        <span class="loop-phase" :class="`loop-phase--${event.phase.toLowerCase()}`">{{ event.phase }}</span>
        <div class="loop-event__body">
          <header>
            <strong>第 {{ event.iteration }} 轮<template v-if="event.replanCount"> · 重规划 {{ event.replanCount }}</template></strong>
            <time v-if="event.createdAt">{{ formatDateTime(event.createdAt) }}</time>
          </header>
          <p v-if="event.decision">{{ event.decision }}</p>
          <div v-if="event.feedbackCodes.length" class="loop-feedback">
            <span v-for="code in event.feedbackCodes" :key="code">{{ code }}</span>
          </div>
          <dl v-if="event.remainingBudget" class="loop-budget">
            <div v-if="event.remainingBudget.modelCalls !== undefined"><dt>剩余模型</dt><dd>{{ event.remainingBudget.modelCalls }}</dd></div>
            <div v-if="event.remainingBudget.toolCalls !== undefined"><dt>剩余 Tool</dt><dd>{{ event.remainingBudget.toolCalls }}</dd></div>
            <div v-if="event.remainingBudget.graphNodes !== undefined"><dt>剩余节点</dt><dd>{{ event.remainingBudget.graphNodes }}</dd></div>
            <div v-if="event.remainingBudget.replans !== undefined"><dt>剩余重规划</dt><dd>{{ event.remainingBudget.replans }}</dd></div>
          </dl>
          <p v-if="event.stopReason" class="loop-stop">停止原因：{{ event.stopReason }}</p>
        </div>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { AgentLoopEvent, AgentRunSummary } from '@/api/types'
import { formatDateTime, formatDuration } from '@/utils/format'
import EmptyState from './EmptyState.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  events: readonly AgentLoopEvent[]
  run?: Partial<AgentRunSummary> | null
}>()

const latest = computed(() => props.events.at(-1))
const modelName = computed(() => props.run?.model ?? props.run?.configuredModel ?? latest.value?.model ?? '—')
const modelCalls = computed(() => props.run?.modelCallCount ?? latest.value?.modelCallCount ?? '—')
const toolCalls = computed(() => props.run?.toolCallCount ?? latest.value?.toolCallCount ?? '—')
const inputTokens = computed(() => props.run?.inputTokens ?? latest.value?.tokenUsage?.inputTokens ?? 0)
const outputTokens = computed(() => props.run?.outputTokens ?? latest.value?.tokenUsage?.outputTokens ?? 0)
const totalTokens = computed(
  () => props.run?.totalTokens ?? latest.value?.tokenUsage?.totalTokens ?? inputTokens.value + outputTokens.value,
)
const hasMetrics = computed(
  () => props.run !== null && props.run !== undefined || props.events.some((event) => event.model !== null || event.tokenUsage !== undefined),
)

function eventKey(event: AgentLoopEvent, index: number): string {
  return `${event.runId}:${event.replanCount}:${event.iteration}:${event.phase}:${event.createdAt ?? index}`
}

function stopStatus(stopReason: string): string {
  if (stopReason === 'READY_FOR_CONFIRMATION' || stopReason === 'COMPLETED') {
    return 'SUCCESS'
  }
  if (stopReason === 'NEED_CLARIFICATION' || stopReason === 'WAITING_BUSINESS_RESULT') {
    return 'PENDING'
  }
  return 'FAILED'
}
</script>
