<template>
  <Teleport to="body"><div v-if="open" class="drawer-layer"><button class="drawer-overlay" aria-label="关闭运行过程" @click="$emit('update:open', false)" /><aside class="trace-drawer" role="dialog" aria-modal="true" aria-labelledby="trace-drawer-title"><header><div><p>安全运行轨迹</p><h2 id="trace-drawer-title">Agent 运行过程</h2></div><button class="icon-button" type="button" aria-label="关闭运行过程" @click="$emit('update:open', false)">×</button></header><p class="drawer-help">只展示结构化步骤、脱敏工具摘要和耗时，不包含隐藏推理或凭据。</p><TraceTimeline :steps="steps" :tools="tools" /><RouterLink v-if="runId" class="ui-button ui-button--outline" :to="{ name: 'agent-run', params: { runId } }">打开完整 Run 详情</RouterLink></aside></div></Teleport>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { AgentStepEvent, AgentToolEvent } from '@/api/types'
import TraceTimeline from './TraceTimeline.vue'
import { useModalFocus } from '@/composables/useModalFocus'

const props = defineProps<{ open: boolean; runId: string | null; steps: readonly AgentStepEvent[]; tools: readonly AgentToolEvent[] }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
useModalFocus(computed(() => props.open), () => emit('update:open', false))
</script>
