<template>
  <AppShell title="智能调度">
    <div class="chat-layout">
      <section class="content-panel chat-composer" aria-labelledby="chat-title">
        <div class="section-heading compact-heading">
          <div>
            <h2 id="chat-title">描述你的会议需求</h2>
            <p class="muted">将由 Supervisor、专业 Agent 和 Java 业务规则共同处理。</p>
          </div>
          <button class="secondary-button" type="button" :disabled="streaming" @click="resetConversation">
            新建会话
          </button>
        </div>

        <form class="stack compact-stack" @submit.prevent="startRun">
          <label>
            <span>调度需求</span>
            <textarea
              v-model.trim="message"
              rows="5"
              maxlength="4000"
              placeholder="例如：下周三下午帮张三安排一个 90 分钟架构评审，要大屏"
              :disabled="streaming || decisionBusy"
              required
            ></textarea>
          </label>
          <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
          <div class="form-actions">
            <button class="primary-button" type="submit" :disabled="streaming || decisionBusy || message.length === 0">
              {{ streaming ? '正在处理…' : '开始调度' }}
            </button>
            <button
              v-if="runId"
              class="secondary-button"
              type="button"
              :disabled="streaming || recoveryLoading"
              @click="loadRecovery(runId)"
            >
              {{ recoveryLoading ? '加载中…' : '刷新当前 Run' }}
            </button>
          </div>
        </form>

        <div v-if="runId" class="run-summary" aria-live="polite">
          <div>
            <strong>Run：{{ runId }}</strong>
            <span class="badge" :class="statusClass(runStatus)">{{ runStatus || 'RUNNING' }}</span>
          </div>
          <RouterLink :to="{ name: 'agent-run', params: { runId } }">查看安全 Trace</RouterLink>
        </div>

        <div v-if="answerSummary" class="answer-summary">
          <h3>业务结果</h3>
          <p>{{ answerSummary }}</p>
        </div>

        <div v-if="citations.length > 0" class="citation-list">
          <h3>可验证引用</h3>
          <ul>
            <li v-for="citation in citations" :key="citation.chunkId">
              {{ citation.title }} · {{ citation.headingPath.join(' / ') }}
              <span v-if="citation.page">（第 {{ citation.page }} 页）</span>
            </li>
          </ul>
        </div>

        <div v-if="bookingRequest" class="booking-status">
          <strong>热门预约状态：{{ bookingRequest.status }}</strong>
          <span v-if="bookingRequest.requestNo">请求号 {{ bookingRequest.requestNo }}</span>
          <span v-if="bookingRequest.errorMessage">{{ bookingRequest.errorMessage }}</span>
          <RouterLink v-if="bookingRequest.meetingId" :to="{ name: 'meetings' }">前往我的会议</RouterLink>
        </div>
      </section>

      <AgentTimeline :steps="steps" :tools="tools" />
    </div>

    <CandidateCards
      v-if="runStatus === 'WAITING_CONFIRMATION' && draft && confirmationToken"
      :candidates="candidates"
      :draft="draft"
      @select="selectCandidate"
    />

    <HitlDecisionPanel
      v-if="draft && confirmationToken"
      :draft="draft"
      :expires-at="expiresAt"
      :busy="decisionBusy || streaming"
      :feedback="hitlFeedback"
      @update:feedback="hitlFeedback = $event"
      @accept="resumeRun('ACCEPT')"
      @reject="resumeRun('REJECT')"
      @edit="(changes) => resumeRun('EDIT', changes)"
    />

    <section v-if="runStatus === 'WAITING_BUSINESS_RESULT' && !bookingRequest" class="content-panel pending-panel">
      <h2>预约正在异步处理</h2>
      <p class="muted">热门时段由业务服务最终裁决。刷新当前 Run 后可查看恢复状态；冲突后会给出新的候选草案。</p>
    </section>
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest, apiSseRequest, type SseMessage } from '../api/client'
import type {
  AgentCandidate,
  AgentCitation,
  AgentDraft,
  AgentDraftParticipant,
  AgentResumeAction,
  AgentRunRecovery,
  AgentStepEvent,
  AgentToolEvent,
  BookingRequest,
} from '../api/types'
import AgentTimeline from '../components/AgentTimeline.vue'
import AppShell from '../components/AppShell.vue'
import CandidateCards from '../components/CandidateCards.vue'
import HitlDecisionPanel from '../components/HitlDecisionPanel.vue'
import { createClientRequestId } from '../utils/format'

const route = useRoute()
const router = useRouter()

const message = ref('下周三下午帮张三安排一个 90 分钟架构评审，要大屏')
const threadId = ref<string | null>(null)
const runId = ref<string | null>(null)
const runStatus = ref('')
const steps = ref<AgentStepEvent[]>([])
const tools = ref<AgentToolEvent[]>([])
const candidates = ref<AgentCandidate[]>([])
const draft = ref<AgentDraft | null>(null)
const confirmationToken = ref<string | null>(null)
const expiresAt = ref<string | undefined>()
const answerSummary = ref('')
const citations = ref<AgentCitation[]>([])
const hitlFeedback = ref('')
const bookingRequest = ref<BookingRequest | null>(null)
const errorMessage = ref('')
const streaming = ref(false)
const decisionBusy = ref(false)
const recoveryLoading = ref(false)

let activeAbort: AbortController | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollAttempts = 0

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function stringValue(value: Record<string, unknown>, key: string): string | undefined {
  return typeof value[key] === 'string' ? value[key] : undefined
}

function numberValue(value: Record<string, unknown>, key: string): number | undefined {
  return typeof value[key] === 'number' && Number.isFinite(value[key]) ? value[key] : undefined
}

function readParticipants(value: unknown): AgentDraftParticipant[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const participant = record(item)
    const employeeId = participant === null ? undefined : numberValue(participant, 'employeeId')
    const displayName = participant === null ? undefined : stringValue(participant, 'displayName')
    return employeeId !== undefined && displayName !== undefined ? [{ employeeId, displayName }] : []
  })
}

function readDraft(value: unknown): AgentDraft | null {
  const item = record(value)
  if (item === null) {
    return null
  }
  const title = stringValue(item, 'title')
  const roomId = numberValue(item, 'roomId')
  const roomName = stringValue(item, 'roomName')
  const startAt = stringValue(item, 'startAt')
  const endAt = stringValue(item, 'endAt')
  if (title === undefined || roomId === undefined || roomName === undefined || startAt === undefined || endAt === undefined) {
    return null
  }
  return {
    title,
    roomId,
    roomName,
    startAt,
    endAt,
    requiredParticipants: readParticipants(item.requiredParticipants),
    optionalParticipants: readParticipants(item.optionalParticipants),
    createVideoConference: item.createVideoConference === true,
  }
}

function readCandidates(value: unknown): AgentCandidate[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const candidate = record(item)
    const costs = candidate === null ? null : record(candidate.costBreakdown)
    if (candidate === null || costs === null) {
      return []
    }
    const candidateId = stringValue(candidate, 'candidateId')
    const roomId = numberValue(candidate, 'roomId')
    const roomName = stringValue(candidate, 'roomName')
    const building = stringValue(candidate, 'building')
    const startAt = stringValue(candidate, 'startAt')
    const endAt = stringValue(candidate, 'endAt')
    const totalCost = numberValue(candidate, 'totalCost')
    const optionalParticipantConflict = numberValue(costs, 'optionalParticipantConflict')
    const preferredTimeDeviation = numberValue(costs, 'preferredTimeDeviation')
    const buildingDistance = numberValue(costs, 'buildingDistance')
    const capacityWaste = numberValue(costs, 'capacityWaste')
    const preferenceViolation = numberValue(costs, 'preferenceViolation')
    const roomChange = numberValue(costs, 'roomChange')
    if (
      candidateId === undefined ||
      roomId === undefined ||
      roomName === undefined ||
      building === undefined ||
      startAt === undefined ||
      endAt === undefined ||
      totalCost === undefined ||
      optionalParticipantConflict === undefined ||
      preferredTimeDeviation === undefined ||
      buildingDistance === undefined ||
      capacityWaste === undefined ||
      preferenceViolation === undefined ||
      roomChange === undefined
    ) {
      return []
    }
    return [
      {
        candidateId,
        roomId,
        roomName,
        building,
        startAt,
        endAt,
        totalCost,
        costBreakdown: {
          optionalParticipantConflict,
          preferredTimeDeviation,
          buildingDistance,
          capacityWaste,
          preferenceViolation,
          roomChange,
        },
      },
    ]
  })
}

function readCitations(value: unknown): AgentCitation[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const citation = record(item)
    if (citation === null) {
      return []
    }
    const chunkId = stringValue(citation, 'chunkId')
    const title = stringValue(citation, 'title')
    const headingPath = Array.isArray(citation.headingPath)
      ? citation.headingPath.filter((heading): heading is string => typeof heading === 'string')
      : []
    const page = citation.page === null || typeof citation.page === 'number' ? citation.page : null
    return chunkId !== undefined && title !== undefined && headingPath.length > 0
      ? [{ chunkId, title, headingPath, page }]
      : []
  })
}

function handleSseMessage(messageEvent: SseMessage): void {
  const payload = record(messageEvent.data)
  if (payload === null) {
    return
  }
  const eventRunId = stringValue(payload, 'runId')
  if (eventRunId !== undefined) {
    runId.value = eventRunId
  }

  switch (messageEvent.event) {
    case 'run.started':
      threadId.value = stringValue(payload, 'threadId') ?? threadId.value
      runStatus.value = stringValue(payload, 'status') ?? 'RUNNING'
      return
    case 'run.resumed':
      runStatus.value = stringValue(payload, 'status') ?? 'RUNNING'
      return
    case 'agent.step': {
      const stepId = stringValue(payload, 'stepId')
      const sequenceNo = numberValue(payload, 'sequenceNo')
      const agentName = stringValue(payload, 'agentName')
      const nodeName = stringValue(payload, 'nodeName')
      const status = stringValue(payload, 'status')
      const summary = stringValue(payload, 'summary')
      const durationMs = numberValue(payload, 'durationMs')
      if (
        eventRunId !== undefined &&
        stepId !== undefined &&
        sequenceNo !== undefined &&
        agentName !== undefined &&
        nodeName !== undefined &&
        status !== undefined &&
        summary !== undefined &&
        durationMs !== undefined
      ) {
        steps.value = [
          ...steps.value.filter((step) => step.stepId !== stepId),
          { runId: eventRunId, stepId, sequenceNo, agentName, nodeName, status, summary, durationMs },
        ]
      }
      return
    }
    case 'tool.call': {
      const toolCallId = stringValue(payload, 'toolCallId')
      const toolName = stringValue(payload, 'toolName')
      const riskLevel = stringValue(payload, 'riskLevel')
      const status = stringValue(payload, 'status')
      const summary = stringValue(payload, 'summary')
      const durationMs = numberValue(payload, 'durationMs')
      if (
        eventRunId !== undefined &&
        toolCallId !== undefined &&
        toolName !== undefined &&
        riskLevel !== undefined &&
        status !== undefined &&
        summary !== undefined &&
        durationMs !== undefined
      ) {
        tools.value = [
          ...tools.value.filter((tool) => tool.toolCallId !== toolCallId),
          { runId: eventRunId, toolCallId, toolName, riskLevel, status, summary, durationMs },
        ]
      }
      return
    }
    case 'plan.candidates':
      candidates.value = readCandidates(payload.candidates)
      return
    case 'hitl.required': {
      const token = stringValue(payload, 'confirmationToken')
      const nextDraft = readDraft(payload.draft)
      if (token !== undefined && nextDraft !== null) {
        confirmationToken.value = token
        draft.value = nextDraft
        expiresAt.value = stringValue(payload, 'expiresAt')
        hitlFeedback.value = ''
        runStatus.value = stringValue(payload, 'status') ?? 'WAITING_CONFIRMATION'
      }
      return
    }
    case 'booking.pending': {
      const requestNo = stringValue(payload, 'requestNo')
      if (requestNo !== undefined) {
        confirmationToken.value = null
        candidates.value = []
        draft.value = null
        runStatus.value = stringValue(payload, 'status') ?? 'WAITING_BUSINESS_RESULT'
        bookingRequest.value = {
          requestNo,
          status: 'PENDING',
          meetingId: null,
          errorCode: null,
          errorMessage: null,
          createdAt: '',
          updatedAt: '',
        }
        beginBookingPoll(requestNo)
      }
      return
    }
    case 'booking.completed':
      runStatus.value = stringValue(payload, 'status') ?? 'SUCCESS'
      confirmationToken.value = null
      candidates.value = []
      draft.value = null
      bookingRequest.value = null
      answerSummary.value = '预约已确认并写入会议列表。'
      return
    case 'run.completed':
      runStatus.value = stringValue(payload, 'status') ?? 'SUCCEEDED'
      answerSummary.value = stringValue(payload, 'answerSummary') ?? '已完成调度。'
      citations.value = readCitations(payload.citations)
      confirmationToken.value = null
      candidates.value = []
      draft.value = null
      return
    case 'run.failed':
      runStatus.value = stringValue(payload, 'status') ?? 'FAILED'
      errorMessage.value = stringValue(payload, 'message') ?? '调度未能完成，请稍后重试。'
      confirmationToken.value = null
      candidates.value = []
      draft.value = null
  }
}

async function consumeStream(path: `/${string}`, body: unknown): Promise<void> {
  activeAbort?.abort()
  const controller = new AbortController()
  activeAbort = controller
  streaming.value = true
  errorMessage.value = ''

  try {
    await apiSseRequest(path, body, handleSseMessage, controller.signal)
  } catch (error) {
    if (controller.signal.aborted) {
      return
    }
    errorMessage.value = error instanceof ApiError ? error.message : '调度服务暂时不可用。'
  } finally {
    if (activeAbort === controller) {
      activeAbort = null
      streaming.value = false
    }
  }
}

async function startRun(): Promise<void> {
  if (message.value.length === 0 || streaming.value || decisionBusy.value) {
    return
  }
  clearRunState()
  await consumeStream('/agent/runs/stream', {
    threadId: threadId.value,
    message: message.value,
    clientRequestId: createClientRequestId(),
  })
}

async function resumeRun(
  action: AgentResumeAction,
  editedDraft?: { roomId?: number; startAt?: string },
): Promise<void> {
  if (runId.value === null || confirmationToken.value === null || decisionBusy.value || streaming.value) {
    return
  }
  const token = confirmationToken.value
  const feedback = hitlFeedback.value.trim()
  decisionBusy.value = true
  if (action === 'EDIT') {
    candidates.value = []
    draft.value = null
    confirmationToken.value = null
  }

  try {
    await consumeStream(`/agent/runs/${runId.value}/resume`, {
      action,
      confirmationToken: token,
      ...(editedDraft === undefined ? {} : { editedDraft }),
      ...(feedback.length === 0 ? {} : { feedback }),
    })
  } finally {
    decisionBusy.value = false
  }
}

function selectCandidate(candidate: AgentCandidate): void {
  void resumeRun('EDIT', { roomId: candidate.roomId, startAt: candidate.startAt })
}

function clearRunState(): void {
  clearBookingPoll()
  runId.value = null
  runStatus.value = ''
  steps.value = []
  tools.value = []
  candidates.value = []
  draft.value = null
  confirmationToken.value = null
  expiresAt.value = undefined
  answerSummary.value = ''
  citations.value = []
  hitlFeedback.value = ''
  bookingRequest.value = null
}

function resetConversation(): void {
  activeAbort?.abort()
  threadId.value = null
  errorMessage.value = ''
  clearRunState()
}

function applyRecovery(recovery: AgentRunRecovery): void {
  runId.value = recovery.runId
  threadId.value = recovery.threadId
  runStatus.value = recovery.status
  answerSummary.value = recovery.answerSummary ?? ''
  const nextDraft = recovery.draft ?? null
  const nextToken = recovery.confirmationToken ?? null
  const isResumable = recovery.status === 'WAITING_CONFIRMATION' && nextDraft !== null && nextToken !== null
  candidates.value = isResumable ? recovery.candidates ?? [] : []
  draft.value = isResumable ? nextDraft : null
  confirmationToken.value = isResumable ? nextToken : null
  expiresAt.value = isResumable ? recovery.expiresAt : undefined
  hitlFeedback.value = ''
}

async function loadRecovery(id: string): Promise<void> {
  if (recoveryLoading.value) {
    return
  }
  recoveryLoading.value = true
  errorMessage.value = ''
  try {
    applyRecovery(await apiRequest<AgentRunRecovery>(`/agent/runs/${id}`))
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法加载当前 Run。'
  } finally {
    recoveryLoading.value = false
  }
}

function clearBookingPoll(): void {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  pollAttempts = 0
}

function beginBookingPoll(requestNo: string): void {
  clearBookingPoll()
  void pollBookingRequest(requestNo)
}

async function pollBookingRequest(requestNo: string): Promise<void> {
  try {
    const latest = await apiRequest<BookingRequest>(`/booking-requests/${requestNo}`)
    bookingRequest.value = latest
    if (latest.status === 'PENDING' || latest.status === 'PROCESSING') {
      pollAttempts += 1
      if (pollAttempts < 60) {
        pollTimer = setTimeout(() => void pollBookingRequest(requestNo), 2000)
      }
      return
    }
    if (latest.status === 'SUCCESS') {
      runStatus.value = 'SUCCESS'
      answerSummary.value = '热门预约已确认并写入会议列表。'
      return
    }
    if (latest.status === 'CONFLICT' && runId.value !== null) {
      const conflictedRunId = runId.value
      runStatus.value = 'CONFLICT'
      pollTimer = setTimeout(() => void loadRecovery(conflictedRunId), 1200)
    }
  } catch (error) {
    if (pollAttempts < 5) {
      pollAttempts += 1
      pollTimer = setTimeout(() => void pollBookingRequest(requestNo), 2000)
      return
    }
    errorMessage.value = error instanceof ApiError ? error.message : '热门预约状态查询失败。'
  }
}

function statusClass(status: string): string {
  if (status === 'FAILED' || status === 'CONFLICT' || status === 'CANCELLED') {
    return 'badge-danger'
  }
  if (status.startsWith('WAITING') || status === 'PENDING' || status === 'PROCESSING') {
    return 'badge-warning'
  }
  return 'badge-success'
}

watch(runId, (nextRunId) => {
  const currentRunId = route.query.runId
  if (nextRunId !== null && currentRunId !== nextRunId) {
    void router.replace({ query: { ...route.query, runId: nextRunId } })
  }
  if (nextRunId === null && currentRunId !== undefined) {
    const query = { ...route.query }
    delete query.runId
    void router.replace({ query })
  }
})

onMounted(() => {
  const requestedRunId = route.query.runId
  if (typeof requestedRunId === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(requestedRunId)) {
    void loadRecovery(requestedRunId)
  }
})

onUnmounted(() => {
  activeAbort?.abort()
  clearBookingPoll()
})
</script>
