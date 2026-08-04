<template>
  <AppShell title="智能编排" description="用自然语言发起会议任务，在执行前审阅 Agent 的结构化计划。" eyebrow="工作台 / 智能编排">
    <template #actions><button class="ui-button ui-button--outline" type="button" :disabled="streaming" @click="resetConversation">＋ 新建会话</button></template>
    <div class="mobile-workspace-tabs" role="tablist" aria-label="智能编排区域"><button type="button" :class="{ active: mobilePane === 'conversation' }" @click="mobilePane='conversation'">对话</button><button type="button" :class="{ active: mobilePane === 'result' }" @click="mobilePane='result'">编排结果</button></div>
    <div class="orchestration-grid" :class="`orchestration-grid--${mobilePane}`">
      <section class="conversation-pane" aria-labelledby="conversation-title">
        <header class="pane-header"><div><h2 id="conversation-title">协作会话</h2><p>Supervisor 协调三个专业 Agent 与确定性工具</p></div><span class="agent-online"><span />Agent 就绪</span></header>
        <div class="conversation-scroll" aria-live="polite">
          <template v-for="turn in conversationHistory" :key="turn.id">
            <div class="message-row message-row--user"><div class="message-bubble"><span>你</span><p>{{ turn.question }}</p></div></div>
            <div class="message-row message-row--agent"><div class="message-avatar">M</div><div class="message-bubble"><span>MeetOps Agent</span><p>{{ turn.answer }}</p><div v-if="turn.runId" class="message-meta"><StatusBadge :status="turn.status || 'SUCCEEDED'" /><RouterLink class="text-button" :to="{ name: 'agent-run', params: { runId: turn.runId } }">查看这次运行</RouterLink></div></div></div>
          </template>
          <div v-if="submittedMessage" class="message-row message-row--user"><div class="message-bubble"><span>你</span><p>{{ submittedMessage }}</p></div></div>
          <div v-if="runId || answerSummary || streaming" class="message-row message-row--agent"><div class="message-avatar">M</div><div class="message-bubble"><span>MeetOps Agent</span><p v-if="answerSummary">{{ answerSummary }}</p><p v-else-if="streaming">正在解析需求、检查政策并查询资源…</p><p v-else>已保存当前 Run，可继续查看结构化编排结果。</p><div v-if="bookingRequest" class="message-meta"><StatusBadge :status="bookingRequest.status" /><span>请求号 {{ bookingRequest.requestNo }}</span></div></div></div>
          <div v-if="!submittedMessage && !runId" class="welcome-message"><div class="message-avatar">M</div><div><h3>你好，我是 MeetOps</h3><p>告诉我参会人、时间和资源要求。我会给出经过硬约束验证的候选，并在执行前请你确认。</p><div class="prompt-chips"><button v-for="example in examples" :key="example" type="button" @click="message=example">{{ example }}</button></div></div></div>
          <LoadingState v-if="streaming" title="正在协同处理" description="Agent 步骤会实时写入安全运行轨迹。" />
          <ErrorState v-if="errorMessage" :message="errorMessage" />
        </div>
        <div class="composer-area"><RunStatusBar :run-id="runId" :status="runStatus" :loading="recoveryLoading" @refresh="runId && loadRecovery(runId)" @trace="traceOpen=true" /><AgentComposer v-model="message" :disabled="streaming || decisionBusy" :streaming="streaming" @submit="startRun" /></div>
      </section>
      <section class="result-pane" aria-labelledby="result-title">
        <header class="pane-header"><div><h2 id="result-title">编排结果</h2><p>业务结果优先，运行细节可按需查看</p></div><button v-if="runId" class="text-button" type="button" @click="traceOpen=true">查看运行过程</button></header>
        <div class="result-tabs" role="tablist"><button v-for="tab in resultTabs" :key="tab.id" type="button" role="tab" :aria-selected="resultTab===tab.id" :class="{ active: resultTab===tab.id }" @click="resultTab=tab.id">{{ tab.label }}<span v-if="tab.id==='candidates' && candidates.length">{{ candidates.length }}</span></button></div>
        <div class="result-scroll">
          <RequirementSummary v-if="resultTab==='requirements'" :action-type="actionType" :draft="hitlDraft" />
          <CandidateComparison v-else-if="resultTab==='candidates'" :candidates="candidates" :draft="editableDraft" @select="selectCandidate" />
          <ResourceTimeline v-else-if="resultTab==='resources'" :slots="[]" />
          <div v-else class="citations-panel"><article v-for="citation in citations" :key="citation.chunkId"><span>政策依据</span><h3>{{ citation.title }}</h3><p>{{ citation.headingPath.join(' / ') }}<template v-if="citation.page"> · 第 {{ citation.page }} 页</template></p><code>{{ citation.chunkId }}</code></article><EmptyState v-if="citations.length===0" title="暂无政策依据" description="仅当 Agent 返回可验证引用时展示，不会根据请求文本推测政策。" icon="§" /></div>
          <div v-if="runStatus==='WAITING_BUSINESS_RESULT'" class="pending-callout"><StatusBadge status="WAITING_BUSINESS_RESULT" /><div><strong>热门预约正在异步裁决</strong><p>业务服务将通过 RocketMQ 返回最终结果；冲突后 Agent 会恢复并重新规划。</p></div></div>
          <AgentLoopTimeline :events="loopEvents" :run="runMetrics" />
        </div>
      </section>
    </div>
    <HitlReviewBar v-if="hitlDraft && actionType && confirmationToken" :action-type="actionType" :draft="hitlDraft" :expires-at="expiresAt" :busy="decisionBusy || streaming" :feedback="hitlFeedback" @update:feedback="hitlFeedback=$event" @accept="resumeRun('ACCEPT')" @reject="resumeRun('REJECT')" @edit="(changes) => resumeRun('EDIT', changes)" />
    <TraceDrawer v-model:open="traceOpen" :run-id="runId" :steps="steps" :tools="tools" :loops="loopEvents" :run="runMetrics" />
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest, apiSseRequest, type SseMessage } from '../api/client'
import { proposedDraft, readHitlDraft, readLoopEvent } from '../api/agent-view'
import type {
  AgentCandidate,
  AgentCitation,
  AgentHitlDraft,
  AgentLoopEvent,
  AgentOperationType,
  AgentResumeAction,
  AgentRunRecovery,
  AgentRunSummary,
  AgentStepEvent,
  AgentTraceStep,
  AgentTraceToolCall,
  AgentToolEvent,
  BookingRequest,
} from '../api/types'
import AgentComposer from '../components/AgentComposer.vue'
import AgentLoopTimeline from '../components/AgentLoopTimeline.vue'
import AppShell from '../components/AppShell.vue'
import CandidateComparison from '../components/CandidateComparison.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import HitlReviewBar from '../components/HitlReviewBar.vue'
import LoadingState from '../components/LoadingState.vue'
import RequirementSummary from '../components/RequirementSummary.vue'
import ResourceTimeline from '../components/ResourceTimeline.vue'
import RunStatusBar from '../components/RunStatusBar.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TraceDrawer from '../components/TraceDrawer.vue'
import { createClientRequestId } from '../utils/format'

const route = useRoute()
const router = useRouter()

const message = ref('下周三下午帮张三安排一个 90 分钟架构评审，要大屏')
const threadId = ref<string | null>(null)
const runId = ref<string | null>(null)
const runStatus = ref('')
const steps = ref<AgentStepEvent[]>([])
const tools = ref<AgentToolEvent[]>([])
const loopEvents = ref<AgentLoopEvent[]>([])
const candidates = ref<AgentCandidate[]>([])
const actionType = ref<AgentOperationType | null>(null)
const hitlDraft = ref<AgentHitlDraft | null>(null)
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
const traceOpen = ref(false)
const mobilePane = ref<'conversation' | 'result'>('conversation')
const resultTab = ref<'requirements' | 'candidates' | 'resources' | 'citations'>('requirements')
const submittedMessage = ref('')
const runMetrics = ref<Partial<AgentRunSummary> | null>(null)
const editableDraft = computed(() => proposedDraft(hitlDraft.value))
const examples = ['下周三下午安排 90 分钟架构评审，要大屏', '找一个 10 人、有视频设备的会议室', '客户会议能不能使用 VIP 会议室？']
const resultTabs = [
  { id: 'requirements' as const, label: '需求解析' }, { id: 'candidates' as const, label: '候选计划' },
  { id: 'resources' as const, label: '资源日历' }, { id: 'citations' as const, label: '政策依据' },
]

interface ConversationTurn {
  id: string
  runId: string | null
  question: string
  answer: string
  status: string
}

interface StoredConversation {
  history: ConversationTurn[]
  current: ConversationTurn | null
}

interface StoredRunContext {
  threadId: string
  question: string
}

const CHAT_HISTORY_STORAGE_KEY = 'meetops.chat-history.v1'
const CHAT_ACTIVE_RUN_STORAGE_KEY = 'meetops.chat-active-run.v1'
const CHAT_ACTIVE_THREAD_STORAGE_KEY = 'meetops.chat-active-thread.v1'
const CHAT_RUN_CONTEXT_STORAGE_KEY = 'meetops.chat-run-context.v1'
const SAFE_RUN_ID = /^[A-Za-z0-9_-]{1,64}$/
const conversationHistory = ref<ConversationTurn[]>([])

let activeAbort: AbortController | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let recoveryTimer: ReturnType<typeof setTimeout> | null = null
let recoveryRetryAttempts = 0
let recoveryEpoch = 0
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
      if (eventRunId !== undefined) {
        persistRunContext(eventRunId)
      }
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
    case 'agent.loop': {
      const loopEvent = readLoopEvent(payload)
      if (loopEvent !== null) {
        loopEvents.value = [
          ...loopEvents.value.filter((event) => !sameLoopEvent(event, loopEvent)),
          loopEvent,
        ]
      }
      return
    }
    case 'plan.candidates':
      candidates.value = readCandidates(payload.candidates)
      return
    case 'hitl.required': {
      const token = stringValue(payload, 'confirmationToken')
      const nextDraft = readHitlDraft(payload.draft, payload.actionType ?? payload.operationType)
      if (token !== undefined && nextDraft !== null) {
        confirmationToken.value = token
        actionType.value = nextDraft.actionType
        hitlDraft.value = nextDraft.draft
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
        actionType.value = null
        hitlDraft.value = null
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
      actionType.value = null
      hitlDraft.value = null
      bookingRequest.value = null
      answerSummary.value = '预约已确认并写入会议列表。'
      return
    case 'run.completed':
      runStatus.value = stringValue(payload, 'status') ?? 'SUCCEEDED'
      answerSummary.value = stringValue(payload, 'answerSummary') ?? '已完成调度。'
      citations.value = readCitations(payload.citations)
      confirmationToken.value = null
      candidates.value = []
      actionType.value = null
      hitlDraft.value = null
      return
    case 'run.failed':
      runStatus.value = stringValue(payload, 'status') ?? 'FAILED'
      errorMessage.value = stringValue(payload, 'message') ?? '调度未能完成，请稍后重试。'
      confirmationToken.value = null
      candidates.value = []
      actionType.value = null
      hitlDraft.value = null
  }
}

function sameLoopEvent(left: AgentLoopEvent, right: AgentLoopEvent): boolean {
  if (left.createdAt !== null && left.createdAt !== undefined && right.createdAt !== null && right.createdAt !== undefined) {
    return left.createdAt === right.createdAt
  }
  return left.runId === right.runId && left.phase === right.phase && left.iteration === right.iteration && left.replanCount === right.replanCount
}

async function consumeStream(path: `/${string}`, body: unknown): Promise<void> {
  activeAbort?.abort()
  const controller = new AbortController()
  activeAbort = controller
  streaming.value = true
  errorMessage.value = ''

  try {
    await apiSseRequest(
      path,
      body,
      handleSseMessage,
      controller.signal,
      ({ runId: openedRunId }) => {
        if (openedRunId !== null && SAFE_RUN_ID.test(openedRunId)) {
          window.sessionStorage.setItem(CHAT_ACTIVE_RUN_STORAGE_KEY, openedRunId)
          persistRunContext(openedRunId)
          runId.value = openedRunId
        }
      },
    )
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
  archiveCurrentTurn()
  clearRunState()
  threadId.value ??= `thread_${crypto.randomUUID().replaceAll('-', '')}`
  submittedMessage.value = message.value
  mobilePane.value = 'result'
  await consumeStream('/agent/runs/stream', {
    threadId: threadId.value,
    message: message.value,
    clientRequestId: createClientRequestId(),
  })
}

function currentConversationTurn(): ConversationTurn | null {
  if (submittedMessage.value.length === 0) {
    return null
  }
  const answer = answerSummary.value || errorMessage.value || (streaming.value ? '正在处理…' : '已保存当前 Run，可继续查看结构化编排结果。')
  return {
    id: runId.value ?? `pending-${submittedMessage.value}`,
    runId: runId.value,
    question: submittedMessage.value,
    answer,
    status: runStatus.value,
  }
}

function archiveCurrentTurn(): void {
  const current = currentConversationTurn()
  if (current === null) {
    return
  }
  conversationHistory.value = [
    ...conversationHistory.value.filter((turn) => turn.id !== current.id),
    current,
  ]
}

function readStoredConversations(): Record<string, StoredConversation> {
  try {
    const raw = window.sessionStorage.getItem(CHAT_HISTORY_STORAGE_KEY)
    if (raw === null) {
      return {}
    }
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null
      ? parsed as Record<string, StoredConversation>
      : {}
  } catch {
    return {}
  }
}

function persistConversation(): void {
  if (threadId.value === null) {
    return
  }
  const conversations = readStoredConversations()
  conversations[threadId.value] = {
    history: conversationHistory.value,
    current: currentConversationTurn(),
  }
  window.sessionStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(conversations))
}

function restoreConversation(thread: string, currentRunId: string): void {
  const stored = readStoredConversations()[thread]
  if (stored === undefined || !Array.isArray(stored.history)) {
    return
  }
  conversationHistory.value = stored.history.filter((turn) => turn.runId !== currentRunId)
  if (stored.current?.runId === currentRunId) {
    submittedMessage.value = stored.current.question
    runStatus.value = stored.current.status || 'RUNNING'
  }
}

function readStoredRunContexts(): Record<string, StoredRunContext> {
  try {
    const raw = window.sessionStorage.getItem(CHAT_RUN_CONTEXT_STORAGE_KEY)
    if (raw === null) {
      return {}
    }
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null
      ? parsed as Record<string, StoredRunContext>
      : {}
  } catch {
    return {}
  }
}

function persistRunContext(id: string): void {
  if (threadId.value === null || submittedMessage.value.length === 0) {
    return
  }
  const contexts = readStoredRunContexts()
  contexts[id] = { threadId: threadId.value, question: submittedMessage.value }
  window.sessionStorage.setItem(CHAT_RUN_CONTEXT_STORAGE_KEY, JSON.stringify(contexts))
}

function restoreRunContext(id: string): void {
  const context = readStoredRunContexts()[id]
  if (
    context === undefined
    || !SAFE_RUN_ID.test(context.threadId)
    || typeof context.question !== 'string'
    || context.question.length === 0
  ) {
    return
  }
  threadId.value = context.threadId
  submittedMessage.value = context.question
  restoreConversation(context.threadId, id)
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
    actionType.value = null
    hitlDraft.value = null
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
  recoveryEpoch += 1
  clearBookingPoll()
  clearRecoveryTimer()
  recoveryRetryAttempts = 0
  runId.value = null
  runStatus.value = ''
  steps.value = []
  tools.value = []
  loopEvents.value = []
  candidates.value = []
  actionType.value = null
  hitlDraft.value = null
  confirmationToken.value = null
  expiresAt.value = undefined
  answerSummary.value = ''
  citations.value = []
  hitlFeedback.value = ''
  bookingRequest.value = null
  runMetrics.value = null
}

function resetConversation(): void {
  activeAbort?.abort()
  const activeRunId = runId.value
  if (activeRunId !== null) {
    const contexts = readStoredRunContexts()
    delete contexts[activeRunId]
    window.sessionStorage.setItem(CHAT_RUN_CONTEXT_STORAGE_KEY, JSON.stringify(contexts))
  }
  threadId.value = null
  conversationHistory.value = []
  errorMessage.value = ''
  submittedMessage.value = ''
  clearRunState()
  window.sessionStorage.removeItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
  window.sessionStorage.removeItem(CHAT_ACTIVE_THREAD_STORAGE_KEY)
  const query = { ...route.query }
  delete query.runId
  void router.replace({ query })
}

function applyRecovery(recovery: AgentRunRecovery): void {
  restoreConversation(recovery.threadId, recovery.runId)
  runId.value = recovery.runId
  threadId.value = recovery.threadId
  runStatus.value = recovery.status
  answerSummary.value = recovery.answerSummary ?? ''
  runMetrics.value = recovery
  const nextDraft = recovery.draft === undefined
    ? null
    : readHitlDraft(recovery.draft, recovery.actionType ?? recovery.operationType)
  const nextToken = recovery.confirmationToken ?? null
  const isResumable = recovery.status === 'WAITING_CONFIRMATION' && nextDraft !== null && nextToken !== null
  candidates.value = isResumable ? recovery.candidates ?? [] : []
  actionType.value = isResumable ? nextDraft.actionType : null
  hitlDraft.value = isResumable ? nextDraft.draft : null
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
  const requestedEpoch = recoveryEpoch
  try {
    const [recovery, trace] = await Promise.all([
      apiRequest<AgentRunRecovery>(`/agent/runs/${id}`),
      apiRequest<{ run: AgentRunSummary; steps: AgentTraceStep[]; toolCalls: AgentTraceToolCall[]; loopEvents?: unknown[] }>(`/agent/runs/${id}/trace`),
    ])
    if (requestedEpoch !== recoveryEpoch || (runId.value !== null && runId.value !== id)) {
      return
    }
    applyRecovery(recovery)
    recoveryRetryAttempts = 0
    runMetrics.value = trace.run
    steps.value = trace.steps.map((step) => ({ ...step, runId: id }))
    tools.value = trace.toolCalls.map((tool) => ({
      runId: id,
      toolCallId: tool.toolCallId,
      toolName: tool.toolName,
      riskLevel: tool.riskLevel,
      status: tool.status,
      summary: tool.resultSummary,
      durationMs: tool.durationMs,
    }))
    loopEvents.value = (trace.loopEvents ?? []).flatMap((event) => {
      const parsed = readLoopEvent(event)
      return parsed === null ? [] : [parsed]
    })
    if (recovery.status === 'RUNNING') {
      scheduleRecovery(id, 1500)
    } else {
      clearRecoveryTimer()
    }
  } catch (error) {
    if (
      error instanceof ApiError
      && (error.status === 404 || error.status === 503)
      && runId.value === id
      && recoveryRetryAttempts < 10
    ) {
      recoveryRetryAttempts += 1
      scheduleRecovery(id, 1000)
      return
    }
    errorMessage.value = error instanceof ApiError ? error.message : '无法加载当前 Run。'
  } finally {
    recoveryLoading.value = false
  }
}

function scheduleRecovery(id: string, delayMs: number): void {
  clearRecoveryTimer()
  recoveryTimer = setTimeout(() => void loadRecovery(id), delayMs)
}

function clearRecoveryTimer(): void {
  if (recoveryTimer !== null) {
    clearTimeout(recoveryTimer)
    recoveryTimer = null
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

watch(runId, (nextRunId) => {
  const currentRunId = route.query.runId
  if (nextRunId !== null) {
    window.sessionStorage.setItem(CHAT_ACTIVE_RUN_STORAGE_KEY, nextRunId)
  }
  if (nextRunId !== null && currentRunId !== nextRunId) {
    void router.replace({ query: { ...route.query, runId: nextRunId } })
  }
  if (nextRunId === null && currentRunId !== undefined) {
    const query = { ...route.query }
    delete query.runId
    void router.replace({ query })
  }
})

watch(threadId, (nextThreadId) => {
  if (nextThreadId !== null) {
    window.sessionStorage.setItem(CHAT_ACTIVE_THREAD_STORAGE_KEY, nextThreadId)
  }
})

watch(
  [threadId, runId, submittedMessage, answerSummary, runStatus, errorMessage, conversationHistory],
  () => persistConversation(),
  { deep: true },
)

onMounted(() => {
  const requestedRunId = route.query.runId
  if (typeof requestedRunId === 'string' && SAFE_RUN_ID.test(requestedRunId)) {
    runId.value = requestedRunId
    restoreRunContext(requestedRunId)
    const activeThreadId = window.sessionStorage.getItem(CHAT_ACTIVE_THREAD_STORAGE_KEY)
    if (threadId.value === null && activeThreadId !== null && SAFE_RUN_ID.test(activeThreadId)) {
      threadId.value = activeThreadId
      restoreConversation(activeThreadId, requestedRunId)
    }
    runStatus.value ||= 'RUNNING'
    void loadRecovery(requestedRunId)
    return
  }
  const activeRunId = window.sessionStorage.getItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
  if (activeRunId !== null && SAFE_RUN_ID.test(activeRunId)) {
    void router.replace({ query: { ...route.query, runId: activeRunId } })
    void loadRecovery(activeRunId)
  }
})

onUnmounted(() => {
  activeAbort?.abort()
  clearBookingPoll()
  clearRecoveryTimer()
})
</script>
