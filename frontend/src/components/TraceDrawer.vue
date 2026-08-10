<template>
  <Teleport to="body">
    <div v-if="open" class="drawer-layer">
      <button class="drawer-overlay" type="button" aria-label="关闭运行过程" @click="$emit('update:open', false)" />
      <aside class="trace-drawer" role="dialog" aria-modal="true" aria-labelledby="trace-drawer-title">
        <header>
          <div><p>安全运行轨迹</p><h2 id="trace-drawer-title">Agent 运行过程</h2></div>
          <button class="icon-button" type="button" aria-label="关闭运行过程" @click="$emit('update:open', false)">
            <X :size="19" aria-hidden="true" />
          </button>
        </header>
        <p class="drawer-help">只展示结构化进度、脱敏 Tool 与 Loop 摘要，不包含隐藏推理或凭据。</p>
        <AgentTimeline :steps="steps" :tools="tools" :run-status="run?.status" />
        <ActivityTimeline :steps="steps" :tools="tools" :loops="loops" :run="run" />
        <RouterLink v-if="runId" class="ui-button ui-button--outline trace-drawer__full-link" :to="{ name: 'agent-run', params: { runId } }">
          <ExternalLink :size="16" aria-hidden="true" />
          打开完整运行记录
        </RouterLink>
      </aside>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ExternalLink, X } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { AgentLoopEvent, AgentRunSummary, AgentStepEvent, AgentToolEvent } from '@/api/types'
import { useModalFocus } from '@/composables/useModalFocus'
import ActivityTimeline from './ActivityTimeline.vue'
import AgentTimeline from './AgentTimeline.vue'

const props = defineProps<{
  open: boolean
  runId: string | null
  steps: readonly AgentStepEvent[]
  tools: readonly AgentToolEvent[]
  loops: readonly AgentLoopEvent[]
  run?: Partial<AgentRunSummary> | null
}>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
useModalFocus(computed(() => props.open), () => emit('update:open', false))
</script>
