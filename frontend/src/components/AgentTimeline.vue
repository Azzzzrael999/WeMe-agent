<template>
  <section class="user-progress" aria-labelledby="timeline-title">
    <div class="section-heading compact-heading">
      <div>
        <p class="eyebrow">业务进度</p>
        <h2 id="timeline-title">本次任务进展</h2>
        <p class="muted">普通视图按六个阶段归纳真实节点；技术细节在下方 Activity 中查看。</p>
      </div>
    </div>

    <ol class="progress-stage-list">
      <li v-for="stage in stages" :key="stage.label" :class="`progress-stage--${stage.state.toLowerCase()}`">
        <span aria-hidden="true"><CircleCheck v-if="stage.state === 'DONE'" :size="18" /><CircleDot v-else-if="stage.state === 'ACTIVE'" :size="18" /><Circle v-else :size="18" /></span>
        <div><strong>{{ stage.label }}</strong><small>{{ stage.summary }}</small></div>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { Circle, CircleCheck, CircleDot } from '@lucide/vue'
import { computed } from 'vue'

import type { AgentStepEvent, AgentToolEvent, AgentTraceStep, AgentTraceToolCall } from '../api/types'

type TimelineStep = AgentStepEvent | AgentTraceStep
type TimelineTool = AgentToolEvent | AgentTraceToolCall

const props = defineProps<{
  steps: readonly TimelineStep[]
  tools: readonly TimelineTool[]
  runStatus?: string
}>()

const sortedSteps = computed(() => [...props.steps].sort((left, right) => left.sequenceNo - right.sequenceNo))
const stages = computed(() => {
  const text = sortedSteps.value.map((step) => `${step.agentName} ${step.nodeName} ${step.summary}`.toLowerCase())
  const tools = props.tools.map((tool) => tool.toolName.toLowerCase())
  const definitions = [
    { label: '理解会议需求', matched: text.some((value) => /supervisor|requirement/.test(value)) },
    { label: '查询参会者时间', matched: tools.some((value) => /employee|free.busy/.test(value)) },
    { label: '检索会议制度', matched: text.some((value) => /policy/.test(value)) },
    { label: '求解候选方案', matched: text.some((value) => /schedul|solver|candidate/.test(value)) },
    { label: '等待用户确认', matched: props.runStatus === 'WAITING_CONFIRMATION' || text.some((value) => /hitl|confirm/.test(value)) },
    { label: '执行业务写入', matched: ['WAITING_BUSINESS_RESULT', 'SUCCEEDED', 'SUCCESS'].includes(props.runStatus ?? '') },
  ]
  const activeIndex = definitions.findIndex((definition) => !definition.matched)
  return definitions.map((definition, index) => ({
    label: definition.label,
    state: definition.matched ? 'DONE' : index === activeIndex && props.runStatus !== 'FAILED' ? 'ACTIVE' : 'PENDING',
    summary: definition.matched ? '已有真实运行活动' : index === activeIndex ? '当前或下一阶段' : '尚未到达',
  }))
})
</script>
