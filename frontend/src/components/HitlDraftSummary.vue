<template>
  <div class="hitl-draft-summary">
    <div class="hitl-operation-heading">
      <StatusBadge :status="operationStatus" :label="operationLabel" />
      <p>{{ operationHelp }}</p>
    </div>

    <div v-if="actionType === 'RESCHEDULE' && reschedule" class="hitl-diff">
      <section>
        <span>原会议</span>
        <h3>{{ reschedule.originalMeeting.title }}</h3>
        <dl>
          <div><dt>会议室</dt><dd>{{ reschedule.originalMeeting.roomName }}</dd></div>
          <div><dt>时间</dt><dd>{{ formatDateTime(reschedule.originalMeeting.startAt) }} — {{ formatDateTime(reschedule.originalMeeting.endAt) }}</dd></div>
        </dl>
      </section>
      <span class="hitl-diff__arrow" aria-hidden="true"><ArrowRight :size="20" /></span>
      <section class="hitl-diff__after">
        <span>改期方案</span>
        <h3>{{ reschedule.proposedMeeting.title }}</h3>
        <dl>
          <div><dt>会议室</dt><dd>{{ reschedule.proposedMeeting.roomName }}</dd></div>
          <div><dt>时间</dt><dd>{{ formatDateTime(reschedule.proposedMeeting.startAt) }} — {{ formatDateTime(reschedule.proposedMeeting.endAt) }}</dd></div>
        </dl>
      </section>
    </div>

    <section v-else-if="actionType === 'CANCEL' && cancellation" class="hitl-target-meeting">
      <span>取消目标会议</span>
      <h3>{{ cancellation.meeting.title }}</h3>
      <dl>
        <div><dt>会议室</dt><dd>{{ cancellation.meeting.roomName }}</dd></div>
        <div><dt>时间</dt><dd>{{ formatDateTime(cancellation.meeting.startAt) }} — {{ formatDateTime(cancellation.meeting.endAt) }}</dd></div>
      </dl>
    </section>

    <section v-else-if="createDraft" class="hitl-target-meeting hitl-target-meeting--create">
      <span>新建会议草案</span>
      <h3>{{ createDraft.title }}</h3>
      <dl>
        <div><dt>会议室</dt><dd>{{ createDraft.roomName }}</dd></div>
        <div><dt>时间</dt><dd>{{ formatDateTime(createDraft.startAt) }} — {{ formatDateTime(createDraft.endAt) }}</dd></div>
        <div><dt>必需参会者</dt><dd>{{ participantNames(createDraft.requiredParticipants) }}</dd></div>
        <div><dt>可选参会者</dt><dd>{{ participantNames(createDraft.optionalParticipants) }}</dd></div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ArrowRight } from '@lucide/vue'
import { computed } from 'vue'

import { isCancellationPreview, isRescheduleDraft, proposedDraft } from '@/api/agent-view'
import type { AgentDraftParticipant, AgentHitlDraft, AgentOperationType } from '@/api/types'
import { formatDateTime } from '@/utils/format'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ actionType: AgentOperationType; draft: AgentHitlDraft }>()
const reschedule = computed(() => isRescheduleDraft(props.draft) ? props.draft : null)
const cancellation = computed(() => isCancellationPreview(props.draft) ? props.draft : null)
const createDraft = computed(() => proposedDraft(props.draft))
const operationLabel = computed(() => ({ CREATE: '创建会议', RESCHEDULE: '改期会议', CANCEL: '取消会议' })[props.actionType])
const operationHelp = computed(() => ({
  CREATE: '确认前不会创建正式会议或占用槽位。',
  RESCHEDULE: '确认前原会议保持不变，调整后方案会再次经过业务校验。',
  CANCEL: '确认前目标会议保持有效，不会释放槽位。',
})[props.actionType])
const operationStatus = computed(() => props.actionType === 'CANCEL' ? 'CONFLICT' : 'PENDING')

function participantNames(participants: AgentDraftParticipant[]): string {
  return participants.length > 0 ? participants.map((participant) => participant.displayName).join('、') : '未提供'
}
</script>
