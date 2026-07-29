<template>
  <section v-if="candidates.length > 0" class="content-panel candidate-panel" aria-labelledby="candidate-title">
    <div class="section-heading compact-heading">
      <div>
        <h2 id="candidate-title">可选方案</h2>
        <p class="muted">候选已通过硬约束校验，按综合成本从低到高排列。</p>
      </div>
    </div>

    <div class="candidate-grid">
      <article
        v-for="candidate in candidates"
        :key="candidate.candidateId"
        class="candidate-card"
        :class="{ 'candidate-card--selected': isDraftCandidate(candidate) }"
      >
        <div class="candidate-card__header">
          <div>
            <p class="room-code">方案 {{ candidate.candidateId.replace(/^cand_/, '') }}</p>
            <h3>{{ candidate.roomName }}</h3>
          </div>
          <span v-if="isDraftCandidate(candidate)" class="badge badge-success">当前草案</span>
          <span v-else class="badge">成本 {{ candidate.totalCost }}</span>
        </div>

        <dl class="candidate-facts">
          <div>
            <dt>地点</dt>
            <dd>{{ candidate.building }}</dd>
          </div>
          <div>
            <dt>开始</dt>
            <dd>{{ formatDateTime(candidate.startAt) }}</dd>
          </div>
          <div>
            <dt>结束</dt>
            <dd>{{ formatDateTime(candidate.endAt) }}</dd>
          </div>
        </dl>

        <p class="cost-summary">
          容量余量 {{ candidate.costBreakdown.capacityWaste }} · 时间偏离
          {{ candidate.costBreakdown.preferredTimeDeviation }} · 可选参会者冲突
          {{ candidate.costBreakdown.optionalParticipantConflict }}
        </p>

        <button
          v-if="!isDraftCandidate(candidate)"
          class="secondary-button"
          type="button"
          @click="$emit('select', candidate)"
        >
          使用此方案重新校验
        </button>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { AgentCandidate, AgentDraft } from '../api/types'
import { formatDateTime } from '../utils/format'

const props = defineProps<{
  candidates: readonly AgentCandidate[]
  draft?: AgentDraft | null
}>()

defineEmits<{
  select: [candidate: AgentCandidate]
}>()

function isDraftCandidate(candidate: AgentCandidate): boolean {
  return (
    props.draft?.roomId === candidate.roomId &&
    props.draft.startAt === candidate.startAt &&
    props.draft.endAt === candidate.endAt
  )
}
</script>
