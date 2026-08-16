import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import {
  createPostMeetingDraft,
  getMeetingLifecycle,
  listVisibleMeetings,
  reviewPostMeetingDraft,
  saveMeetingPreparation,
  updateMeetingActionItem,
} from '../api/meeting-lifecycle'
import type {
  Meeting,
  MeetingActionItem,
  MeetingActionItemStatus,
  MeetingLifecycle,
  MeetingMaterialStatus,
  PostMeetingDraftContent,
} from '../api/types'
import { authStore } from '../auth/store'
import { createClientRequestId, formatDateTime, toShanghaiDateTimeLocal, toShanghaiOffset } from '../utils/format'
import { isTechnicalDemoMeeting } from '../utils/labels'

export function useMeetingLifecycle() {
  interface AgendaFormItem {
    clientId: string
    topic: string
    ownerEmployeeId: number
    plannedMinutes: number
  }

  interface MaterialFormItem {
    clientId: string
    title: string
    ownerEmployeeId: number
    required: boolean
    status: MeetingMaterialStatus
    versionLabel: string
    note: string
  }

  interface EditablePostMeetingContent {
    minutes: { background: string; discussionSummary: string; conclusion: string }
    decisions: Array<{ content: string; rationale: string }>
    actionItems: Array<{
      title: string
      description: string
      assigneeEmployeeId: number
      dueAt: string
    }>
  }

  const route = useRoute()
  const router = useRouter()
  const meetings = ref<Meeting[]>([])
  const selectedMeetingId = ref<number | null>(null)
  const meetingListLoading = ref(true)
  const meetingListError = ref('')
  const lifecycle = ref<MeetingLifecycle | null>(null)
  const lifecycleLoading = ref(false)
  const lifecycleError = ref('')
  const operationError = ref('')
  const operationNotice = ref('')
  const agendaForms = ref<AgendaFormItem[]>([])
  const materialForms = ref<MaterialFormItem[]>([])
  const preparationSaving = ref(false)
  const transcript = ref('')
  const draftSubmitting = ref(false)
  const draftReviewing = ref(false)
  const draftEditor = ref<EditablePostMeetingContent | null>(null)
  const draftSnapshot = ref('')
  const actionStatusEdits = reactive<Record<number, MeetingActionItemStatus>>({})
  const actionUpdatingId = ref<number | null>(null)
  let formSequence = 0
  let lifecycleRequestEpoch = 0
  let draftIdempotencyKey: string | null = null
  let draftIdempotencyTranscript = ''

  const draft = computed(() => lifecycle.value?.postMeeting.draft ?? null)
  const canEditPreparation = computed(() => lifecycle.value?.permissions.canEditPreparation === true && isFutureMeeting.value)
  const isFutureMeeting = computed(() => {
    const startAt = lifecycle.value?.meeting.startAt
    return startAt !== undefined && new Date(startAt).getTime() > Date.now()
  })
  const meetingDurationMinutes = computed(() => {
    const meeting = lifecycle.value?.meeting
    if (!meeting) return 0
    return Math.round((new Date(meeting.endAt).getTime() - new Date(meeting.startAt).getTime()) / 60000)
  })
  const agendaTotalMinutes = computed(() => agendaForms.value.reduce((total, item) => total + (Number(item.plannedMinutes) || 0), 0))
  const meetingPeople = computed(() => {
    const meeting = lifecycle.value?.meeting
    if (!meeting) return []
    const people = new Map<number, string>([[meeting.organizerId, meeting.organizerName]])
    meeting.participants.forEach((participant) => people.set(participant.employeeId, participant.displayName))
    return [...people].map(([id, name]) => ({ id, name }))
  })
  const hasFormalPostMeetingRecord = computed(() => lifecycle.value?.postMeeting.minutes !== null)
  const shouldShowTranscriptForm = computed(() => {
    if (hasFormalPostMeetingRecord.value) return false
    return draft.value === null || ['FAILED', 'REJECTED'].includes(draft.value.status)
  })
  const draftDirty = computed(() => draftEditor.value !== null && JSON.stringify(draftEditor.value) !== draftSnapshot.value)

  function nextClientId(prefix: string): string {
    formSequence += 1
    return `${prefix}-${formSequence}`
  }

  function parseMeetingId(value: unknown): number | null {
    if (typeof value !== 'string' || !/^\d{1,18}$/.test(value)) return null
    const parsed = Number(value)
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
  }

  function meetingOptionLabel(meeting: Meeting): string {
    const status = meeting.status === 'CONFIRMED' ? '已确认' : meeting.status === 'COMPLETED' ? '已完成' : meeting.status === 'CANCELLED' ? '已取消' : '其他状态'
    return `${formatDateTime(meeting.startAt)} · ${meeting.title} · ${status}`
  }

  function checklistLabel(code: string): string {
    const labels: Record<string, string> = {
      AGENDA_PRESENT: '议程已配置',
      AGENDA_DURATION: '议程时长合理',
      AGENDA_OWNERS: '议题负责人有效',
      MATERIALS_READY: '必需材料已就绪',
      ROOM_ACTIVE: '会议室可用',
      PARTICIPANTS_PRESENT: '必需参会者有效',
    }
    return labels[code] ?? '其他准备项'
  }

  function actionStatusLabel(status: MeetingActionItemStatus): string {
    return { OPEN: '待处理', IN_PROGRESS: '进行中', DONE: '已完成' }[status]
  }

  function employeeName(employeeId: number | null | undefined): string {
    if (employeeId === null || employeeId === undefined) return '—'
    const meetingPerson = meetingPeople.value.find((person) => person.id === employeeId)
    if (meetingPerson !== undefined) return meetingPerson.name
    const currentUser = authStore.state.user
    return currentUser?.id === employeeId ? currentUser.displayName : '已授权审核人'
  }

  function sortMeetings(items: Meeting[]): Meeting[] {
    const rank = (status: string): number => status === 'CONFIRMED' ? 0 : status === 'COMPLETED' ? 1 : status === 'CANCELLED' ? 2 : 3
    return [...items].sort((left, right) => {
      const statusRank = rank(left.status) - rank(right.status)
      if (statusRank !== 0) return statusRank
      return left.status === 'CONFIRMED'
        ? left.startAt.localeCompare(right.startAt)
        : right.startAt.localeCompare(left.startAt)
    })
  }

  async function loadMeetingChoices(): Promise<void> {
    meetingListLoading.value = true
    meetingListError.value = ''
    try {
      const result = await listVisibleMeetings()
      meetings.value = sortMeetings(result.items.filter((meeting) => !isTechnicalDemoMeeting(meeting.title, meeting.meetingType)))
      const routeMeetingId = parseMeetingId(route.query.meetingId)
      const initialMeetingId = routeMeetingId !== null && meetings.value.some((meeting) => meeting.id === routeMeetingId)
        ? routeMeetingId
        : meetings.value[0]?.id ?? null
      selectedMeetingId.value = initialMeetingId
      if (initialMeetingId !== null) await loadLifecycle(false)
    } catch (error) {
      meetingListError.value = userMessage(error, '会议列表加载失败，请稍后重试。')
    } finally {
      meetingListLoading.value = false
    }
  }

  async function handleMeetingSelection(): Promise<void> {
    if (selectedMeetingId.value === null) return
    await router.replace({ name: 'meeting-lifecycle', query: { meetingId: String(selectedMeetingId.value) } })
    await loadLifecycle()
  }

  async function loadLifecycle(clearFeedback = true): Promise<void> {
    const meetingId = selectedMeetingId.value
    if (meetingId === null) return
    const epoch = ++lifecycleRequestEpoch
    lifecycleLoading.value = true
    lifecycleError.value = ''
    if (clearFeedback) clearOperationFeedback()
    try {
      const result = await getMeetingLifecycle(meetingId)
      if (epoch !== lifecycleRequestEpoch) return
      applyLifecycle(result)
      if (!meetings.value.some((meeting) => meeting.id === result.meeting.id)) {
        meetings.value = sortMeetings([...meetings.value, result.meeting])
      }
      if (route.query.meetingId !== String(meetingId)) {
        await router.replace({ name: 'meeting-lifecycle', query: { meetingId: String(meetingId) } })
      }
    } catch (error) {
      if (epoch === lifecycleRequestEpoch) lifecycleError.value = userMessage(error, '生命周期数据加载失败，请稍后重试。')
    } finally {
      if (epoch === lifecycleRequestEpoch) lifecycleLoading.value = false
    }
  }

  function applyLifecycle(result: MeetingLifecycle): void {
    if (lifecycle.value?.meeting.id !== result.meeting.id) {
      transcript.value = ''
      draftIdempotencyKey = null
      draftIdempotencyTranscript = ''
    }
    lifecycle.value = result
    agendaForms.value = result.preparation.agendaItems
      .slice()
      .sort((left, right) => left.sequenceNo - right.sequenceNo)
      .map((item) => ({ clientId: nextClientId('agenda'), topic: item.topic, ownerEmployeeId: item.ownerEmployeeId, plannedMinutes: item.plannedMinutes }))
    materialForms.value = result.preparation.materials
      .slice()
      .sort((left, right) => left.sequenceNo - right.sequenceNo)
      .map((item) => ({
        clientId: nextClientId('material'),
        title: item.title,
        ownerEmployeeId: item.ownerEmployeeId,
        required: item.required,
        status: item.status,
        versionLabel: item.versionLabel ?? '',
        note: item.note ?? '',
      }))
    const content = result.postMeeting.draft?.content
    draftEditor.value = content === null || content === undefined ? null : editableDraft(content)
    draftSnapshot.value = draftEditor.value === null ? '' : JSON.stringify(draftEditor.value)
    Object.keys(actionStatusEdits).forEach((key) => delete actionStatusEdits[Number(key)])
    result.postMeeting.actionItems.forEach((item) => { actionStatusEdits[item.id] = item.status })
  }

  function editableDraft(content: PostMeetingDraftContent): EditablePostMeetingContent {
    return {
      minutes: { ...content.minutes },
      decisions: content.decisions.map((item) => ({ content: item.content, rationale: item.rationale ?? '' })),
      actionItems: content.actionItems.map((item) => ({
        title: item.title,
        description: item.description ?? '',
        assigneeEmployeeId: item.assigneeEmployeeId ?? 0,
        dueAt: toShanghaiDateTimeLocal(item.dueAt),
      })),
    }
  }

  function addAgendaItem(): void {
    if (agendaForms.value.length >= 30) return
    agendaForms.value.push({ clientId: nextClientId('agenda'), topic: '', ownerEmployeeId: meetingPeople.value[0]?.id ?? 0, plannedMinutes: 10 })
  }

  function addMaterialItem(): void {
    if (materialForms.value.length >= 50) return
    materialForms.value.push({ clientId: nextClientId('material'), title: '', ownerEmployeeId: meetingPeople.value[0]?.id ?? 0, required: true, status: 'MISSING', versionLabel: '', note: '' })
  }

  function moveItem<T>(items: T[], index: number, direction: -1 | 1): void {
    const target = index + direction
    if (target < 0 || target >= items.length) return
    const [item] = items.splice(index, 1)
    if (item !== undefined) items.splice(target, 0, item)
  }

  function validatePreparation(): string | null {
    if (agendaForms.value.some((item) => item.topic.trim().length === 0 || item.ownerEmployeeId <= 0 || !Number.isInteger(item.plannedMinutes) || item.plannedMinutes < 5 || item.plannedMinutes > 240)) {
      return '请完整填写每个议题的名称、负责人，以及 5–240 分钟的整数时长。'
    }
    if (materialForms.value.some((item) => item.title.trim().length === 0 || item.ownerEmployeeId <= 0)) {
      return '请完整填写每份材料的名称和负责人。'
    }
    if (agendaTotalMinutes.value > meetingDurationMinutes.value) return '议程总时长不能超过会议时长。'
    return null
  }

  async function savePreparation(): Promise<void> {
    const current = lifecycle.value
    if (!current || preparationSaving.value) return
    clearOperationFeedback()
    const validation = validatePreparation()
    if (validation !== null) { operationError.value = validation; return }
    preparationSaving.value = true
    try {
      const updated = await saveMeetingPreparation(current.meeting.id, {
        expectedVersion: current.preparation.version,
        agendaItems: agendaForms.value.map((item) => ({ topic: item.topic.trim(), ownerEmployeeId: item.ownerEmployeeId, plannedMinutes: item.plannedMinutes })),
        materials: materialForms.value.map((item) => ({
          title: item.title.trim(),
          ownerEmployeeId: item.ownerEmployeeId,
          required: item.required,
          status: item.status,
          versionLabel: emptyToNull(item.versionLabel),
          note: emptyToNull(item.note),
        })),
      })
      applyLifecycle(updated)
      operationNotice.value = '会前准备已保存，动态清单已按最新事实重新计算。'
    } catch (error) {
      await handleMutationError(error, '会前准备保存失败。')
    } finally {
      preparationSaving.value = false
    }
  }

  async function submitTranscript(): Promise<void> {
    const current = lifecycle.value
    const normalizedTranscript = transcript.value.trim()
    if (!current || draftSubmitting.value || normalizedTranscript.length === 0) return
    clearOperationFeedback()
    if (draftIdempotencyKey === null || draftIdempotencyTranscript !== normalizedTranscript) {
      draftIdempotencyKey = createClientRequestId()
      draftIdempotencyTranscript = normalizedTranscript
    }
    draftSubmitting.value = true
    try {
      const updated = await createPostMeetingDraft(current.meeting.id, normalizedTranscript, draftIdempotencyKey)
      draftIdempotencyKey = null
      draftIdempotencyTranscript = ''
      transcript.value = ''
      applyLifecycle(updated)
      if (draft.value?.status === 'FAILED') {
        operationError.value = `草案生成失败${draft.value.errorCode ? `（${draft.value.errorCode}）` : ''}，正式记录未发生变化。`
      } else if (draft.value?.status === 'PROCESSING') {
        operationNotice.value = '草案请求已受理，Agent 仍在处理，请稍后刷新当前会议。'
      } else {
        operationNotice.value = '会后草案已生成，请核对内容后执行接受、编辑或拒绝。'
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        draftIdempotencyKey = null
        draftIdempotencyTranscript = ''
      }
      await handleMutationError(error, '会后草案生成失败。')
    } finally {
      draftSubmitting.value = false
    }
  }

  function addDraftDecision(): void {
    if (draftEditor.value !== null && draftEditor.value.decisions.length < 20) draftEditor.value.decisions.push({ content: '', rationale: '' })
  }

  function addDraftAction(): void {
    if (draftEditor.value !== null && draftEditor.value.actionItems.length < 50) draftEditor.value.actionItems.push({ title: '', description: '', assigneeEmployeeId: 0, dueAt: '' })
  }

  function validateDraftEditor(): string | null {
    const editor = draftEditor.value
    const meeting = lifecycle.value?.meeting
    if (!editor || !meeting) return '当前草案不可编辑。'
    if (!editor.minutes.background.trim() || !editor.minutes.discussionSummary.trim() || !editor.minutes.conclusion.trim()) return '纪要的背景、讨论摘要和结论不能为空。'
    if (editor.decisions.some((item) => item.content.trim().length === 0)) return '决策内容不能为空。'
    for (const item of editor.actionItems) {
      if (!item.title.trim() || item.assigneeEmployeeId <= 0 || !item.dueAt) return '每个行动项都必须填写任务、负责人和截止时间。'
      if (new Date(toShanghaiOffset(item.dueAt)).getTime() <= new Date(meeting.endAt).getTime()) return '行动项截止时间必须晚于会议结束时间。'
    }
    return null
  }

  function draftMutationContent(): PostMeetingDraftContent | null {
    const editor = draftEditor.value
    if (!editor) return null
    return {
      minutes: {
        background: editor.minutes.background.trim(),
        discussionSummary: editor.minutes.discussionSummary.trim(),
        conclusion: editor.minutes.conclusion.trim(),
      },
      decisions: editor.decisions.map((item) => ({ content: item.content.trim(), rationale: item.rationale.trim() })),
      actionItems: editor.actionItems.map((item) => ({
        title: item.title.trim(),
        description: item.description.trim(),
        assigneeEmployeeId: item.assigneeEmployeeId,
        dueAt: toShanghaiOffset(item.dueAt),
      })),
    }
  }

  async function reviewDraft(action: 'ACCEPT' | 'EDIT' | 'REJECT'): Promise<void> {
    const current = lifecycle.value
    const currentDraft = draft.value
    if (!current || !currentDraft || draftReviewing.value) return
    clearOperationFeedback()
    if (action !== 'REJECT') {
      const validation = validateDraftEditor()
      if (validation !== null) { operationError.value = validation; return }
    }
    if (action === 'ACCEPT' && draftDirty.value) {
      operationError.value = '当前有未保存编辑。请先保存编辑并取得新版本，再接受草案。'
      return
    }
    draftReviewing.value = true
    try {
      const editedDraft = action === 'EDIT' ? draftMutationContent() : null
      const updated = await reviewPostMeetingDraft(current.meeting.id, currentDraft.id, {
        action,
        expectedVersion: currentDraft.version,
        ...(editedDraft === null ? {} : { editedDraft }),
      })
      applyLifecycle(updated)
      operationNotice.value = action === 'EDIT'
        ? '编辑已保存为新草案版本；正式记录尚未写入，请再次确认后接受。'
        : action === 'ACCEPT'
          ? '草案已接受，正式纪要、决策和行动项已写入。'
          : '草案已拒绝，正式业务记录未发生变化。'
    } catch (error) {
      await handleMutationError(error, '草案审核失败。')
    } finally {
      draftReviewing.value = false
    }
  }

  function canUpdateActionItem(item: MeetingActionItem): boolean {
    const user = authStore.state.user
    const meeting = lifecycle.value?.meeting
    return user?.roles.includes('ADMIN') === true || user?.id === item.assigneeEmployeeId || user?.id === meeting?.organizerId
  }

  function availableActionStatuses(status: MeetingActionItemStatus): MeetingActionItemStatus[] {
    if (status === 'OPEN') return ['OPEN', 'IN_PROGRESS', 'DONE']
    if (status === 'IN_PROGRESS') return ['IN_PROGRESS', 'OPEN', 'DONE']
    return ['DONE']
  }

  function setActionStatus(actionItemId: number, event: Event): void {
    actionStatusEdits[actionItemId] = (event.target as HTMLSelectElement).value as MeetingActionItemStatus
  }

  async function saveActionStatus(item: MeetingActionItem): Promise<void> {
    const current = lifecycle.value
    const status = actionStatusEdits[item.id] ?? item.status
    if (!current || actionUpdatingId.value !== null || status === item.status) return
    clearOperationFeedback()
    actionUpdatingId.value = item.id
    try {
      const updated = await updateMeetingActionItem(current.meeting.id, item.id, { status, expectedVersion: item.version })
      const index = current.postMeeting.actionItems.findIndex((candidate) => candidate.id === item.id)
      if (index >= 0) current.postMeeting.actionItems[index] = updated
      actionStatusEdits[item.id] = updated.status
      operationNotice.value = `行动项“${updated.title}”已更新为${actionStatusLabel(updated.status)}。`
    } catch (error) {
      await handleMutationError(error, '行动项状态更新失败。')
    } finally {
      actionUpdatingId.value = null
    }
  }

  async function handleMutationError(error: unknown, fallback: string): Promise<void> {
    if (error instanceof ApiError && error.status === 409) {
      await loadLifecycle(false)
      operationError.value = '内容已被其他操作更新，页面已刷新到最新版本，请核对后重试。'
      return
    }
    operationError.value = userMessage(error, fallback)
  }

  function userMessage(error: unknown, fallback: string): string {
    return error instanceof ApiError ? error.message : fallback
  }

  function emptyToNull(value: string): string | null {
    const normalized = value.trim()
    return normalized.length === 0 ? null : normalized
  }

  function clearOperationFeedback(): void {
    operationError.value = ''
    operationNotice.value = ''
  }

  watch(transcript, (value) => {
    if (value.trim() !== draftIdempotencyTranscript) draftIdempotencyKey = null
  })

  watch(() => route.query.meetingId, (value, previous) => {
    if (value === previous || meetingListLoading.value) return
    const meetingId = parseMeetingId(value)
    if (meetingId !== null && meetingId !== selectedMeetingId.value) {
      selectedMeetingId.value = meetingId
      void loadLifecycle()
    }
  })

  onMounted(() => { void loadMeetingChoices() })

  return {
    actionStatusEdits,
    actionStatusLabel,
    actionUpdatingId,
    addAgendaItem,
    addDraftAction,
    addDraftDecision,
    addMaterialItem,
    agendaForms,
    agendaTotalMinutes,
    availableActionStatuses,
    canEditPreparation,
    canUpdateActionItem,
    checklistLabel,
    draft,
    draftDirty,
    draftEditor,
    draftReviewing,
    draftSubmitting,
    employeeName,
    formatDateTime,
    handleMeetingSelection,
    hasFormalPostMeetingRecord,
    isFutureMeeting,
    lifecycle,
    lifecycleError,
    lifecycleLoading,
    loadLifecycle,
    loadMeetingChoices,
    materialForms,
    meetingDurationMinutes,
    meetingListError,
    meetingListLoading,
    meetingOptionLabel,
    meetingPeople,
    meetings,
    moveItem,
    operationError,
    operationNotice,
    preparationSaving,
    reviewDraft,
    saveActionStatus,
    savePreparation,
    selectedMeetingId,
    setActionStatus,
    shouldShowTranscriptForm,
    submitTranscript,
    transcript,
  }
}
