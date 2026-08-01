<template>
  <div v-if="candidates.length" class="candidate-comparison">
    <article v-for="(candidate, index) in candidates" :key="candidate.candidateId" class="candidate-option" :class="{ 'candidate-option--selected': selected(candidate) }">
      <header><div><span class="candidate-rank">方案 {{ index + 1 }}</span><h3>{{ candidate.roomName }}</h3><p>{{ candidate.building }}</p></div><StatusBadge v-if="index === 0" status="SUCCESS" label="推荐" /></header>
      <div class="candidate-time"><strong>{{ formatDateTime(candidate.startAt) }}</strong><span>至 {{ formatDateTime(candidate.endAt) }}</span></div>
      <div class="candidate-score"><span>综合成本</span><strong>{{ candidate.totalCost }}</strong></div>
      <dl class="cost-breakdown">
        <div v-for="item in costs(candidate)" :key="item.label" :class="{ muted: item.value === 0 }"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div>
      </dl>
      <button v-if="!selected(candidate)" class="ui-button ui-button--outline ui-button--sm" type="button" @click="$emit('select', candidate)">选择并重新校验</button>
      <span v-else class="selected-label">当前草案</span>
    </article>
  </div>
  <EmptyState v-else title="还没有候选计划" description="Agent 完成资源查询和硬约束验证后，最多展示三个方案。" icon="◇" />
</template>
<script setup lang="ts">
import type { AgentCandidate, AgentDraft } from '@/api/types'
import { formatDateTime } from '@/utils/format'
import EmptyState from './EmptyState.vue'; import StatusBadge from './StatusBadge.vue'
const props = defineProps<{ candidates: readonly AgentCandidate[]; draft: AgentDraft | null }>()
defineEmits<{ select: [candidate: AgentCandidate] }>()
function selected(candidate: AgentCandidate): boolean { return props.draft?.roomId === candidate.roomId && props.draft.startAt === candidate.startAt }
function costs(candidate: AgentCandidate): { label: string; value: number }[] { return [
  { label: '可选人员冲突', value: candidate.costBreakdown.optionalParticipantConflict }, { label: '时间偏离', value: candidate.costBreakdown.preferredTimeDeviation },
  { label: '楼宇距离', value: candidate.costBreakdown.buildingDistance }, { label: '容量余量', value: candidate.costBreakdown.capacityWaste },
  { label: '偏好违反', value: candidate.costBreakdown.preferenceViolation }, { label: '换房成本', value: candidate.costBreakdown.roomChange },
] }
</script>
