<template>
  <section class="activity-timeline" aria-labelledby="activity-title">
    <header>
      <div>
        <h2 id="activity-title">技术 Activity</h2>
        <p>按时间查看 Agent、Tool 与受控 Loop 的脱敏摘要；点击活动可查看节点详情。</p>
      </div>
    </header>
    <TraceTimeline :steps="steps" :tools="tools" :loops="loops" :run="run" />
  </section>
</template>

<script setup lang="ts">
import type {
  AgentLoopEvent,
  AgentRunSummary,
  AgentStepEvent,
  AgentToolEvent,
  AgentTraceStep,
  AgentTraceToolCall,
} from '@/api/types'
import TraceTimeline from './TraceTimeline.vue'

defineProps<{
  steps: readonly (AgentStepEvent | AgentTraceStep)[]
  tools: readonly (AgentToolEvent | AgentTraceToolCall)[]
  loops: readonly AgentLoopEvent[]
  run?: Partial<AgentRunSummary> | null
}>()
</script>
