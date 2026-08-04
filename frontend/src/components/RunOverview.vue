<template>
  <section class="run-overview" aria-labelledby="run-overview-title">
    <header class="run-overview__header">
      <div>
        <p class="eyebrow">运行摘要</p>
        <h2 id="run-overview-title">{{ run.intent || 'Agent 任务' }}</h2>
        <p>{{ run.questionSummary }}</p>
      </div>
      <StatusBadge :status="run.status" />
    </header>

    <dl class="run-overview__metrics">
      <div v-if="run.durationMs !== null"><dt>总耗时</dt><dd>{{ formatDuration(run.durationMs) }}</dd></div>
      <div v-if="run.modelCallCount > 0"><dt>模型调用</dt><dd>{{ run.modelCallCount }}</dd></div>
      <div v-if="run.toolCallCount > 0"><dt>Tool 调用</dt><dd>{{ run.toolCallCount }}</dd></div>
      <div v-if="provider"><dt>Provider</dt><dd>{{ provider }}</dd></div>
      <div v-if="model"><dt>模型</dt><dd>{{ model }}</dd></div>
      <div v-if="run.promptVersion"><dt>Prompt 版本</dt><dd>{{ run.promptVersion }}</dd></div>
      <div v-if="run.schemaVersion"><dt>Schema 版本</dt><dd>{{ run.schemaVersion }}</dd></div>
      <div v-if="totalTokens !== null"><dt>Token</dt><dd>{{ totalTokens }}</dd></div>
    </dl>

    <div v-if="run.answerSummary || run.errorCode" class="run-overview__result">
      <div v-if="run.answerSummary"><span>结果摘要</span><p>{{ run.answerSummary }}</p></div>
      <div v-if="run.errorCode"><span>错误码</span><p>{{ run.errorCode }}</p></div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { AgentRunSummary } from '@/api/types'
import { formatDuration } from '@/utils/format'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ run: AgentRunSummary }>()
const provider = computed(() => props.run.modelProvider || null)
const model = computed(() => props.run.model || props.run.configuredModel || null)
const totalTokens = computed(() => {
  if (typeof props.run.totalTokens === 'number') return props.run.totalTokens
  if (typeof props.run.inputTokens === 'number' || typeof props.run.outputTokens === 'number') {
    return (props.run.inputTokens ?? 0) + (props.run.outputTokens ?? 0)
  }
  return null
})
</script>
