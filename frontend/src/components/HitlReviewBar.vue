<template>
  <div class="hitl-review-bar" role="region" aria-labelledby="hitl-review-title">
    <div class="hitl-review-bar__summary">
      <span class="hitl-icon" aria-hidden="true">!</span>
      <div>
        <p id="hitl-review-title">需要你的确认 · {{ operationLabel }}</p>
        <strong>{{ summaryTitle }}</strong>
        <span>{{ summaryLine }}<template v-if="expiresAt"> · {{ formatDateTime(expiresAt) }} 前有效</template></span>
      </div>
    </div>
    <div class="hitl-review-bar__actions">
      <button class="ui-button ui-button--default" type="button" :disabled="busy" @click="$emit('accept')">
        {{ busy ? '处理中…' : acceptLabel }}
      </button>
      <button v-if="actionType !== 'CANCEL'" class="ui-button ui-button--outline" type="button" :disabled="busy" @click="editing = true">
        编辑后重新规划
      </button>
      <button class="ui-button ui-button--ghost hitl-reject" type="button" :disabled="busy" @click="confirmReject = true">拒绝</button>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="editing && editableDraft" class="dialog-layer">
      <button class="drawer-overlay" aria-label="关闭编辑" @click="editing=false" />
      <section class="ui-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-title">
        <header><div><p>HITL 编辑 · {{ operationLabel }}</p><h2 id="edit-title">修改草案并重新规划</h2></div><button class="icon-button" type="button" aria-label="关闭编辑" @click="editing=false">×</button></header>
        <p>只允许调整会议室或开始时间；提交后会重新执行规则与可用性校验。</p>
        <label><span>会议室 ID</span><input v-model.trim="roomId" inputmode="numeric" /></label>
        <label><span>开始时间（Asia/Shanghai）</span><input v-model="startAt" type="datetime-local" step="1800" /></label>
        <label><span>反馈（可选）</span><textarea :value="feedback" rows="2" maxlength="1000" @input="updateFeedback" /></label>
        <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
        <footer><button class="ui-button ui-button--outline" type="button" @click="editing=false">取消</button><button class="ui-button ui-button--default" type="button" @click="submitEdit">重新规划</button></footer>
      </section>
    </div>
    <div v-if="confirmReject" class="dialog-layer">
      <button class="drawer-overlay" aria-label="关闭拒绝确认" @click="confirmReject=false" />
      <section class="ui-dialog ui-dialog--sm" role="alertdialog" aria-modal="true" aria-labelledby="reject-title">
        <h2 id="reject-title">拒绝这份{{ operationLabel }}草案？</h2>
        <p>草案会失效，当前 Agent Run 将结束；不会产生正式业务写入。</p>
        <footer><button class="ui-button ui-button--outline" type="button" @click="confirmReject=false">返回</button><button class="ui-button ui-button--destructive" type="button" @click="reject">确认拒绝</button></footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { isCancellationPreview, proposedDraft } from '@/api/agent-view'
import type { AgentHitlDraft, AgentOperationType } from '@/api/types'
import { useModalFocus } from '@/composables/useModalFocus'
import { formatDateTime, toShanghaiDateTimeLocal, toShanghaiOffset } from '@/utils/format'

const props = defineProps<{
  actionType: AgentOperationType
  draft: AgentHitlDraft
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
const confirmReject = ref(false)
const roomId = ref('')
const startAt = ref('')
const error = ref('')
const editableDraft = computed(() => proposedDraft(props.draft))
const operationLabel = computed(() => ({ CREATE: '创建会议', RESCHEDULE: '改期会议', CANCEL: '取消会议' })[props.actionType])
const acceptLabel = computed(() => ({ CREATE: '接受并创建', RESCHEDULE: '接受并改期', CANCEL: '确认取消会议' })[props.actionType])
const summaryTitle = computed(() => isCancellationPreview(props.draft) ? props.draft.meeting.title : (editableDraft.value?.title ?? '待确认草案'))
const summaryLine = computed(() => {
  if (isCancellationPreview(props.draft)) {
    return `${props.draft.meeting.roomName} · ${formatDateTime(props.draft.meeting.startAt)}`
  }
  const draft = editableDraft.value
  return draft === null ? '' : `${draft.roomName} · ${formatDateTime(draft.startAt)} — ${formatDateTime(draft.endAt)}`
})

useModalFocus(computed(() => editing.value || confirmReject.value), () => { editing.value = false; confirmReject.value = false })
watch(editableDraft, (draft) => {
  editing.value = false
  roomId.value = draft === null ? '' : String(draft.roomId)
  startAt.value = draft === null ? '' : toShanghaiDateTimeLocal(draft.startAt)
  error.value = ''
}, { immediate: true })

function updateFeedback(event: Event): void {
  if (event.target instanceof HTMLTextAreaElement) {
    emit('update:feedback', event.target.value)
  }
}

function submitEdit(): void {
  const draft = editableDraft.value
  if (draft === null) {
    error.value = '当前操作不支持编辑。'
    return
  }
  const nextRoom = Number.parseInt(roomId.value, 10)
  const original = toShanghaiDateTimeLocal(draft.startAt)
  const changedRoom = Number.isSafeInteger(nextRoom) && nextRoom > 0 && nextRoom !== draft.roomId
  const changedStart = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):(?:00|30)$/.test(startAt.value) && startAt.value !== original
  if (!changedRoom && !changedStart) {
    error.value = '请至少修改会议室或开始时间，且时间须落在 30 分钟槽位。'
    return
  }
  editing.value = false
  emit('edit', {
    ...(changedRoom ? { roomId: nextRoom } : {}),
    ...(changedStart ? { startAt: toShanghaiOffset(startAt.value) } : {}),
  })
}

function reject(): void {
  confirmReject.value = false
  emit('reject')
}
</script>
