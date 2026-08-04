<template>
  <div class="trace-timeline">
    <AgentLoopTimeline :events="loops ?? []" :run="run" />
    <EmptyState v-if="steps.length === 0 && tools.length === 0" title="暂无运行步骤" description="处理开始后，这里仅展示脱敏的 Agent 与 Tool 摘要。" icon="⋮" />
    <ol v-else>
      <li v-for="step in sorted" :key="step.stepId" class="trace-step">
        <span class="trace-node" />
        <div class="trace-step__body"><header><div><strong>{{ agentLabel(step.agentName) }}</strong><span>{{ step.nodeName }}</span></div><StatusBadge :status="step.status" /></header><p>{{ step.summary }}</p><small>{{ formatDuration(step.durationMs) }}</small></div>
      </li>
    </ol>
    <div v-if="tools.length" class="trace-tools"><h3>工具调用</h3><details v-for="tool in tools" :key="tool.toolCallId"><summary><span><strong>{{ tool.toolName }}</strong><em>{{ tool.riskLevel }}</em></span><span>{{ formatDuration(tool.durationMs) }}⌄</span></summary><div class="tool-detail"><p>{{ summary(tool) }}</p><pre v-if="args(tool)">{{ formatSanitizedArgs(args(tool)!) }}</pre></div></details></div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { AgentLoopEvent, AgentRunSummary, AgentStepEvent, AgentToolEvent, AgentTraceStep, AgentTraceToolCall } from '@/api/types'
import { formatDuration, formatSanitizedArgs } from '@/utils/format'
import AgentLoopTimeline from './AgentLoopTimeline.vue'
import EmptyState from './EmptyState.vue'
import StatusBadge from './StatusBadge.vue'
type Step = AgentStepEvent | AgentTraceStep
type Tool = AgentToolEvent | AgentTraceToolCall
const props = defineProps<{ steps: readonly Step[]; tools: readonly Tool[]; loops?: readonly AgentLoopEvent[]; run?: Partial<AgentRunSummary> | null }>()
const sorted = computed(() => [...props.steps].sort((a, b) => a.sequenceNo - b.sequenceNo))
function agentLabel(value: string): string { return ({ supervisor: 'Supervisor', requirement: 'Requirement Agent', policy: 'Policy Agent', scheduling: 'Scheduling Agent', deterministic: '确定性节点' } as Record<string, string>)[value] ?? value }
function args(tool: Tool): Record<string, unknown> | null { return 'sanitizedArgs' in tool ? tool.sanitizedArgs : null }
function summary(tool: Tool): string { return 'resultSummary' in tool ? tool.resultSummary : tool.summary }
</script>
