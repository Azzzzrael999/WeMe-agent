<template>
  <section class="content-panel hitl-panel" aria-labelledby="hitl-title">
    <div class="section-heading compact-heading">
      <div>
        <p class="eyebrow">需要你的确认</p>
        <h2 id="hitl-title">{{ draft.title }}</h2>
        <p class="muted">草案在确认前不会占用会议室或写入正式会议。</p>
      </div>
      <span class="badge badge-warning">待确认</span>
    </div>

    <dl class="draft-facts">
      <div>
        <dt>会议室</dt>
        <dd>{{ draft.roomName }}</dd>
      </div>
      <div>
        <dt>时间（Asia/Shanghai）</dt>
        <dd>{{ formatDateTime(draft.startAt) }} — {{ formatDateTime(draft.endAt) }}</dd>
      </div>
      <div>
        <dt>必须参会</dt>
        <dd>{{ participantNames(draft.requiredParticipants) }}</dd>
      </div>
      <div>
        <dt>可选参会</dt>
        <dd>{{ participantNames(draft.optionalParticipants) }}</dd>
      </div>
      <div v-if="expiresAt">
        <dt>确认有效期至</dt>
        <dd>{{ formatDateTime(expiresAt) }}</dd>
      </div>
    </dl>

    <div v-if="editing" class="edit-draft-form">
      <label>
        <span>会议室 ID（可选）</span>
        <input v-model.trim="editRoomId" inputmode="numeric" placeholder="例如 102" :disabled="busy" />
      </label>
      <label>
        <span>开始时间（可选，Asia/Shanghai）</span>
        <input v-model="editStartAt" type="datetime-local" step="1800" :disabled="busy" />
      </label>
      <p class="muted">编辑仅允许会议室或开始时间，提交后会重新查询并求解候选。</p>
      <p v-if="editError" class="error-message" role="alert">{{ editError }}</p>
    </div>

    <label class="feedback-field">
      <span>反馈（可选）</span>
      <textarea
        :value="feedback"
        rows="2"
        maxlength="1000"
        placeholder="例如：请优先研发楼，或尽量避开 16:00"
        :disabled="busy"
        @input="updateFeedback"
      ></textarea>
      <small class="muted">反馈将随本次确认或编辑请求传递给后续重规划上下文，不会显示在本页 Trace 中。</small>
    </label>

    <div class="hitl-actions">
      <button class="primary-button" type="button" :disabled="busy" @click="$emit('accept')">
        {{ busy ? '处理中…' : '接受并确认' }}
      </button>
      <button class="secondary-button" type="button" :disabled="busy" @click="toggleEdit">
        {{ editing ? '收起编辑' : '编辑后重新校验' }}
      </button>
      <button v-if="editing" class="secondary-button" type="button" :disabled="busy" @click="submitEdit">
        提交编辑
      </button>
      <button class="danger-button" type="button" :disabled="busy" @click="$emit('reject')">
        拒绝草案
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import type { AgentDraft, AgentDraftParticipant } from '../api/types'
import { formatDateTime, toShanghaiDateTimeLocal, toShanghaiOffset } from '../utils/format'

const props = defineProps<{
  draft: AgentDraft
  expiresAt?: string
  busy: boolean
  feedback: string
}>()

const emit = defineEmits<{
  accept: []
  reject: []
  edit: [changes: { roomId?: number; startAt?: string }]
  'update:feedback': [value: string]
}>()

const editing = ref(false)
const editRoomId = ref('')
const editStartAt = ref('')
const editError = ref('')

watch(
  () => props.draft,
  (draft) => {
    editing.value = false
    editRoomId.value = String(draft.roomId)
    editStartAt.value = toShanghaiDateTimeLocal(draft.startAt)
    editError.value = ''
  },
  { immediate: true },
)

function participantNames(participants: AgentDraftParticipant[]): string {
  return participants.length > 0 ? participants.map((participant) => participant.displayName).join('、') : '无'
}

function toggleEdit(): void {
  editing.value = !editing.value
  editError.value = ''
}

function updateFeedback(event: Event): void {
  const target = event.target
  if (target instanceof HTMLTextAreaElement) {
    emit('update:feedback', target.value)
  }
}

function submitEdit(): void {
  const roomId = editRoomId.value.length > 0 ? Number.parseInt(editRoomId.value, 10) : undefined
  const startAt = editStartAt.value.length > 0 ? toShanghaiOffset(editStartAt.value) : undefined
  const originalStart = toShanghaiDateTimeLocal(props.draft.startAt)
  const changedRoom = roomId !== undefined && roomId !== props.draft.roomId
  const changedStart = editStartAt.value.length > 0 && editStartAt.value !== originalStart

  if (roomId !== undefined && (!Number.isSafeInteger(roomId) || roomId <= 0)) {
    editError.value = '会议室 ID 必须是正整数。'
    return
  }
  if (editStartAt.value.length > 0 && !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):(?:00|30)$/.test(editStartAt.value)) {
    editError.value = '开始时间必须落在 30 分钟槽位。'
    return
  }
  if (!changedRoom && !changedStart) {
    editError.value = '请至少修改会议室或开始时间。'
    return
  }

  editError.value = ''
  emit('edit', {
    ...(changedRoom ? { roomId } : {}),
    ...(changedStart && startAt !== undefined ? { startAt } : {}),
  })
}
</script>
