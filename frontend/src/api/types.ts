export interface ApiSuccess<T> {
  data: T
  traceId: string
  timestamp: string
}

export interface ApiErrorDetail {
  field?: string
  reason: string
}

export interface ApiFailure {
  code: string
  message: string
  details: ApiErrorDetail[]
  traceId: string
}

export interface CurrentUser {
  id: number
  username: string
  displayName: string
  email: string
  departmentId: number
  departmentName: string
  roles: string[]
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResult {
  accessToken: string
  tokenType: 'Bearer'
  expiresIn: number
  user: CurrentUser
}

export interface RoomFeature {
  code: string
  name: string
}

export interface MeetingRoom {
  id: number
  code: string
  name: string
  building: string
  floor: string
  capacity: number
  roomType: string
  isHot: boolean
  status: string
  version: number
  features: RoomFeature[]
}

export interface RoomListResult {
  items: MeetingRoom[]
  total: number
}

export interface RoomAvailabilitySlot {
  startAt: string
  endAt: string
  available: boolean
}

export interface RoomAvailability {
  roomId: number
  from: string
  to: string
  availableSlots: RoomAvailabilitySlot[]
}

export interface RoomMutation {
  code: string
  name: string
  building: string
  floor: string
  capacity: number
  roomType: string
  isHot: boolean
  featureCodes: string[]
}

export interface RoomUpdateMutation extends RoomMutation {
  expectedVersion: number
}

export interface RoomStatusMutation {
  status: string
  expectedVersion: number
}

export interface MeetingParticipant {
  employeeId: number
  displayName: string
  participantType: string
}

export interface Meeting {
  id: number
  meetingNo: string
  title: string
  meetingType: string
  organizerId: number
  organizerName: string
  roomId: number
  roomCode: string
  roomName: string
  startAt: string
  endAt: string
  status: string
  source: string
  participants: MeetingParticipant[]
  version: number
  createdAt: string
  updatedAt: string
  cancelledAt: string | null
}

export interface MeetingListResult {
  items: Meeting[]
  total: number
}

export interface MeetingMutation {
  title: string
  meetingType: string
  roomId: number
  startAt: string
  endAt: string
  requiredParticipantIds: number[]
  optionalParticipantIds: number[]
}

export interface MeetingUpdateMutation extends MeetingMutation {
  expectedVersion: number
}

export interface BookingRequest {
  requestNo: string
  status: 'PENDING' | 'PROCESSING' | 'SUCCESS' | 'CONFLICT' | 'FAILED' | string
  meetingId: number | null
  errorCode: string | null
  errorMessage: string | null
  createdAt: string
  updatedAt: string
}

export interface AgentCandidateCostBreakdown {
  optionalParticipantConflict: number
  preferredTimeDeviation: number
  buildingDistance: number
  capacityWaste: number
  preferenceViolation: number
  roomChange: number
}

export interface AgentCandidate {
  candidateId: string
  roomId: number
  roomName: string
  building: string
  startAt: string
  endAt: string
  totalCost: number
  costBreakdown: AgentCandidateCostBreakdown
}

export interface AgentDraftParticipant {
  employeeId: number
  displayName: string
}

export type AgentOperationType = 'CREATE' | 'RESCHEDULE' | 'CANCEL'

export interface AgentDraft {
  title: string
  roomId: number
  roomName: string
  startAt: string
  endAt: string
  requiredParticipants: AgentDraftParticipant[]
  optionalParticipants: AgentDraftParticipant[]
}

export interface AgentMeetingSnapshot {
  meetingId: number
  meetingNo?: string
  title: string
  roomId: number
  roomName: string
  startAt: string
  endAt: string
  status?: string
  version?: number
  requiredParticipants?: AgentDraftParticipant[]
  optionalParticipants?: AgentDraftParticipant[]
}

export interface AgentRescheduleDraft {
  originalMeeting: AgentMeetingSnapshot
  proposedMeeting: AgentDraft
}

export interface AgentCancellationPreview {
  meeting: AgentMeetingSnapshot
}

export type AgentHitlDraft = AgentDraft | AgentRescheduleDraft | AgentCancellationPreview

export interface AgentCitation {
  chunkId: string
  title: string
  headingPath: string[]
  page: number | null
}

export interface AgentRunSummary {
  runId: string
  threadId: string
  traceId: string
  userId: number
  intent: string | null
  status: string
  questionSummary: string
  answerSummary: string | null
  modelCallCount: number
  toolCallCount: number
  modelProvider?: string | null
  configuredModel?: string | null
  model?: string | null
  promptVersion?: string | null
  schemaVersion?: string | null
  inputTokens?: number
  outputTokens?: number
  cachedInputTokens?: number
  cacheMissInputTokens?: number
  totalTokens?: number
  durationMs: number | null
  errorCode: string | null
  createdAt: string
  finishedAt: string | null
}

/** A no-store recovery view returned only for the owner or an ADMIN. */
export interface AgentRunRecovery extends AgentRunSummary {
  candidates?: AgentCandidate[]
  actionType?: AgentOperationType
  operationType?: AgentOperationType
  draft?: AgentHitlDraft
  confirmationToken?: string
  expiresAt?: string
}

export type AgentLoopPhase = 'PLAN' | 'ACT' | 'OBSERVE' | 'VERIFY' | 'REPLAN' | string

export interface AgentRemainingBudget {
  modelCalls?: number
  toolCalls?: number
  graphNodes?: number
  replans?: number
}

export interface AgentTokenUsage {
  inputTokens?: number
  outputTokens?: number
  cachedInputTokens?: number
  cacheMissInputTokens?: number
  totalTokens?: number
}

export interface AgentLoopEvent {
  runId: string
  phase: AgentLoopPhase
  iteration: number
  decision?: string | null
  replanCount: number
  feedbackCodes: string[]
  stopReason?: string | null
  remainingBudget?: AgentRemainingBudget
  modelCallCount?: number
  toolCallCount?: number
  model?: string | null
  promptVersion?: string | null
  schemaVersion?: string | null
  tokenUsage?: AgentTokenUsage
  createdAt?: string | null
}

export interface AgentTraceStep {
  stepId: string
  sequenceNo: number
  agentName: string
  nodeName: string
  status: string
  summary: string
  durationMs: number
  errorCode: string | null
  createdAt: string
}

export interface AgentTraceToolCall {
  toolCallId: string
  toolName: string
  riskLevel: string
  sanitizedArgs: Record<string, unknown>
  resultSummary: string
  status: string
  durationMs: number
  createdAt: string
}

export interface AgentTrace {
  run: AgentRunSummary
  steps: AgentTraceStep[]
  toolCalls: AgentTraceToolCall[]
  loopEvents?: AgentLoopEvent[]
}

export interface AgentStreamRequest {
  threadId: string | null
  message: string
  clientRequestId: string
}

export type AgentResumeAction = 'ACCEPT' | 'EDIT' | 'REJECT'

export interface AgentResumeRequest {
  action: AgentResumeAction
  confirmationToken: string
  editedDraft?: {
    roomId?: number
    startAt?: string
  }
  feedback?: string | null
}

export interface AgentStepEvent {
  runId: string
  stepId: string
  sequenceNo: number
  agentName: string
  nodeName: string
  status: string
  summary: string
  durationMs: number
}

export interface AgentToolEvent {
  runId: string
  toolCallId: string
  toolName: string
  riskLevel: string
  status: string
  summary: string
  durationMs: number
}
