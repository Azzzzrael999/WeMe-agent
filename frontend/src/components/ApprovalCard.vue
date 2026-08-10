<template>
  <article class="approval-card" :aria-labelledby="`approval-${runId}`">
    <header class="approval-card__header">
      <div>
        <div class="approval-card__kicker">
          <StatusBadge status="WAITING_CONFIRMATION" />
          <span>{{ operationLabel }}</span>
        </div>
        <h2 :id="`approval-${runId}`">{{ title }}</h2>
        <p>确认前不会改变会议或占用会议室。</p>
      </div>
      <RouterLink
        class="ui-button ui-button--outline ui-button--sm"
        :to="{ name: 'agent-run', params: { runId } }"
      >
        <Activity :size="15" aria-hidden="true" />
        查看运行记录
      </RouterLink>
    </header>

    <HitlDraftSummary :action-type="actionType" :draft="draft" />

    <div class="approval-card__meta">
      <span><Clock3 :size="15" aria-hidden="true" />{{ expiryLabel }}</span>
      <span><ShieldCheck :size="15" aria-hidden="true" />显式确认后才会执行</span>
    </div>

    <p v-if="expired" class="approval-expired" role="status">
      <CircleAlert :size="16" aria-hidden="true" />
      确认令牌已过期，请返回智能编排重新生成草案。
    </p>

    <HitlReviewBar
      :action-type="actionType"
      :draft="draft"
      :expires-at="expiresAt"
      :expired="expired"
      :busy="busy"
      :feedback="feedback"
      @update:feedback="$emit('update:feedback', $event)"
      @accept="$emit('accept')"
      @reject="$emit('reject')"
      @edit="$emit('edit', $event)"
    />
  </article>
</template>

<script setup lang="ts">
import { Activity, CircleAlert, Clock3, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { isCancellationPreview, proposedDraft } from '@/api/agent-view'
import type { AgentHitlDraft, AgentOperationType } from '@/api/types'
import { formatDateTime } from '@/utils/format'
import HitlDraftSummary from './HitlDraftSummary.vue'
import HitlReviewBar from './HitlReviewBar.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  runId: string
  actionType: AgentOperationType
  draft: AgentHitlDraft
  expiresAt?: string
  expired: boolean
  busy: boolean
  feedback: string
}>()

defineEmits<{
  accept: []
  reject: []
  edit: [changes: { roomId?: number; startAt?: string }]
  'update:feedback': [value: string]
}>()

const operationLabel = computed(() => ({
  CREATE: '创建会议',
  RESCHEDULE: '会议改期',
  CANCEL: '取消会议',
})[props.actionType])
const title = computed(() => {
  if (isCancellationPreview(props.draft)) {
    return props.draft.meeting.title
  }
  return proposedDraft(props.draft)?.title ?? '待确认草案'
})
const expiryLabel = computed(() => props.expiresAt === undefined
  ? '有效期由服务端控制'
  : props.expired
    ? `已于 ${formatDateTime(props.expiresAt)} 过期`
    : `${formatDateTime(props.expiresAt)} 前有效`)
</script>
