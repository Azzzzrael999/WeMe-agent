<template>
  <AppShell title="我的会议" description="查看本人发起或参与的会议，并通过 Java 事务服务手动管理预约。" eyebrow="协作 / 我的会议">
    <template #actions><button class="ui-button ui-button--outline" type="button" :disabled="listLoading" @click="loadMeetings">{{ listLoading ? '刷新中…' : '刷新' }}</button><button class="ui-button ui-button--default" type="button" @click="createDialogOpen=true">＋ 创建会议</button></template>
    <div class="management-layout management-layout--single">
      <Teleport to="body"><div v-if="createDialogOpen" class="dialog-layer"><button class="drawer-overlay" aria-label="关闭创建会议" @click="createDialogOpen=false" /><section class="ui-dialog meeting-form-dialog" aria-labelledby="manual-meeting-title">
        <div class="section-heading compact-heading">
          <div>
            <p class="eyebrow">手动预约</p>
            <h2 id="manual-meeting-title">创建会议</h2>
            <p class="muted">最终提交即为人工确认，仍由 Java 在事务内重新校验时间、容量与并发冲突。</p>
          </div>
        </div>

        <form class="form-grid" @submit.prevent="createMeeting">
          <label class="form-span-2">
            <span>会议主题</span>
            <input v-model.trim="createForm.title" maxlength="128" required :disabled="createSubmitting" />
          </label>
          <label>
            <span>会议类型</span>
            <input v-model.trim="createForm.meetingType" maxlength="32" required :disabled="createSubmitting" />
          </label>
          <label>
            <span>会议室</span>
            <select v-model.number="createForm.roomId" required :disabled="createSubmitting || roomsLoading">
              <option :value="0" disabled>请选择会议室</option>
              <option v-for="room in rooms" :key="room.id" :value="room.id">
                {{ room.name }}（{{ room.capacity }} 人）
              </option>
            </select>
          </label>
          <label>
            <span>开始（Asia/Shanghai）</span>
            <input v-model="createForm.startAt" type="datetime-local" step="1800" required :disabled="createSubmitting" />
          </label>
          <label>
            <span>结束（Asia/Shanghai）</span>
            <input v-model="createForm.endAt" type="datetime-local" step="1800" required :disabled="createSubmitting" />
          </label>
          <label>
            <span>必须参会者 ID</span>
            <input v-model.trim="createForm.requiredParticipantIds" placeholder="1002, 1003" :disabled="createSubmitting" />
          </label>
          <label>
            <span>可选参会者 ID</span>
            <input v-model.trim="createForm.optionalParticipantIds" placeholder="1004" :disabled="createSubmitting" />
          </label>
          <p class="form-span-2 muted form-help">以逗号分隔员工 ID；组织者会由服务端加入必须参会者。会议固定为 30 分钟槽位。</p>
          <p v-if="createError" class="error-message form-span-2" role="alert">{{ createError }}</p>
          <div class="form-actions form-span-2"><button class="secondary-button" type="button" @click="createDialogOpen=false">关闭</button>
            <button class="primary-button" type="submit" :disabled="createSubmitting || rooms.length === 0">
              {{ createSubmitting ? '正在创建…' : '创建并确认' }}
            </button>
          </div>
        </form>
      </section></div></Teleport>

      <section class="content-panel" aria-labelledby="meeting-list-title">
        <div class="section-heading compact-heading">
          <div>
            <h2 id="meeting-list-title">会议列表</h2>
            <p class="muted">显示本人发起或参与的会议；管理员可看到全部会议。</p>
          </div>
          <div class="inline-actions">
            <label class="inline-filter">
              <span>状态</span>
              <select v-model="statusFilter" :disabled="listLoading" @change="loadMeetings">
                <option value="">全部</option>
                <option value="CONFIRMED">已确认</option>
                <option value="CANCELLED">已取消</option>
                <option value="COMPLETED">已完成</option>
              </select>
            </label>
            <button class="secondary-button" type="button" :disabled="listLoading" @click="loadMeetings">
              {{ listLoading ? '刷新中…' : '刷新' }}
            </button>
          </div>
        </div>

        <p v-if="listError" class="error-message" role="alert">{{ listError }}</p>
        <p v-else-if="listLoading" class="status-message">正在加载会议…</p>
        <div v-else-if="meetings.length === 0" class="empty-state">暂时没有符合条件的会议。</div>

        <div v-else class="meeting-table-wrap"><table class="meeting-table"><thead><tr><th>会议</th><th>时间</th><th>会议室</th><th>组织者 / 来源</th><th>状态</th><th><span class="sr-only">操作</span></th></tr></thead><tbody><tr v-for="meeting in meetings" :key="meeting.id"><td><strong>{{ meeting.title }}</strong><span>{{ meeting.meetingNo }}</span></td><td>{{ formatDateTime(meeting.startAt) }}<span>至 {{ formatDateTime(meeting.endAt) }}</span></td><td>{{ meeting.roomName }}</td><td>{{ meeting.organizerName }}<span>{{ meeting.source === 'AGENT' ? '智能编排' : '手动创建' }}</span></td><td><StatusBadge :status="meeting.status" /></td><td><div v-if="meeting.status==='CONFIRMED'" class="table-actions"><button class="text-button" type="button" @click="beginEdit(meeting)">修改</button><button class="text-button danger-text" type="button" @click="pendingCancel=meeting">取消</button></div></td></tr></tbody></table></div>
        <div v-if="!listLoading && !listError && meetings.length" class="meeting-list meeting-list--mobile">
          <article v-for="meeting in meetings" :key="meeting.id" class="meeting-card">
            <div class="meeting-card__header">
              <div>
                <p class="room-code">{{ meeting.meetingNo }} · {{ meeting.source }}</p>
                <h3>{{ meeting.title }}</h3>
              </div>
              <StatusBadge :status="meeting.status" />
            </div>
            <dl class="meeting-facts">
              <div><dt>会议室</dt><dd>{{ meeting.roomName }}</dd></div>
              <div><dt>时间</dt><dd>{{ formatDateTime(meeting.startAt) }} — {{ formatDateTime(meeting.endAt) }}</dd></div>
              <div><dt>组织者</dt><dd>{{ meeting.organizerName }}</dd></div>
              <div><dt>参会者</dt><dd>{{ participantSummary(meeting) }}</dd></div>
            </dl>
            <div v-if="meeting.status === 'CONFIRMED'" class="inline-actions">
              <button class="secondary-button" type="button" @click="beginEdit(meeting)">修改</button>
              <button class="danger-button" type="button" @click="pendingCancel=meeting">取消会议</button>
            </div>
          </article>
        </div>
        <p v-if="!listLoading && !listError" class="result-count">共 {{ total }} 条会议记录</p>
      </section>
    </div>

    <Teleport to="body"><div v-if="editingMeeting" class="dialog-layer"><button class="drawer-overlay" aria-label="关闭编辑会议" @click="editingMeeting=null" /><section class="ui-dialog meeting-form-dialog" aria-labelledby="edit-meeting-title">
      <div class="section-heading compact-heading">
        <div>
          <p class="eyebrow">修改会议</p>
          <h2 id="edit-meeting-title">{{ editingMeeting.title }}</h2>
          <p class="muted">更新会携带当前版本，并在服务端事务中重新校验。</p>
        </div>
        <button class="secondary-button" type="button" :disabled="updateSubmitting" @click="editingMeeting = null">关闭</button>
      </div>

      <form class="form-grid" @submit.prevent="updateMeeting">
        <label class="form-span-2"><span>会议主题</span><input v-model.trim="editForm.title" maxlength="128" required :disabled="updateSubmitting" /></label>
        <label><span>会议类型</span><input v-model.trim="editForm.meetingType" maxlength="32" required :disabled="updateSubmitting" /></label>
        <label>
          <span>会议室</span>
          <select v-model.number="editForm.roomId" required :disabled="updateSubmitting || roomsLoading">
            <option v-for="room in rooms" :key="room.id" :value="room.id">{{ room.name }}（{{ room.capacity }} 人）</option>
          </select>
        </label>
        <label><span>开始（Asia/Shanghai）</span><input v-model="editForm.startAt" type="datetime-local" step="1800" required :disabled="updateSubmitting" /></label>
        <label><span>结束（Asia/Shanghai）</span><input v-model="editForm.endAt" type="datetime-local" step="1800" required :disabled="updateSubmitting" /></label>
        <label><span>必须参会者 ID</span><input v-model.trim="editForm.requiredParticipantIds" :disabled="updateSubmitting" /></label>
        <label><span>可选参会者 ID</span><input v-model.trim="editForm.optionalParticipantIds" :disabled="updateSubmitting" /></label>
        <p v-if="updateError" class="error-message form-span-2" role="alert">{{ updateError }}</p>
        <div class="form-actions form-span-2"><button class="primary-button" type="submit" :disabled="updateSubmitting">{{ updateSubmitting ? '正在保存…' : '保存并重新校验' }}</button></div>
      </form>
    </section></div></Teleport>
    <Teleport to="body"><div v-if="pendingCancel" class="dialog-layer"><button class="drawer-overlay" aria-label="关闭取消确认" @click="pendingCancel=null" /><section class="ui-dialog ui-dialog--sm" role="alertdialog" aria-modal="true" aria-labelledby="cancel-meeting-title"><h2 id="cancel-meeting-title">取消“{{ pendingCancel.title }}”？</h2><p>正式槽位将被释放。只有当前为已确认状态的会议可以取消。</p><footer><button class="ui-button ui-button--outline" type="button" @click="pendingCancel=null">返回</button><button class="ui-button ui-button--destructive" type="button" @click="confirmCancel">确认取消</button></footer></section></div></Teleport>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError, apiRequest } from '../api/client'
import type {
  Meeting,
  MeetingListResult,
  MeetingMutation,
  MeetingRoom,
  MeetingUpdateMutation,
  RoomListResult,
} from '../api/types'
import AppShell from '../components/AppShell.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useModalFocus } from '../composables/useModalFocus'
import { createClientRequestId, formatDateTime, parseEmployeeIds, toShanghaiDateTimeLocal, toShanghaiOffset } from '../utils/format'

interface MeetingForm {
  title: string
  meetingType: string
  roomId: number
  startAt: string
  endAt: string
  requiredParticipantIds: string
  optionalParticipantIds: string
}

const rooms = ref<MeetingRoom[]>([])
const roomsLoading = ref(false)
const meetings = ref<Meeting[]>([])
const total = ref(0)
const statusFilter = ref('')
const listLoading = ref(true)
const listError = ref('')
const createSubmitting = ref(false)
const createError = ref('')
const updateSubmitting = ref(false)
const updateError = ref('')
const editingMeeting = ref<Meeting | null>(null)
const createDialogOpen = ref(false)
const pendingCancel = ref<Meeting | null>(null)
useModalFocus(computed(() => createDialogOpen.value || editingMeeting.value !== null || pendingCancel.value !== null), () => { createDialogOpen.value=false; editingMeeting.value=null; pendingCancel.value=null })
let createIdempotencyKey: string | null = null

const createForm = reactive<MeetingForm>({
  title: '架构评审',
  meetingType: 'ARCHITECTURE_REVIEW',
  roomId: 0,
  startAt: '',
  endAt: '',
  requiredParticipantIds: '',
  optionalParticipantIds: '',
})

const editForm = reactive<MeetingForm>({
  title: '',
  meetingType: '',
  roomId: 0,
  startAt: '',
  endAt: '',
  requiredParticipantIds: '',
  optionalParticipantIds: '',
})

async function loadRooms(): Promise<void> {
  roomsLoading.value = true
  try {
    const result = await apiRequest<RoomListResult>('/rooms')
    rooms.value = result.items
    if (createForm.roomId === 0 && result.items.length > 0) {
      createForm.roomId = result.items[0].id
    }
  } catch (error) {
    createError.value = error instanceof ApiError ? error.message : '会议室列表加载失败。'
  } finally {
    roomsLoading.value = false
  }
}

async function loadMeetings(): Promise<void> {
  listLoading.value = true
  listError.value = ''
  try {
    const query = new URLSearchParams({ page: '1', size: '50' })
    if (statusFilter.value.length > 0) {
      query.set('status', statusFilter.value)
    }
    const result = await apiRequest<MeetingListResult>(`/meetings?${query.toString()}`)
    meetings.value = result.items
    total.value = result.total
  } catch (error) {
    listError.value = error instanceof ApiError ? error.message : '会议列表加载失败。'
  } finally {
    listLoading.value = false
  }
}

function validateForm(form: MeetingForm): MeetingMutation | null {
  const requiredParticipantIds = parseEmployeeIds(form.requiredParticipantIds)
  const optionalParticipantIds = parseEmployeeIds(form.optionalParticipantIds)
  const duplicated = optionalParticipantIds.some((id) => requiredParticipantIds.includes(id))
  if (form.roomId <= 0 || form.startAt.length === 0 || form.endAt.length === 0) {
    return null
  }
  if (!/:(00|30)$/.test(form.startAt) || !/:(00|30)$/.test(form.endAt)) {
    return null
  }
  if (form.endAt <= form.startAt || duplicated) {
    return null
  }
  return {
    title: form.title,
    meetingType: form.meetingType,
    roomId: form.roomId,
    startAt: toShanghaiOffset(form.startAt),
    endAt: toShanghaiOffset(form.endAt),
    requiredParticipantIds,
    optionalParticipantIds,
  }
}

async function createMeeting(): Promise<void> {
  if (createSubmitting.value) {
    return
  }
  const payload = validateForm(createForm)
  if (payload === null) {
    createError.value = '请填写有效的 30 分钟槽位，结束时间须晚于开始时间，且参会者不能重复。'
    return
  }
  createSubmitting.value = true
  createError.value = ''
  createIdempotencyKey ??= createClientRequestId()
  try {
    await apiRequest<Meeting>('/meetings', {
      method: 'POST',
      headers: { 'Idempotency-Key': createIdempotencyKey },
      body: JSON.stringify(payload),
    })
    createIdempotencyKey = null
    createForm.startAt = ''
    createForm.endAt = ''
    createForm.requiredParticipantIds = ''
    createForm.optionalParticipantIds = ''
    await loadMeetings()
  } catch (error) {
    createError.value = error instanceof ApiError ? error.message : '手动创建会议失败。'
  } finally {
    createSubmitting.value = false
  }
}

function beginEdit(meeting: Meeting): void {
  editingMeeting.value = meeting
  updateError.value = ''
  editForm.title = meeting.title
  editForm.meetingType = meeting.meetingType
  editForm.roomId = meeting.roomId
  editForm.startAt = toShanghaiDateTimeLocal(meeting.startAt)
  editForm.endAt = toShanghaiDateTimeLocal(meeting.endAt)
  editForm.requiredParticipantIds = meeting.participants
    .filter((participant) => participant.participantType === 'REQUIRED')
    .map((participant) => participant.employeeId)
    .join(', ')
  editForm.optionalParticipantIds = meeting.participants
    .filter((participant) => participant.participantType === 'OPTIONAL')
    .map((participant) => participant.employeeId)
    .join(', ')
}

async function updateMeeting(): Promise<void> {
  const meeting = editingMeeting.value
  if (meeting === null || updateSubmitting.value) {
    return
  }
  const payload = validateForm(editForm)
  if (payload === null) {
    updateError.value = '请填写有效的 30 分钟槽位，结束时间须晚于开始时间，且参会者不能重复。'
    return
  }
  updateSubmitting.value = true
  updateError.value = ''
  const request: MeetingUpdateMutation = { ...payload, expectedVersion: meeting.version }
  try {
    await apiRequest<Meeting>(`/meetings/${meeting.id}`, {
      method: 'PUT',
      body: JSON.stringify(request),
    })
    editingMeeting.value = null
    await loadMeetings()
  } catch (error) {
    updateError.value = error instanceof ApiError ? error.message : '会议修改失败。'
  } finally {
    updateSubmitting.value = false
  }
}

async function cancelMeeting(meeting: Meeting): Promise<void> {
  listError.value = ''
  try {
    await apiRequest<Meeting>(`/meetings/${meeting.id}`, { method: 'DELETE' })
    await loadMeetings()
  } catch (error) {
    listError.value = error instanceof ApiError ? error.message : '会议取消失败。'
  }
}

async function confirmCancel(): Promise<void> { const meeting = pendingCancel.value; if (meeting === null) return; pendingCancel.value = null; await cancelMeeting(meeting) }

function participantSummary(meeting: Meeting): string {
  return meeting.participants.length > 0
    ? meeting.participants.map((participant) => participant.displayName).join('、')
    : '无额外参会者'
}

onMounted(() => {
  void Promise.all([loadRooms(), loadMeetings()])
})
</script>
