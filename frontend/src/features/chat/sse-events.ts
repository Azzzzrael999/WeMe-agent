import type { Ref } from 'vue'

import { readHitlDraft, readLoopEvent } from '../../api/agent-view'
import type {
  AgentCandidate,
  AgentCitation,
  AgentHitlDraft,
  AgentLoopEvent,
  AgentOperationType,
  AgentRequirementItem,
  AgentStepEvent,
  AgentToolEvent,
  AgentUnsatAnalysis,
  BookingRequest,
} from '../../api/types'
import type { SseMessage } from '../../api/client'
import {
  numberValue,
  readCandidates,
  readCitations,
  readRequirementItems,
  readUnsatAnalysis,
  record,
  stringValue,
} from './parsers'

export interface ChatSseContext {
  actionType: Ref<AgentOperationType | null>
  answerSummary: Ref<string>
  autoOpenOrchestration: (tab: 'requirements' | 'candidates') => void
  beginBookingPoll: (requestNo: string) => void
  bookingRequest: Ref<BookingRequest | null>
  candidates: Ref<AgentCandidate[]>
  citations: Ref<AgentCitation[]>
  confirmationToken: Ref<string | null>
  errorMessage: Ref<string>
  expiresAt: Ref<string | undefined>
  hitlDraft: Ref<AgentHitlDraft | null>
  hitlFeedback: Ref<string>
  loopEvents: Ref<AgentLoopEvent[]>
  openOrchestration: (tab: 'requirements' | 'candidates' | 'policy' | 'execution') => void
  persistRunContext: (id: string) => void
  requirementBaselineAvailable: Ref<boolean>
  requirementItems: Ref<AgentRequirementItem[]>
  requirementRevision: Ref<number>
  runId: Ref<string | null>
  runStatus: Ref<string>
  steps: Ref<AgentStepEvent[]>
  threadId: Ref<string | null>
  tools: Ref<AgentToolEvent[]>
  unsatAnalysis: Ref<AgentUnsatAnalysis | null>
}

export function applyChatSseMessage(messageEvent: SseMessage, context: ChatSseContext): void {
  const {
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
  } = context
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
      unsatAnalysis.value = null
      autoOpenOrchestration('candidates')
      return
    case 'plan.unsat':
      unsatAnalysis.value = readUnsatAnalysis(payload.unsatAnalysis)
      candidates.value = []
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
        unsatAnalysis.value = null
        runStatus.value = stringValue(payload, 'status') ?? 'WAITING_CONFIRMATION'
        answerSummary.value = stringValue(payload, 'answerSummary') ?? answerSummary.value
        if (payload.conflictRepair === true) {
          openOrchestration('candidates')
        } else {
          autoOpenOrchestration('requirements')
        }
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
