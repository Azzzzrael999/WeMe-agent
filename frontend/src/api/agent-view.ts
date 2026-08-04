import type {
  AgentCancellationPreview,
  AgentDraft,
  AgentDraftParticipant,
  AgentHitlDraft,
  AgentLoopEvent,
  AgentMeetingSnapshot,
  AgentOperationType,
  AgentRescheduleDraft,
} from './types'

export function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

export function stringValue(value: Record<string, unknown>, key: string): string | undefined {
  return typeof value[key] === 'string' ? value[key] : undefined
}

export function numberValue(value: Record<string, unknown>, key: string): number | undefined {
  return typeof value[key] === 'number' && Number.isFinite(value[key]) ? value[key] : undefined
}

export function readOperationType(value: unknown): AgentOperationType | null {
  if (value === 'CREATE' || value === 'RESCHEDULE' || value === 'CANCEL') {
    return value
  }
  return value === 'MODIFY' ? 'RESCHEDULE' : null
}

function readParticipants(value: unknown): AgentDraftParticipant[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const participant = recordValue(item)
    const employeeId = participant === null ? undefined : numberValue(participant, 'employeeId')
    const displayName = participant === null ? undefined : stringValue(participant, 'displayName')
    return employeeId !== undefined && displayName !== undefined ? [{ employeeId, displayName }] : []
  })
}

export function readCreateDraft(value: unknown): AgentDraft | null {
  const item = recordValue(value)
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
  }
}

function readMeetingSnapshot(value: unknown): AgentMeetingSnapshot | null {
  const item = recordValue(value)
  if (item === null) {
    return null
  }
  const meetingId = numberValue(item, 'meetingId') ?? numberValue(item, 'id')
  const title = stringValue(item, 'title')
  const roomId = numberValue(item, 'roomId')
  const roomName = stringValue(item, 'roomName')
  const startAt = stringValue(item, 'startAt')
  const endAt = stringValue(item, 'endAt')
  if (meetingId === undefined || title === undefined || roomId === undefined || roomName === undefined || startAt === undefined || endAt === undefined) {
    return null
  }
  return {
    meetingId,
    ...(stringValue(item, 'meetingNo') === undefined ? {} : { meetingNo: stringValue(item, 'meetingNo') }),
    title,
    roomId,
    roomName,
    startAt,
    endAt,
    ...(stringValue(item, 'status') === undefined ? {} : { status: stringValue(item, 'status') }),
    ...(numberValue(item, 'version') === undefined ? {} : { version: numberValue(item, 'version') }),
    requiredParticipants: readParticipants(item.requiredParticipants ?? item.participants),
    optionalParticipants: readParticipants(item.optionalParticipants),
  }
}

export function isRescheduleDraft(draft: AgentHitlDraft): draft is AgentRescheduleDraft {
  return 'originalMeeting' in draft && 'proposedMeeting' in draft
}

export function isCancellationPreview(draft: AgentHitlDraft): draft is AgentCancellationPreview {
  return 'meeting' in draft && !('title' in draft)
}

export function proposedDraft(draft: AgentHitlDraft | null): AgentDraft | null {
  if (draft === null || isCancellationPreview(draft)) {
    return null
  }
  return isRescheduleDraft(draft) ? draft.proposedMeeting : draft
}

export function readHitlDraft(
  value: unknown,
  explicitActionType: unknown,
): { actionType: AgentOperationType; draft: AgentHitlDraft } | null {
  const item = recordValue(value)
  if (item === null) {
    return null
  }
  const actionType =
    readOperationType(explicitActionType) ??
    readOperationType(item.actionType) ??
    readOperationType(item.operationType) ??
    ('originalMeeting' in item || 'before' in item
      ? 'RESCHEDULE'
      : 'meeting' in item
        ? 'CANCEL'
        : 'CREATE')

  if (actionType === 'RESCHEDULE') {
    const originalMeeting = readMeetingSnapshot(item.originalMeeting ?? item.before)
    const proposedMeeting = readCreateDraft(item.proposedMeeting ?? item.after)
    return originalMeeting !== null && proposedMeeting !== null
      ? { actionType, draft: { originalMeeting, proposedMeeting } }
      : null
  }
  if (actionType === 'CANCEL') {
    const meeting = readMeetingSnapshot(item.meeting ?? item.targetMeeting)
    return meeting === null ? null : { actionType, draft: { meeting } }
  }
  const draft = readCreateDraft(item)
  return draft === null ? null : { actionType, draft }
}

function readTokenUsage(value: unknown): AgentLoopEvent['tokenUsage'] {
  const item = recordValue(value)
  if (item === null) {
    return undefined
  }
  return {
    ...(numberValue(item, 'inputTokens') === undefined ? {} : { inputTokens: numberValue(item, 'inputTokens') }),
    ...(numberValue(item, 'outputTokens') === undefined ? {} : { outputTokens: numberValue(item, 'outputTokens') }),
    ...(numberValue(item, 'cachedInputTokens') === undefined ? {} : { cachedInputTokens: numberValue(item, 'cachedInputTokens') }),
    ...(numberValue(item, 'cacheMissInputTokens') === undefined ? {} : { cacheMissInputTokens: numberValue(item, 'cacheMissInputTokens') }),
    ...(numberValue(item, 'totalTokens') === undefined ? {} : { totalTokens: numberValue(item, 'totalTokens') }),
  }
}

export function readLoopEvent(value: unknown): AgentLoopEvent | null {
  const item = recordValue(value)
  if (item === null) {
    return null
  }
  const runId = stringValue(item, 'runId')
  const phase = stringValue(item, 'phase')
  const iteration = numberValue(item, 'iteration')
  if (runId === undefined || phase === undefined || iteration === undefined) {
    return null
  }
  const budget = recordValue(item.remainingBudget)
  return {
    runId,
    phase,
    iteration,
    decision: stringValue(item, 'decision') ?? null,
    replanCount: numberValue(item, 'replanCount') ?? 0,
    feedbackCodes: Array.isArray(item.feedbackCodes)
      ? item.feedbackCodes.filter((code): code is string => typeof code === 'string')
      : [],
    stopReason: stringValue(item, 'stopReason') ?? null,
    ...(budget === null
      ? {}
      : {
          remainingBudget: {
            ...(numberValue(budget, 'modelCalls') === undefined ? {} : { modelCalls: numberValue(budget, 'modelCalls') }),
            ...(numberValue(budget, 'toolCalls') === undefined ? {} : { toolCalls: numberValue(budget, 'toolCalls') }),
            ...(numberValue(budget, 'graphNodes') === undefined ? {} : { graphNodes: numberValue(budget, 'graphNodes') }),
            ...(numberValue(budget, 'replans') === undefined ? {} : { replans: numberValue(budget, 'replans') }),
          },
        }),
    ...(numberValue(item, 'modelCallCount') === undefined ? {} : { modelCallCount: numberValue(item, 'modelCallCount') }),
    ...(numberValue(item, 'toolCallCount') === undefined ? {} : { toolCallCount: numberValue(item, 'toolCallCount') }),
    model: stringValue(item, 'model') ?? null,
    promptVersion: stringValue(item, 'promptVersion') ?? null,
    schemaVersion: stringValue(item, 'schemaVersion') ?? null,
    tokenUsage: readTokenUsage(item.tokenUsage),
    createdAt: stringValue(item, 'createdAt') ?? stringValue(item, 'timestamp') ?? null,
  }
}
