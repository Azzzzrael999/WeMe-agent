<template>
  <section class="content-panel timeline-panel" aria-labelledby="timeline-title">
    <div class="section-heading compact-heading">
      <div>
        <h2 id="timeline-title">Agent 与工具步骤</h2>
        <p class="muted">仅展示结构化摘要，不展示模型隐藏推理或内部凭据。</p>
      </div>
    </div>

    <div v-if="steps.length === 0 && tools.length === 0" class="empty-state compact-empty">
      提交需求后，这里会显示当前 Agent 和 Java Tool 的处理步骤。
    </div>

    <ol v-else class="timeline-list">
      <li v-for="step in sortedSteps" :key="step.stepId" class="timeline-entry">
        <span class="timeline-marker" aria-hidden="true"></span>
        <div>
          <div class="timeline-entry__title">
            <strong>{{ displayAgent(step.agentName) }}</strong>
            <span class="badge" :class="statusClass(step.status)">{{ step.status }}</span>
            <span class="muted">{{ formatDuration(step.durationMs) }}</span>
          </div>
          <p>{{ step.summary }}</p>
          <small>{{ step.nodeName }}</small>
        </div>
      </li>
    </ol>

    <div v-if="tools.length > 0" class="tool-summary-list">
      <h3>Java 工具调用</h3>
      <article v-for="tool in tools" :key="tool.toolCallId" class="tool-summary-card">
        <div>
          <strong>{{ tool.toolName }}</strong>
          <span class="badge" :class="statusClass(tool.status)">{{ tool.status }}</span>
          <span class="muted">{{ tool.riskLevel }} · {{ formatDuration(tool.durationMs) }}</span>
        </div>
        <p>{{ toolSummary(tool) }}</p>
        <pre v-if="toolArgs(tool)" class="sanitized-args">{{ formatSanitizedArgs(toolArgs(tool)!) }}</pre>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { AgentStepEvent, AgentToolEvent, AgentTraceStep, AgentTraceToolCall } from '../api/types'
import { formatDuration, formatSanitizedArgs } from '../utils/format'

type TimelineStep = AgentStepEvent | AgentTraceStep
type TimelineTool = AgentToolEvent | AgentTraceToolCall

const props = defineProps<{
  steps: readonly TimelineStep[]
  tools: readonly TimelineTool[]
}>()

const sortedSteps = computed(() => [...props.steps].sort((left, right) => left.sequenceNo - right.sequenceNo))

function displayAgent(name: string): string {
  const labels: Record<string, string> = {
    supervisor: 'Supervisor',
    requirement: 'Requirement Agent',
    policy: 'Policy Agent',
    scheduling: 'Scheduling Agent',
    deterministic: '确定性处理器',
  }
  return labels[name] ?? name
}

function statusClass(status: string): string {
  if (status === 'FAILED') {
    return 'badge-danger'
  }
  if (status === 'WAITING_CONFIRMATION' || status === 'WAITING_BUSINESS_RESULT') {
    return 'badge-warning'
  }
  return 'badge-success'
}

function toolArgs(tool: TimelineTool): Record<string, unknown> | null {
  return 'sanitizedArgs' in tool ? tool.sanitizedArgs : null
}

function toolSummary(tool: TimelineTool): string {
  return 'resultSummary' in tool ? tool.resultSummary : tool.summary
}
</script>
