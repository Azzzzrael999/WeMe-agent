import type {
  AgentCandidate,
  AgentCitation,
  AgentRequirementItem,
  AgentUnsatAnalysis,
} from '../../api/types'
export function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

export function stringValue(value: Record<string, unknown>, key: string): string | undefined {
  return typeof value[key] === 'string' ? value[key] : undefined
}

export function numberValue(value: Record<string, unknown>, key: string): number | undefined {
  return typeof value[key] === 'number' && Number.isFinite(value[key]) ? value[key] : undefined
}

export function readCandidates(value: unknown): AgentCandidate[] {
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

export function readCitations(value: unknown): AgentCitation[] {
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

export function readRequirementItems(value: unknown): AgentRequirementItem[] {
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

export function readUnsatAnalysis(value: unknown): AgentUnsatAnalysis | null {
  const analysis = record(value)
  const window = analysis === null ? null : record(analysis.requestedWindow)
  if (analysis === null || window === null) {
    return null
  }
  const category = stringValue(analysis, 'category')
  const summary = stringValue(analysis, 'summary')
  const start = stringValue(window, 'start')
  const end = stringValue(window, 'end')
  const durationMinutes = numberValue(analysis, 'durationMinutes')
  if (
    category === undefined
    || summary === undefined
    || start === undefined
    || end === undefined
    || durationMinutes === undefined
  ) {
    return null
  }
  const blockingIntervals = Array.isArray(analysis.blockingIntervals)
    ? analysis.blockingIntervals.flatMap((entry) => {
        const blocker = record(entry)
        if (blocker === null) {
          return []
        }
        const resourceType = stringValue(blocker, 'resourceType')
        const startAt = stringValue(blocker, 'startAt')
        const endAt = stringValue(blocker, 'endAt')
        const reason = stringValue(blocker, 'reason')
        if (
          resourceType === undefined
          || startAt === undefined
          || endAt === undefined
          || reason === undefined
        ) {
          return []
        }
        return [{
          resourceType,
          resourceId: typeof blocker.resourceId === 'number' ? blocker.resourceId : null,
          resourceName: typeof blocker.resourceName === 'string' ? blocker.resourceName : null,
          meetingId: typeof blocker.meetingId === 'number' ? blocker.meetingId : null,
          startAt,
          endAt,
          reason,
        }]
      })
    : []
  const relaxationSuggestions = Array.isArray(analysis.relaxationSuggestions)
    ? analysis.relaxationSuggestions.filter((item): item is string => typeof item === 'string')
    : []
  return {
    category,
    summary,
    requestedWindow: { start, end },
    durationMinutes,
    blockingIntervals,
    relaxationSuggestions,
  }
}

export function requirementFieldLabel(field: string): string {
  return {
    timeWindow: '时间范围',
    durationMinutes: '会议时长',
    requiredParticipants: '参会人员',
    optionalRequirements: '其他要求',
  }[field] ?? '其他要求'
}

export function requirementStatusLabel(status: string): string {
  return {
    EXPLICIT: '已明确',
    DEFAULTED: '已默认',
    DIRECTORY_RESOLVED: '组织库补全',
    INHERITED: '原会议继承',
    MISSING: '待补充',
    AMBIGUOUS: '待确认',
    CONFLICT: '有冲突',
    UNSPECIFIED: '未说明',
    CLOSED: '已结束',
  }[status] ?? '待确认'
}
