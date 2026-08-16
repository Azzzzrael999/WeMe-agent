import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

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
  AgentThreadDetail,
  AgentThreadList,
  AgentTraceStep,
  AgentTraceToolCall,
  AgentToolEvent,
  AgentUnsatAnalysis,
  BookingRequest,
} from '../api/types'
import {
  numberValue,
  readCandidates,
  readCitations,
  readRequirementItems,
  readUnsatAnalysis,
  record,
  requirementFieldLabel,
  requirementStatusLabel,
  stringValue,
} from '../features/chat/parsers'
import { applyChatSseMessage } from '../features/chat/sse-events'
import {
  CHAT_ACTIVE_RUN_STORAGE_KEY,
  CHAT_ACTIVE_THREAD_STORAGE_KEY,
  CHAT_CONTEXT_EVENT,
  CHAT_HISTORY_STORAGE_KEY,
  CHAT_RUN_CONTEXT_STORAGE_KEY,
  CHAT_SHEET_DISMISSED_STORAGE_KEY,
  CHAT_SHEET_OPENED_STORAGE_KEY,
  CHAT_SUPPRESS_RESTORE_STORAGE_KEY,
  NEW_CONVERSATION_EVENT,
  SAFE_RUN_ID,
  persistStoredRunSet,
  readReplanPrefill,
  readStoredConversations,
  readStoredRunContexts,
  readStoredRunSet,
  type ConversationTurn,
  type StoredRunContext,
} from '../features/chat/storage'
import { createClientRequestId } from '../utils/format'

export function useChatWorkflow() {
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
  const unsatAnalysis = ref<AgentUnsatAnalysis | null>(null)
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
  const replanPrefillLoaded = ref(false)
  const expiredRegenerationAttempts = new Set<string>()

  const EXPIRED_DRAFT_REGENERATION_MESSAGE = '原确认草案已过期，请保留已确认的时间窗口、时长、参会人和设备要求，重新读取当前会议室、人员忙闲等事实并生成新方案。'

  const conversationHistory = ref<ConversationTurn[]>([])
  const sheetAutoOpenedRuns = readStoredRunSet(CHAT_SHEET_OPENED_STORAGE_KEY)
  const sheetDismissedRuns = readStoredRunSet(CHAT_SHEET_DISMISSED_STORAGE_KEY)

  let activeAbort: AbortController | null = null
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let recoveryTimer: ReturnType<typeof setTimeout> | null = null
  let recoveryRetryAttempts = 0
  let recoveryEpoch = 0
  let pollAttempts = 0


  function activateReplanPrefill(prompt: string): void {
    activeAbort?.abort()
    archiveCurrentTurn()
    threadId.value = null
    conversationHistory.value = []
    submittedMessage.value = ''
    errorMessage.value = ''
    clearRunState()
    message.value = prompt
    replanPrefillLoaded.value = true
  }



  function handleSseMessage(messageEvent: SseMessage): void {
    applyChatSseMessage(messageEvent, {
      actionType,
      answerSummary,
      autoOpenOrchestration,
      beginBookingPoll,
      bookingRequest,
      candidates,
      citations,
      confirmationToken,
      errorMessage,
      expiresAt,
      hitlDraft,
      hitlFeedback,
      loopEvents,
      openOrchestration,
      persistRunContext,
      requirementBaselineAvailable,
      requirementItems,
      requirementRevision,
      runId,
      runStatus,
      steps,
      threadId,
      tools,
      unsatAnalysis,
    })
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
      unsatAnalysis.value = null
      errorMessage.value = ''
    }
    threadId.value ??= `thread_${crypto.randomUUID().replaceAll('-', '')}`
    submittedMessage.value = submitted
    message.value = ''
    replanPrefillLoaded.value = false
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

  function expiredDraftRegenerationRequested(runIdToCheck: string): boolean {
    return route.query.regenerate === 'expired'
      && route.query.runId === runIdToCheck
  }

  function recoveryDraftExpired(recovery: AgentRunRecovery): boolean {
    if (recovery.status !== 'WAITING_CONFIRMATION' || recovery.expiresAt === undefined) {
      return false
    }
    const expiry = Date.parse(recovery.expiresAt)
    return Number.isFinite(expiry) && expiry <= Date.now()
  }

  async function clearRegenerationQuery(): Promise<void> {
    if (route.query.regenerate === undefined) return
    const query: LocationQueryRaw = { ...route.query }
    delete query.regenerate
    await router.replace({ query })
  }

  async function regenerateExpiredDraft(recovery: AgentRunRecovery): Promise<void> {
    if (expiredRegenerationAttempts.has(recovery.runId)) return
    expiredRegenerationAttempts.add(recovery.runId)
    await clearRegenerationQuery()

    if (!recoveryDraftExpired(recovery) || !recovery.requirementBaselineAvailable) {
      errorMessage.value = recoveryDraftExpired(recovery)
        ? '原草案的需求基线暂时无法恢复。请在下方重新提交会议时间、时长、参会人和设备要求。'
        : '当前草案仍在有效期内，请直接确认；如需调整，请编辑草案后重新校验。'
      if (recoveryDraftExpired(recovery)) {
        message.value = EXPIRED_DRAFT_REGENERATION_MESSAGE
      }
      return
    }

    const baseRunId = recovery.runId
    const recoveryThreadId = recovery.threadId
    archiveCurrentTurn()
    clearRunState()
    threadId.value = recoveryThreadId
    submittedMessage.value = EXPIRED_DRAFT_REGENERATION_MESSAGE
    message.value = ''
    await consumeStream('/agent/runs/stream', {
      threadId: recoveryThreadId,
      message: EXPIRED_DRAFT_REGENERATION_MESSAGE,
      clientRequestId: createClientRequestId(),
      baseRunId,
    })
    if (errorMessage.value.length > 0) {
      message.value = EXPIRED_DRAFT_REGENERATION_MESSAGE
    }
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
    const answer = answerSummary.value || errorMessage.value || (streaming.value ? '正在处理…' : '已保存当前任务，可继续查看编排结果。')
    return {
      id: runId.value === null
        ? `pending-${submittedMessage.value}`
        : `${runId.value}:${requirementRevision.value}:${submittedMessage.value}`,
      runId: runId.value,
      question: submittedMessage.value,
      answer,
      status: runStatus.value,
      unsatAnalysis: unsatAnalysis.value,
      citations: citations.value,
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
    unsatAnalysis.value = null
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
    replanPrefillLoaded.value = false
    clearRunState()
    window.sessionStorage.removeItem(CHAT_ACTIVE_RUN_STORAGE_KEY)
    window.sessionStorage.removeItem(CHAT_ACTIVE_THREAD_STORAGE_KEY)
    const query = { ...route.query }
    delete query.runId
    delete query.prefill
    delete query.sourceCaseId
    delete query.regenerate
    void router.replace({ query })
    window.dispatchEvent(new CustomEvent(CHAT_CONTEXT_EVENT))
  }

  function applyRecovery(recovery: AgentRunRecovery, serverHistoryLoaded = false): void {
    if (!serverHistoryLoaded) {
      restoreConversation(recovery.threadId, recovery.runId)
    }
    runId.value = recovery.runId
    threadId.value = recovery.threadId
    runStatus.value = recovery.status
    answerSummary.value = recovery.answerSummary ?? ''
    runMetrics.value = recovery
    requirementRevision.value = recovery.requirementRevision ?? 0
    requirementItems.value = recovery.requirementItems ?? []
    requirementBaselineAvailable.value = recovery.requirementBaselineAvailable ?? false
    unsatAnalysis.value = readUnsatAnalysis(recovery.unsatAnalysis)
    citations.value = recovery.citations ?? citations.value
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

  function applyThreadHistory(detail: AgentThreadDetail, currentRunId: string): void {
    const turns: ConversationTurn[] = []
    let pendingUser: typeof detail.messages[number] | null = null
    for (const entry of detail.messages) {
      if (entry.role === 'USER') {
        pendingUser = entry
        continue
      }
      if (pendingUser === null) continue
      const status = typeof entry.visiblePayload.status === 'string'
        ? entry.visiblePayload.status
        : detail.thread.latestStatus
      turns.push({
        id: `server-${entry.messageId}`,
        runId: entry.runId,
        question: pendingUser.content,
        answer: entry.content,
        status,
        unsatAnalysis: readUnsatAnalysis(entry.visiblePayload.unsatAnalysis),
        citations: readCitations(entry.visiblePayload.citations),
      })
      pendingUser = null
    }

    let currentIndex = -1
    for (let index = turns.length - 1; index >= 0; index -= 1) {
      if (turns[index]?.runId === currentRunId) {
        currentIndex = index
        break
      }
    }
    const current = currentIndex >= 0 ? turns.splice(currentIndex, 1)[0] : undefined
    conversationHistory.value = turns
    if (current !== undefined) {
      submittedMessage.value = current.question
      answerSummary.value = current.answer
      runStatus.value = current.status
      unsatAnalysis.value = current.unsatAnalysis ?? null
      citations.value = current.citations ?? []
    } else if (pendingUser?.runId === currentRunId) {
      submittedMessage.value = pendingUser.content
    }
    threadId.value = detail.thread.threadId
  }

  async function loadRecovery(id: string): Promise<void> {
    if (recoveryLoading.value) {
      return
    }
    recoveryLoading.value = true
    errorMessage.value = ''
    const requestedEpoch = recoveryEpoch
    let expiredRegeneration: AgentRunRecovery | null = null
    try {
      const [recovery, trace] = await Promise.all([
        apiRequest<AgentRunRecovery>(`/agent/runs/${id}`),
        apiRequest<{ run: AgentRunSummary; steps: AgentTraceStep[]; toolCalls: AgentTraceToolCall[]; loopEvents?: unknown[] }>(`/agent/runs/${id}/trace`),
      ])
      const detail = await apiRequest<AgentThreadDetail>(
        `/agent/threads/${recovery.threadId}`,
      ).catch(() => null)
      if (requestedEpoch !== recoveryEpoch || (runId.value !== null && runId.value !== id)) {
        return
      }
      if (detail !== null) {
        applyThreadHistory(detail, id)
      }
      applyRecovery(recovery, detail !== null)
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
      if (expiredDraftRegenerationRequested(id)) {
        expiredRegeneration = recovery
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
      errorMessage.value = error instanceof ApiError ? error.message : '无法加载当前任务。'
    } finally {
      recoveryLoading.value = false
      if (expiredRegeneration !== null) {
        void regenerateExpiredDraft(expiredRegeneration)
      }
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
      const query: LocationQueryRaw = { ...route.query, runId: nextRunId }
      delete query.prefill
      delete query.sourceCaseId
      delete query.regenerate
      void router.replace({ query })
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

  watch(
    () => route.query.prefill,
    (value) => {
      const prompt = readReplanPrefill(value)
      if (prompt !== null && (!replanPrefillLoaded.value || message.value !== prompt)) {
        activateReplanPrefill(prompt)
      }
    },
  )

  watch(
    () => route.query.runId,
    (value) => {
      if (typeof value !== 'string' || !SAFE_RUN_ID.test(value) || value === runId.value) return
      message.value = ''
      replanPrefillLoaded.value = false
      clearRunState()
      runId.value = value
      restoreRunContext(value)
      runStatus.value ||= 'RUNNING'
      void loadRecovery(value)
    },
  )

  watch(threadId, (nextThreadId) => {
    if (nextThreadId !== null) {
      window.sessionStorage.setItem(CHAT_ACTIVE_THREAD_STORAGE_KEY, nextThreadId)
    }
  })

  watch(
    [
      threadId,
      runId,
      submittedMessage,
      answerSummary,
      unsatAnalysis,
      runStatus,
      errorMessage,
      conversationHistory,
    ],
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

  async function restoreLatestThread(): Promise<void> {
    try {
      const result = await apiRequest<AgentThreadList>('/agent/threads?page=1&size=1')
      const latestRunId = result.items[0]?.latestRunId
      if (latestRunId === undefined || !SAFE_RUN_ID.test(latestRunId)) return
      runId.value = latestRunId
      runStatus.value = result.items[0]?.latestStatus ?? 'RUNNING'
      await router.replace({ query: { ...route.query, runId: latestRunId } })
      await loadRecovery(latestRunId)
    } catch {
      // The empty-state composer remains usable when history recovery is unavailable.
    }
  }

  onMounted(() => {
    window.addEventListener(NEW_CONVERSATION_EVENT, handleNewConversation)
    const replanPrefill = readReplanPrefill(route.query.prefill)
    if (replanPrefill !== null) {
      window.sessionStorage.removeItem(CHAT_SUPPRESS_RESTORE_STORAGE_KEY)
      activateReplanPrefill(replanPrefill)
      return
    }
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
      return
    }
    void restoreLatestThread()
  })

  onUnmounted(() => {
    window.removeEventListener(NEW_CONVERSATION_EVENT, handleNewConversation)
    activeAbort?.abort()
    clearBookingPoll()
    clearRecoveryTimer()
  })

  return {
    actionType,
    answerSummary,
    bookingRequest,
    candidates,
    citations,
    confirmationToken,
    conversationHistory,
    decisionBusy,
    errorMessage,
    expiresAt,
    hitlDraft,
    hitlFeedback,
    loadRecovery,
    loopEvents,
    message,
    openOrchestration,
    orchestrationOpen,
    orchestrationTab,
    recoveryLoading,
    replanPrefillLoaded,
    requirementBaselineAvailable,
    requirementFieldLabel,
    requirementItems,
    requirementRevision,
    requirementStatusLabel,
    resetConversation,
    resumeRun,
    runId,
    runMetrics,
    runStatus,
    selectCandidate,
    selectExample,
    setOrchestrationOpen,
    startRun,
    steps,
    streaming,
    submittedMessage,
    tools,
    traceOpen,
    unsatAnalysis,
  }
}
