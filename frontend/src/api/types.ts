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

export type EmployeeRole = 'EMPLOYEE' | 'ADMIN'
export type EmployeeStatus = 'ACTIVE' | 'DISABLED'

export interface DepartmentOption {
  id: number
  name: string
  defaultBuilding: string
  defaultFloor: string
}

export interface DepartmentListResult {
  items: DepartmentOption[]
}

export interface Employee {
  id: number
  username: string
  displayName: string
  email: string
  departmentId: number | null
  departmentName: string | null
  role: EmployeeRole
  status: EmployeeStatus
  version: number
  createdAt: string
  updatedAt: string
}

export interface EmployeeListResult {
  items: Employee[]
  total: number
}

export interface EmployeeDirectoryItem {
  id: number
  displayName: string
  departmentId: number | null
  departmentName: string | null
}

export interface EmployeeDirectoryResult {
  items: EmployeeDirectoryItem[]
}

export interface EmployeeCreateMutation {
  username: string
  initialPassword: string
  displayName: string
  email: string
  departmentId: number | null
  role: EmployeeRole
  status: EmployeeStatus
}

export interface EmployeeUpdateMutation {
  displayName: string
  email: string
  departmentId: number | null
  role: EmployeeRole
  expectedVersion: number
}

export interface EmployeeStatusMutation {
  status: EmployeeStatus
  expectedVersion: number
}

export interface EmployeePasswordMutation {
  newPassword: string
  expectedVersion: number
}

export type NotificationType =
  | 'MEETING_CONFIRMED'
  | 'MEETING_CHANGED'
  | 'MEETING_CANCELLED'
  | 'RESOURCE_UNAVAILABLE'
  | 'RESOURCE_RESTORED'
  | 'MEETING_REMINDER_24H'
  | 'MEETING_REMINDER_30M'
  | 'PREPARATION_MISSING'
  | 'ACTION_ITEM_DUE_SOON'
  | 'ACTION_ITEM_OVERDUE'

export interface NotificationItem {
  id: number
  type: NotificationType
  title: string
  content: string
  relatedMeetingId: number | null
  relatedReplanCaseId: number | null
  readAt: string | null
  createdAt: string
}

export interface NotificationListResult {
  items: NotificationItem[]
  total: number
  unreadCount: number
}

export interface NotificationUnreadCountResult {
  unreadCount: number
}

export interface NotificationReadAllResult {
  updatedCount: number
  readAt: string
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
  reason?: string
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

export type PreparationChecklistStatus = 'READY' | 'NEEDS_ATTENTION'
export type MeetingMaterialStatus = 'MISSING' | 'READY'
export type PostMeetingDraftStatus =
  | 'PROCESSING'
  | 'PENDING_REVIEW'
  | 'ACCEPTED'
  | 'REJECTED'
  | 'FAILED'
export type MeetingActionItemStatus = 'OPEN' | 'IN_PROGRESS' | 'DONE'

export interface MeetingLifecyclePermissions {
  canEditPreparation: boolean
  canSubmitRecord: boolean
  canReviewDraft: boolean
}

export interface MeetingAgendaItem {
  id: number
  sequenceNo: number
  topic: string
  ownerEmployeeId: number
  ownerName: string
  plannedMinutes: number
}

export interface MeetingMaterial {
  id: number
  sequenceNo: number
  title: string
  ownerEmployeeId: number
  ownerName: string
  required: boolean
  status: MeetingMaterialStatus
  versionLabel: string | null
  note: string | null
}

export interface PreparationChecklistItem {
  code: string
  passed: boolean
  message: string
}

export interface PreparationChecklist {
  status: PreparationChecklistStatus
  generatedAt: string
  items: PreparationChecklistItem[]
}

export interface MeetingPreparation {
  version: number
  agendaItems: MeetingAgendaItem[]
  materials: MeetingMaterial[]
  checklist: PreparationChecklist
}

export interface PostMeetingMinutesDraft {
  background: string
  discussionSummary: string
  conclusion: string
}

export interface PostMeetingDecisionDraft {
  content: string
  rationale: string | null
}

export interface PostMeetingActionItemDraft {
  title: string
  description: string | null
  assigneeEmployeeId: number | null
  assigneeName?: string | null
  dueAt: string | null
}

export interface PostMeetingDraftContent {
  minutes: PostMeetingMinutesDraft
  decisions: PostMeetingDecisionDraft[]
  actionItems: PostMeetingActionItemDraft[]
}

export interface PostMeetingDraft {
  id: number
  status: PostMeetingDraftStatus
  version: number
  agentRunId: string | null
  errorCode: string | null
  content: PostMeetingDraftContent | null
}

export interface MeetingMinutes {
  background: string
  discussionSummary: string
  conclusion: string
  confirmedBy: number
  confirmedAt: string
}

export interface MeetingDecision {
  id: number
  sequenceNo: number
  content: string
  rationale: string | null
}

export interface MeetingActionItem {
  id: number
  sequenceNo: number
  title: string
  description: string | null
  assigneeEmployeeId: number
  assigneeName: string
  dueAt: string
  status: MeetingActionItemStatus
  version: number
  completedAt: string | null
}

export interface MeetingLifecycle {
  meeting: Meeting
  permissions: MeetingLifecyclePermissions
  preparation: MeetingPreparation
  postMeeting: {
    draft: PostMeetingDraft | null
    minutes: MeetingMinutes | null
    decisions: MeetingDecision[]
    actionItems: MeetingActionItem[]
  }
}

export interface MeetingPreparationMutation {
  expectedVersion: number
  agendaItems: Array<{
    topic: string
    ownerEmployeeId: number
    plannedMinutes: number
  }>
  materials: Array<{
    title: string
    ownerEmployeeId: number
    required: boolean
    status: MeetingMaterialStatus
    versionLabel: string | null
    note: string | null
  }>
}

export interface PostMeetingDraftReviewMutation {
  action: 'ACCEPT' | 'EDIT' | 'REJECT'
  expectedVersion: number
  editedDraft?: PostMeetingDraftContent
}

export interface MeetingActionItemMutation {
  status: MeetingActionItemStatus
  expectedVersion: number
}

export type ReplanCaseStatus = 'OPEN' | 'RESOLVED' | 'RESTORED' | 'CANCELLED'

export type ReplanResolutionType =
  | 'QUICK_ROOM_CHANGE'
  | 'AGENT_RESCHEDULE'
  | 'MEETING_CANCELLED'
  | 'RESOURCE_RESTORED'

export interface ReplanFailedRoom {
  id: number
  name: string
}

export interface ReplanCase {
  id: number
  caseNo: string
  meetingId: number
  organizerId: number
  status: ReplanCaseStatus
  failureReason: string
  failedRoom: ReplanFailedRoom
  roomStatusVersion: number
  originalStartAt: string
  originalEndAt: string
  currentMeeting: Meeting
  changedConstraints: string[]
  preservedConstraints: string[]
  resolutionType: ReplanResolutionType | null
  resolvedRoomId: number | null
  resolvedStartAt: string | null
  resolvedEndAt: string | null
  version: number
  createdAt: string
  updatedAt: string
  resolvedAt: string | null
}

export interface ReplanCaseListResult {
  items: ReplanCase[]
  total: number
}

export interface ReplanAlternative {
  roomId: number
  roomCode: string
  roomName: string
  building: string
  floor: string
  capacity: number
  features: RoomFeature[]
  reason: string
}

export interface ReplanAlternatives {
  caseId: number
  caseVersion: number
  meetingVersion: number
  sameTime: true
  changedConstraints: string[]
  preservedConstraints: string[]
  items: ReplanAlternative[]
}

export interface ReplanResolveMutation {
  roomId: number
  expectedMeetingVersion: number
  expectedCaseVersion: number
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

export interface AgentUnsatBlockingInterval {
  resourceType: 'EMPLOYEE' | 'ROOM' | 'POLICY' | string
  resourceId: number | null
  resourceName: string | null
  meetingId: number | null
  startAt: string
  endAt: string
  reason: string
}

export interface AgentUnsatAnalysis {
  category: string
  summary: string
  requestedWindow: { start: string; end: string }
  durationMinutes: number
  blockingIntervals: AgentUnsatBlockingInterval[]
  relaxationSuggestions: string[]
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

export type AgentRequirementStatus =
  | 'EXPLICIT'
  | 'DEFAULTED'
  | 'DIRECTORY_RESOLVED'
  | 'INHERITED'
  | 'MISSING'
  | 'AMBIGUOUS'
  | 'CONFLICT'
  | 'UNSPECIFIED'
  | 'CLOSED'
  | string

export interface AgentRequirementItem {
  field: string
  status: AgentRequirementStatus
  summary: string
  source?: string | null
  ruleId?: string | null
  blocking?: boolean
}

/** A no-store recovery view returned only for the owner or an ADMIN. */
export interface AgentRunRecovery extends AgentRunSummary {
  candidates?: AgentCandidate[]
  actionType?: AgentOperationType
  operationType?: AgentOperationType
  draft?: AgentHitlDraft
  confirmationToken?: string
  expiresAt?: string
  requirementRevision?: number
  requirementItems?: AgentRequirementItem[]
  requirementBaselineAvailable?: boolean
  unsatAnalysis?: AgentUnsatAnalysis
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
  baseRunId?: string
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
