<template>
  <WorkspaceShell>
    <div class="chat-workspace">
      <header class="chat-workspace__header">
        <div>
          <span class="chat-workspace__eyebrow">工作台 / 智能编排</span>
          <div class="chat-workspace__title-row">
            <h1>智能编排</h1>
            <StatusBadge v-if="runId" :status="runStatus || 'RUNNING'" />
          </div>
          <p>从一句话需求到可验证候选，所有写操作都会等待你的明确确认。</p>
        </div>
        <div class="chat-workspace__actions">
          <button
            v-if="runId"
            class="ui-button ui-button--outline"
            type="button"
            @click="openOrchestration('execution')"
          >
            <PanelRightOpen :size="17" aria-hidden="true" />编排详情
          </button>
          <button class="ui-button ui-button--default" type="button" :disabled="streaming" @click="resetConversation">
            <Plus :size="17" aria-hidden="true" />新建编排
          </button>
        </div>
      </header>

      <ConversationCanvas
        :history="conversationHistory"
        :submitted-message="submittedMessage"
        :answer-summary="answerSummary"
        :run-id="runId"
        :run-status="runStatus"
        :booking-request="bookingRequest"
        :streaming="streaming"
        :recovery-loading="recoveryLoading"
        :error-message="errorMessage"
        @select-example="selectExample"
      />

      <div v-if="!recoveryLoading || runId" class="composer-dock">
        <section
          v-if="(runStatus === 'WAITING_USER_INPUT' || (runStatus === 'FAILED' && requirementBaselineAvailable)) && requirementItems.length > 0"
          class="requirement-progress"
        >
          <header>
            <div>
              <strong>已整理的会议需求</strong>
              <small>{{ runStatus === 'FAILED' ? '上次运行失败，将从这版有效需求创建恢复任务' : '直接补充待确认项，Agent 会在当前任务中继续' }}</small>
            </div>
            <span>第 {{ requirementRevision }} 版</span>
          </header>
          <ul>
            <li v-for="item in requirementItems" :key="item.field">
              <span class="requirement-progress__status" :data-status="item.status">
                {{ requirementStatusLabel(item.status) }}
              </span>
              <span><strong>{{ requirementFieldLabel(item.field) }}</strong><small>{{ item.summary }}</small></span>
            </li>
          </ul>
        </section>
        <button
          v-if="hitlDraft && actionType && confirmationToken"
          class="composer-hitl-notice"
          type="button"
          @click="openOrchestration('requirements')"
        >
          <ShieldCheck :size="18" aria-hidden="true" />
          <span><strong>方案正在等待确认</strong><small>在执行任何业务写入前查看并确认完整草案</small></span>
          <ChevronRight :size="17" aria-hidden="true" />
        </button>
        <RunStatusBar
          :run-id="runId"
          :status="runStatus"
          :loading="recoveryLoading"
          @refresh="runId && loadRecovery(runId)"
          @trace="traceOpen = true"
        />
        <AgentComposer
          v-model="message"
          :disabled="streaming || decisionBusy"
          :streaming="streaming"
          @submit="startRun"
        />
        <p class="composer-disclaimer">MeetOps 只基于已验证的业务事实生成建议；关键安排请在确认前复核。</p>
      </div>
    </div>

    <OrchestrationSheet
      :open="orchestrationOpen"
      :initial-tab="orchestrationTab"
      :run-id="runId"
      :run-status="runStatus"
      :candidates="candidates"
      :citations="citations"
      :action-type="actionType"
      :draft="hitlDraft"
      :confirmation-token="confirmationToken"
      :expires-at="expiresAt"
      :feedback="hitlFeedback"
      :busy="decisionBusy || streaming"
      :steps="steps"
      :tools="tools"
      :loops="loopEvents"
      :run="runMetrics"
      @update:open="setOrchestrationOpen"
      @update:feedback="hitlFeedback = $event"
      @accept="resumeRun('ACCEPT')"
      @reject="resumeRun('REJECT')"
      @edit="resumeRun('EDIT', $event)"
      @select-candidate="selectCandidate"
      @trace="traceOpen = true"
      @refresh="runId && loadRecovery(runId)"
    />
    <TraceDrawer v-model:open="traceOpen" :run-id="runId" :steps="steps" :tools="tools" :loops="loopEvents" :run="runMetrics" />
  </WorkspaceShell>
</template>

<script setup lang="ts">
import { ChevronRight, PanelRightOpen, Plus, ShieldCheck } from '@lucide/vue'
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest, apiSseRequest, type SseMessage } from '../api/client'
import { readHitlDraft, readLoopEvent } from '../api/agent-view'
import type {
  AgentCandidate,
  AgentCitation,
  AgentHitlDraft,
  AgentLoopEvent,
  AgentOperationType,
  AgentRequirementItem,
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
import ConversationCanvas from '../components/ConversationCanvas.vue'
import OrchestrationSheet from '../components/OrchestrationSheet.vue'
import RunStatusBar from '../components/RunStatusBar.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TraceDrawer from '../components/TraceDrawer.vue'
import WorkspaceShell from '../components/WorkspaceShell.vue'
import { createClientRequestId } from '../utils/format'

const route = useRoute()
const router = useRouter()

const message = ref('')
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
const orchestrationOpen = ref(false)
const orchestrationTab = ref<'requirements' | 'candidates' | 'policy' | 'execution'>('requirements')
const submittedMessage = ref('')
const runMetrics = ref<Partial<AgentRunSummary> | null>(null)
const requirementRevision = ref(0)
const requirementItems = ref<AgentRequirementItem[]>([])
const requirementBaselineAvailable = ref(false)

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
  status?: string
  updatedAt?: number
}

const CHAT_HISTORY_STORAGE_KEY = 'meetops.chat-history.v1'
const CHAT_ACTIVE_RUN_STORAGE_KEY = 'meetops.chat-active-run.v1'
const CHAT_ACTIVE_THREAD_STORAGE_KEY = 'meetops.chat-active-thread.v1'
const CHAT_SUPPRESS_RESTORE_STORAGE_KEY = 'meetops.chat-suppress-restore.v1'
const CHAT_RUN_CONTEXT_STORAGE_KEY = 'meetops.chat-run-context.v1'
const CHAT_SHEET_OPENED_STORAGE_KEY = 'meetops.chat-sheet-opened.v1'
const CHAT_SHEET_DISMISSED_STORAGE_KEY = 'meetops.chat-sheet-dismissed.v1'
const CHAT_CONTEXT_EVENT = 'meetops:chat-context-updated'
const NEW_CONVERSATION_EVENT = 'meetops:new-conversation'
const SAFE_RUN_ID = /^[A-Za-z0-9_-]{1,64}$/
const conversationHistory = ref<ConversationTurn[]>([])
const sheetAutoOpenedRuns = readStoredRunSet(CHAT_SHEET_OPENED_STORAGE_KEY)
const sheetDismissedRuns = readStoredRunSet(CHAT_SHEET_DISMISSED_STORAGE_KEY)

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

function readStoredRunSet(key: string): Set<string> {
  try {
    const raw = window.sessionStorage.getItem(key)
    const parsed: unknown = raw === null ? [] : JSON.parse(raw)
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string' && SAFE_RUN_ID.test(id)) : [])
  } catch {
    return new Set()
  }
}

function persistStoredRunSet(key: string, values: Set<string>): void {
  window.sessionStorage.setItem(key, JSON.stringify([...values].slice(-50)))
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

function readRequirementItems(value: unknown): AgentRequirementItem[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((entry) => {
    const item = record(entry)
    if (item === null) {
      return []
    }
    const field = stringValue(item, 'field')
    const status = stringValue(item, 'status')
    const summary = stringValue(item, 'summary')
    if (field === undefined || status === undefined || summary === undefined) {
      return []
    }
    return [{
      field,
      status,
      summary,
      source: typeof item.source === 'string' || item.source === null ? item.source : undefined,
      ruleId: typeof item.ruleId === 'string' || item.ruleId === null ? item.ruleId : undefined,
      blocking: typeof item.blocking === 'boolean' ? item.blocking : undefined,
    }]
  })
}

function requirementFieldLabel(field: string): string {
  return {
    timeWindow: '时间范围',
    durationMinutes: '会议时长',
    requiredParticipants: '参会人员',
    optionalRequirements: '其他要求',
  }[field] ?? field
}

function requirementStatusLabel(status: string): string {
  return {
    EXPLICIT: '已明确',
    DEFAULTED: '已默认',
    DIRECTORY_RESOLVED: '组织库补全',
    MISSING: '待补充',
    AMBIGUOUS: '待确认',
    CONFLICT: '有冲突',
    UNSPECIFIED: '未说明',
    CLOSED: '已结束',
  }[status] ?? status
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
    case 'requirement.updated':
      requirementRevision.value = numberValue(payload, 'revision') ?? requirementRevision.value
      requirementItems.value = readRequirementItems(payload.items)
      requirementBaselineAvailable.value = false
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
      autoOpenOrchestration('candidates')
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
        autoOpenOrchestration('requirements')
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
      requirementBaselineAvailable.value = requirementRevision.value > 0 && requirementItems.value.length > 0
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
  let reachedExpectedBoundary = false

  try {
    await apiSseRequest(
      path,
      body,
      (messageEvent) => {
        if (['run.completed', 'run.failed', 'booking.completed'].includes(messageEvent.event)) {
          reachedExpectedBoundary = true
        }
        handleSseMessage(messageEvent)
      },
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
    archiveCurrentTurn()
  } finally {
    if (activeAbort === controller) {
      activeAbort = null
      streaming.value = false
    }
    if (reachedExpectedBoundary) {
      archiveCurrentTurn()
      submittedMessage.value = ''
    }
  }
}

async function startRun(): Promise<void> {
  const submitted = message.value.trim()
  if (submitted.length === 0 || streaming.value || decisionBusy.value) {
    return
  }
  const continuingRequirement = runStatus.value === 'WAITING_USER_INPUT'
    && runId.value !== null
    && requirementRevision.value > 0
  const continuingRunId = runId.value
  const expectedRevision = requirementRevision.value
  const failedBaselineRunId = runStatus.value === 'FAILED'
    && runId.value !== null
    && (requirementBaselineAvailable.value || requirementRevision.value > 0)
    ? runId.value
    : null
  archiveCurrentTurn()
  if (!continuingRequirement) {
    clearRunState()
  } else {
    answerSummary.value = ''
    errorMessage.value = ''
  }
  threadId.value ??= `thread_${crypto.randomUUID().replaceAll('-', '')}`
  submittedMessage.value = submitted
  message.value = ''
  if (continuingRequirement && continuingRunId !== null) {
    await consumeStream(`/agent/runs/${continuingRunId}/input`, {
      message: submitted,
      clientRequestId: createClientRequestId(),
      expectedRevision,
    })
    return
  }
  await consumeStream('/agent/runs/stream', {
    threadId: threadId.value,
    message: submitted,
    clientRequestId: createClientRequestId(),
    ...(failedBaselineRunId === null ? {} : { baseRunId: failedBaselineRunId }),
  })
}

function selectExample(prompt: string): void {
  message.value = prompt
}

function openOrchestration(tab: 'requirements' | 'candidates' | 'policy' | 'execution'): void {
  orchestrationTab.value = tab
  orchestrationOpen.value = true
}

function setOrchestrationOpen(value: boolean): void {
  orchestrationOpen.value = value
  if (!value && runId.value !== null) {
    sheetDismissedRuns.add(runId.value)
    persistStoredRunSet(CHAT_SHEET_DISMISSED_STORAGE_KEY, sheetDismissedRuns)
  }
}

function autoOpenOrchestration(tab: 'requirements' | 'candidates'): void {
  const id = runId.value
  if (id === null || sheetAutoOpenedRuns.has(id) || sheetDismissedRuns.has(id)) {
    return
  }
  sheetAutoOpenedRuns.add(id)
  persistStoredRunSet(CHAT_SHEET_OPENED_STORAGE_KEY, sheetAutoOpenedRuns)
  openOrchestration(tab)
}

function currentConversationTurn(): ConversationTurn | null {
  if (submittedMessage.value.length === 0) {
    return null
  }
  const answer = answerSummary.value || errorMessage.value || (streaming.value ? '正在处理…' : '已保存当前 Run，可继续查看结构化编排结果。')
  return {
    id: runId.value === null
      ? `pending-${submittedMessage.value}`
      : `${runId.value}:${requirementRevision.value}:${submittedMessage.value}`,
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
  const currentQuestion = stored.current?.runId === currentRunId
    ? stored.current.question
    : submittedMessage.value
  conversationHistory.value = stored.history.filter((turn) => (
    turn.id !== stored.current?.id
    && !(turn.runId === currentRunId && turn.question === currentQuestion)
  ))
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
  contexts[id] = {
    threadId: threadId.value,
    question: submittedMessage.value,
    status: runStatus.value,
    updatedAt: Date.now(),
  }
  window.sessionStorage.setItem(CHAT_RUN_CONTEXT_STORAGE_KEY, JSON.stringify(contexts))
  window.dispatchEvent(new CustomEvent(CHAT_CONTEXT_EVENT))
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
  if (typeof context.status === 'string' && context.status.length > 0) {
    runStatus.value = context.status
  }
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
  requirementRevision.value = 0
  requirementItems.value = []
  requirementBaselineAvailable.value = false
  orchestrationOpen.value = false
  orchestrationTab.value = 'requirements'
}

function resetConversation(): void {
  activeAbort?.abort()
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
  window.dispatchEvent(new CustomEvent(CHAT_CONTEXT_EVENT))
}

function applyRecovery(recovery: AgentRunRecovery): void {
  restoreConversation(recovery.threadId, recovery.runId)
  runId.value = recovery.runId
  threadId.value = recovery.threadId
  runStatus.value = recovery.status
  answerSummary.value = recovery.answerSummary ?? ''
  runMetrics.value = recovery
  requirementRevision.value = recovery.requirementRevision ?? 0
  requirementItems.value = recovery.requirementItems ?? []
  requirementBaselineAvailable.value = recovery.requirementBaselineAvailable ?? false
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
  if (isResumable) {
    autoOpenOrchestration('requirements')
  }
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
      archiveCurrentTurn()
      submittedMessage.value = ''
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
  if (nextRunId !== null) {
    persistRunContext(nextRunId)
  }
})

watch(threadId, (nextThreadId) => {
  if (nextThreadId !== null) {
    window.sessionStorage.setItem(CHAT_ACTIVE_THREAD_STORAGE_KEY, nextThreadId)
  }
})

watch(
  [threadId, runId, submittedMessage, answerSummary, runStatus, errorMessage, conversationHistory],
  () => {
    persistConversation()
    if (runId.value !== null) {
      persistRunContext(runId.value)
    }
  },
  { deep: true },
)

function handleNewConversation(): void {
  resetConversation()
}

onMounted(() => {
  window.addEventListener(NEW_CONVERSATION_EVENT, handleNewConversation)
  const suppressRestore = window.sessionStorage.getItem(CHAT_SUPPRESS_RESTORE_STORAGE_KEY) === 'true'
  if (suppressRestore) {
    window.sessionStorage.removeItem(CHAT_SUPPRESS_RESTORE_STORAGE_KEY)
    return
  }
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
  window.removeEventListener(NEW_CONVERSATION_EVENT, handleNewConversation)
  activeAbort?.abort()
  clearBookingPoll()
  clearRecoveryTimer()
})
</script>
