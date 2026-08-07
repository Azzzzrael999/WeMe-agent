"""Structured contracts exchanged by the four Day 4 agents.

The models deliberately use ``extra='forbid'``.  They are used both for
model-output validation and for the state passed between LangGraph nodes, so
an unexpected field cannot quietly change a business decision.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class AgentSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
    )


def _is_shanghai_slot(value: datetime) -> bool:
    """Return whether an external scheduling time is an Asia/Shanghai half-hour slot."""

    return (
        value.tzinfo is not None
        and value.utcoffset() == timedelta(hours=8)
        and value.minute % 30 == 0
        and value.second == 0
        and value.microsecond == 0
    )


class Intent(StrEnum):
    CREATE_MEETING = "CREATE_MEETING"
    FIND_COMMON_TIME = "FIND_COMMON_TIME"
    RECOMMEND_ROOM = "RECOMMEND_ROOM"
    MODIFY_MEETING = "MODIFY_MEETING"
    CANCEL_MEETING = "CANCEL_MEETING"
    QUERY_POLICY = "QUERY_POLICY"
    UPDATE_PREFERENCE = "UPDATE_PREFERENCE"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_USER_INPUT = "WAITING_USER_INPUT"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_BUSINESS_RESULT = "WAITING_BUSINESS_RESULT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Route(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    POLICY = "POLICY"
    SCHEDULING = "SCHEDULING"
    CLARIFICATION = "CLARIFICATION"
    HITL = "HITL"
    WAIT_BUSINESS_RESULT = "WAIT_BUSINESS_RESULT"
    FINAL = "FINAL"
    FAIL = "FAIL"


class OperationType(StrEnum):
    CREATE = "CREATE"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"


class EvidenceProvenance(StrEnum):
    USER_EXPLICIT = "USER_EXPLICIT"
    USER_DERIVED = "USER_DERIVED"


class RequirementSlotStatus(StrEnum):
    EXPLICIT = "EXPLICIT"
    DEFAULTED = "DEFAULTED"
    DIRECTORY_RESOLVED = "DIRECTORY_RESOLVED"
    INHERITED = "INHERITED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"
    UNSPECIFIED = "UNSPECIFIED"
    CLOSED = "CLOSED"


class Participant(AgentSchema):
    name: str = Field(min_length=1, max_length=64)
    employee_id: int | None = Field(default=None, ge=1)


class TimeWindow(AgentSchema):
    start: datetime
    end: datetime

    @field_validator("end")
    @classmethod
    def end_must_be_after_start(cls, end: datetime, info: Any) -> datetime:
        start = info.data.get("start")
        if isinstance(start, datetime) and end <= start:
            raise ValueError("end must be after start")
        return end


class Constraint(AgentSchema):
    type: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=160)
    weight: int | None = Field(default=None, ge=1, le=100)


class MeetingRequest(AgentSchema):
    intent: Intent
    title: str = Field(min_length=1, max_length=128)
    meeting_type: str = Field(min_length=1, max_length=32)
    duration_minutes: int = Field(ge=30, le=480, multiple_of=30)
    time_window: TimeWindow | None = None
    required_participants: list[Participant] = Field(default_factory=list, max_length=50)
    optional_groups: list[str] = Field(default_factory=list, max_length=20)
    required_features: list[str] = Field(default_factory=list, max_length=20)
    minimum_capacity: int | None = Field(default=None, ge=1, le=10000)
    preferred_buildings: list[str] = Field(default_factory=list, max_length=20)
    hard_constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    soft_constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    target_meeting_id: int | None = Field(default=None, ge=1)
    target_meeting_reference: str | None = Field(default=None, max_length=240)


class Citation(AgentSchema):
    chunk_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    heading_path: list[str] = Field(min_length=1, max_length=10)
    page: int | None = Field(default=None, ge=1)


class PolicyConstraint(AgentSchema):
    type: str = Field(
        pattern=(
            "^(MAX_DURATION_MINUTES|ALLOWED_ROOM_TYPES|REQUIRED_ROOM_FEATURE|"
            "DISALLOWED_TIME_WINDOW|ADVISORY_ONLY)$"
        )
    )
    value: str = Field(min_length=1, max_length=160)


class PolicyResult(AgentSchema):
    summary: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    verification_status: str = Field(pattern="^(VERIFIED|UNVERIFIED)$")
    constraints: list[PolicyConstraint] = Field(default_factory=list, max_length=10)
    citations: list[Citation] = Field(default_factory=list, max_length=3)


class SupervisorDecision(AgentSchema):
    route: Route
    intent_hint: Intent | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence: str = Field(default="", max_length=500)
    summary: str = Field(min_length=1, max_length=240)


class ClarificationResponse(AgentSchema):
    """User-facing wording only; business facts stay in deterministic state."""

    message: str = Field(min_length=1, max_length=500)

    @field_validator("message")
    @classmethod
    def reject_internal_or_effect_claims(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("clarification message must not be blank")
        if re.search(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", normalized) or any(
            marker in normalized for marker in ("EVALUATOR_FEEDBACK", "内部错误码")
        ):
            raise ValueError("clarification message exposed internal validation details")
        if any(
            claim in normalized
            for claim in ("已创建会议", "已确认会议", "已取消会议", "已完成预约")
        ):
            raise ValueError("clarification message claimed an unverified business effect")
        return normalized


class FieldEvidence(AgentSchema):
    field: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=500)
    provenance: EvidenceProvenance


class RequirementDraft(AgentSchema):
    intent: Intent
    title: str | None = Field(default=None, max_length=128)
    meeting_type: str | None = Field(default=None, max_length=32)
    duration_minutes: int | None = Field(default=None, ge=30, le=480, multiple_of=30)
    time_window: TimeWindow | None = None
    pending_start_at: datetime | None = None
    pending_start_ambiguous: bool = False
    required_participant_names: list[str] = Field(default_factory=list, max_length=50)
    participant_scope: Literal["MY_DEPARTMENT", "ORGANIZER_ONLY"] | None = None
    participant_list_modified: bool = False
    optional_groups: list[str] = Field(default_factory=list, max_length=20)
    required_features: list[str] = Field(default_factory=list, max_length=20)
    minimum_capacity: int | None = Field(default=None, ge=1, le=10_000)
    preferred_buildings: list[str] = Field(default_factory=list, max_length=20)
    hard_constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    soft_constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    target_meeting_id: int | None = Field(default=None, ge=1)
    target_meeting_reference: str | None = Field(default=None, max_length=240)
    field_evidence: list[FieldEvidence] = Field(default_factory=list, max_length=40)
    needs_policy: bool = False
    summary: str = Field(min_length=1, max_length=240)


class RequirementExtraction(AgentSchema):
    requirement_draft: RequirementDraft
    missing_fields: list[str] = Field(default_factory=list, max_length=10)

    @property
    def meeting_request(self) -> MeetingRequest:
        """Compatibility view for the deterministic fixture evaluator."""

        draft = self.requirement_draft
        duration = draft.duration_minutes
        if duration is None and draft.time_window is not None:
            duration = int((draft.time_window.end - draft.time_window.start).total_seconds() / 60)
        return MeetingRequest(
            intent=draft.intent,
            title=draft.title or "会议安排",
            meeting_type=draft.meeting_type or "GENERAL",
            duration_minutes=duration or 30,
            time_window=draft.time_window,
            required_participants=[
                Participant(name=name) for name in draft.required_participant_names
            ],
            optional_groups=draft.optional_groups,
            required_features=draft.required_features,
            minimum_capacity=max(
                draft.minimum_capacity or 1,
                len(set(draft.required_participant_names))
                if draft.participant_scope == "MY_DEPARTMENT"
                else 1 + len(set(draft.required_participant_names)),
            ),
            preferred_buildings=draft.preferred_buildings,
            hard_constraints=draft.hard_constraints,
            soft_constraints=draft.soft_constraints,
            target_meeting_id=draft.target_meeting_id,
            target_meeting_reference=draft.target_meeting_reference,
        )


class RequirementItem(AgentSchema):
    field: Literal["timeWindow", "durationMinutes", "requiredParticipants", "optionalRequirements"]
    status: RequirementSlotStatus
    summary: str = Field(min_length=1, max_length=500)
    source: str | None = Field(default=None, max_length=500)
    rule_id: str | None = Field(default=None, max_length=64)
    blocking: bool = False


class ProcessedRequirementInput(AgentSchema):
    client_request_id: str = Field(min_length=1, max_length=80)
    content_hash: str = Field(min_length=64, max_length=64)


class NormalizationReport(AgentSchema):
    defaults_applied: list[str] = Field(default_factory=list, max_length=20)
    derived_fields: list[str] = Field(default_factory=list, max_length=20)
    evidence_coverage: float = Field(ge=0, le=1)


class PolicySelection(AgentSchema):
    answer_summary: str = Field(min_length=1, max_length=500)
    selected_chunk_ids: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    constraints: list[PolicyConstraint] = Field(default_factory=list, max_length=10)


class CandidateCostBreakdown(AgentSchema):
    """Weighted, user-visible cost components for one schedule candidate.

    Each value already includes the fixed weight from the Day 5 OR-Tools
    objective. Keeping the weighted values in the schema makes the returned
    plan directly explainable without exposing internal solver state.
    """

    optional_participant_conflict: int = Field(default=0, ge=0)
    preferred_time_deviation: int = Field(default=0, ge=0)
    building_distance: int = Field(default=0, ge=0)
    capacity_waste: int = Field(default=0, ge=0)
    preference_violation: int = Field(default=0, ge=0)
    room_change: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return sum(
            (
                self.optional_participant_conflict,
                self.preferred_time_deviation,
                self.building_distance,
                self.capacity_waste,
                self.preference_violation,
                self.room_change,
            )
        )


class ScheduleCandidate(AgentSchema):
    """A candidate safe for the Day 5 ``plan.candidates`` SSE event."""

    candidate_id: str = Field(min_length=1, max_length=64)
    room_id: int = Field(ge=1)
    room_name: str = Field(min_length=1, max_length=64)
    building: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime
    total_cost: int = Field(ge=0)
    cost_breakdown: CandidateCostBreakdown

    @model_validator(mode="after")
    def validate_visible_candidate(self) -> ScheduleCandidate:
        if self.end_at <= self.start_at:
            raise ValueError("candidate endAt must be after startAt")
        if not _is_shanghai_slot(self.start_at) or not _is_shanghai_slot(self.end_at):
            raise ValueError("candidate times must be Asia/Shanghai 30-minute slots")
        if self.total_cost != self.cost_breakdown.total:
            raise ValueError("totalCost must equal the sum of costBreakdown")
        return self


class UnsatCategory(StrEnum):
    FACILITY_CAPACITY = "FACILITY_CAPACITY"
    REQUIRED_AVAILABILITY = "REQUIRED_AVAILABILITY"
    TIME_WINDOW_DURATION = "TIME_WINDOW_DURATION"
    POLICY = "POLICY"


class BlockingInterval(AgentSchema):
    resource_type: Literal["EMPLOYEE", "ROOM", "POLICY"]
    resource_id: int | None = Field(default=None, ge=1)
    resource_name: str | None = Field(default=None, min_length=1, max_length=64)
    meeting_id: int | None = Field(default=None, ge=1)
    start_at: datetime
    end_at: datetime
    reason: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_blocking_interval(self) -> BlockingInterval:
        if self.end_at <= self.start_at:
            raise ValueError("blocking interval endAt must be after startAt")
        if not _is_shanghai_slot(self.start_at) or not _is_shanghai_slot(self.end_at):
            raise ValueError("blocking intervals must use Asia/Shanghai 30-minute slots")
        return self


class UnsatAnalysis(AgentSchema):
    category: UnsatCategory
    summary: str = Field(min_length=1, max_length=500)
    requested_window: TimeWindow
    duration_minutes: int = Field(ge=30, le=480, multiple_of=30)
    blocking_intervals: list[BlockingInterval] = Field(default_factory=list, max_length=10)
    relaxation_suggestions: list[str] = Field(default_factory=list, max_length=3)


class BusyInterval(AgentSchema):
    meeting_id: int | None = Field(default=None, ge=1)
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def validate_busy_interval(self) -> BusyInterval:
        if self.end_at <= self.start_at:
            raise ValueError("busy interval endAt must be after startAt")
        if not _is_shanghai_slot(self.start_at) or not _is_shanghai_slot(self.end_at):
            raise ValueError("busy intervals must use Asia/Shanghai 30-minute slots")
        return self


class EmployeeBusySlots(AgentSchema):
    employee_id: int = Field(ge=1)
    busy_intervals: list[BusyInterval] = Field(default_factory=list, max_length=672)


class RoomAvailability(AgentSchema):
    """A room returned by Java's allow-listed availability Tool.

    ``available_slot_starts`` is optional because the current Java Tool
    contract guarantees that a returned room is available for the whole
    requested window. A future richer Tool response may provide exact slots;
    when present every selected continuous slot is independently checked.
    """

    room_id: int = Field(ge=1)
    room_name: str = Field(min_length=1, max_length=64)
    building: str = Field(min_length=1, max_length=64)
    capacity: int = Field(ge=1, le=10000)
    room_type: str = Field(min_length=1, max_length=32)
    features: list[str] = Field(default_factory=list, max_length=50)
    busy_intervals: list[BusyInterval] = Field(default_factory=list, max_length=672)
    available_slot_starts: list[datetime] | None = Field(default=None, max_length=672)

    @field_validator("available_slot_starts")
    @classmethod
    def validate_available_slots(cls, values: list[datetime] | None) -> list[datetime] | None:
        if values is not None and any(not _is_shanghai_slot(value) for value in values):
            raise ValueError("available slots must use Asia/Shanghai 30-minute slots")
        return values


class AvailabilitySnapshot(AgentSchema):
    rooms: list[RoomAvailability] = Field(default_factory=list, max_length=50)
    employee_busy_slots: list[EmployeeBusySlots] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def prevent_duplicate_snapshot_entities(self) -> AvailabilitySnapshot:
        room_ids = [room.room_id for room in self.rooms]
        employee_ids = [employee.employee_id for employee in self.employee_busy_slots]
        if len(room_ids) != len(set(room_ids)):
            raise ValueError("availability snapshot contains duplicate room IDs")
        if len(employee_ids) != len(set(employee_ids)):
            raise ValueError("availability snapshot contains duplicate employee IDs")
        return self


class DailyTimeRange(AgentSchema):
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class SchedulingPreferences(AgentSchema):
    preferred_buildings: list[str] = Field(default_factory=list, max_length=20)
    avoid_weekdays: list[int] = Field(default_factory=list, max_length=7)
    avoid_time_ranges: list[DailyTimeRange] = Field(default_factory=list, max_length=20)
    preferred_time_ranges: list[DailyTimeRange] = Field(default_factory=list, max_length=20)

    @field_validator("avoid_weekdays")
    @classmethod
    def validate_weekdays(cls, values: list[int]) -> list[int]:
        if any(value < 0 or value > 6 for value in values):
            raise ValueError("weekday values must be in the Python Monday=0 to Sunday=6 range")
        return values


class SchedulingProblem(AgentSchema):
    """Deterministic input for candidate construction and the OR-Tools solver."""

    meeting_request: MeetingRequest
    availability_snapshot: AvailabilitySnapshot
    organizer_id: int | None = Field(default=None, ge=1)
    required_participant_ids: list[int] = Field(default_factory=list, max_length=50)
    optional_participant_ids: list[int] = Field(default_factory=list, max_length=50)
    policy_constraints: list[PolicyConstraint] = Field(default_factory=list, max_length=10)
    user_preferences: SchedulingPreferences = Field(default_factory=SchedulingPreferences)
    building_distance_scores: dict[str, int] = Field(default_factory=dict)
    previous_room_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_scheduling_problem(self) -> SchedulingProblem:
        window = self.meeting_request.time_window
        if window is None:
            raise ValueError("scheduling requires a timeWindow")
        if not _is_shanghai_slot(window.start) or not _is_shanghai_slot(window.end):
            raise ValueError("timeWindow must use Asia/Shanghai 30-minute slots")
        if window.end - window.start > timedelta(days=14):
            raise ValueError("scheduling timeWindow must be no more than 14 days")
        required = set(self.required_participant_ids)
        optional = set(self.optional_participant_ids)
        if len(required) != len(self.required_participant_ids):
            raise ValueError("required participant IDs must be unique")
        if len(optional) != len(self.optional_participant_ids):
            raise ValueError("optional participant IDs must be unique")
        if required.intersection(optional):
            raise ValueError("an employee cannot be both required and optional")
        if any(score < 0 for score in self.building_distance_scores.values()):
            raise ValueError("building distance scores must not be negative")
        return self


class DraftParticipant(AgentSchema):
    employee_id: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=64)


class BookingDraft(AgentSchema):
    """User-visible Java draft kept in the Redis LangGraph checkpoint only."""

    title: str = Field(min_length=1, max_length=128)
    room_id: int = Field(ge=1)
    room_name: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime
    required_participants: list[DraftParticipant] = Field(default_factory=list, max_length=100)
    optional_participants: list[DraftParticipant] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_draft_window(self) -> BookingDraft:
        if self.end_at <= self.start_at:
            raise ValueError("draft endAt must be after startAt")
        if not _is_shanghai_slot(self.start_at) or not _is_shanghai_slot(self.end_at):
            raise ValueError("draft times must use Asia/Shanghai 30-minute slots")
        return self


class MeetingParticipantView(AgentSchema):
    employee_id: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=64)
    participant_type: str = Field(pattern="^(REQUIRED|OPTIONAL)$")


class MeetingView(AgentSchema):
    id: int = Field(ge=1)
    meeting_no: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=128)
    meeting_type: str = Field(min_length=1, max_length=32)
    organizer_id: int = Field(ge=1)
    organizer_name: str = Field(min_length=1, max_length=64)
    room_id: int = Field(ge=1)
    room_code: str = Field(min_length=1, max_length=32)
    room_name: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime
    status: str = Field(min_length=1, max_length=24)
    source: str = Field(min_length=1, max_length=16)
    participants: list[MeetingParticipantView] = Field(default_factory=list, max_length=100)
    version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None


class CreateDraftView(AgentSchema):
    action_type: Literal[OperationType.CREATE] = OperationType.CREATE
    draft: BookingDraft


class RescheduleDraftView(AgentSchema):
    action_type: Literal[OperationType.RESCHEDULE] = OperationType.RESCHEDULE
    original_meeting: MeetingView
    proposed_meeting: BookingDraft


class CancellationDraftView(AgentSchema):
    action_type: Literal[OperationType.CANCEL] = OperationType.CANCEL
    meeting: MeetingView


MutationDraft = Annotated[
    CreateDraftView | RescheduleDraftView | CancellationDraftView,
    Field(discriminator="action_type"),
]


class ResumeAction(StrEnum):
    ACCEPT = "ACCEPT"
    EDIT = "EDIT"
    REJECT = "REJECT"


class EditedDraft(AgentSchema):
    room_id: int | None = Field(default=None, ge=1)
    start_at: datetime | None = None
    meeting_id: int | None = Field(default=None, ge=1)

    @field_validator("start_at")
    @classmethod
    def validate_start_slot(cls, value: datetime | None) -> datetime | None:
        if value is not None and not _is_shanghai_slot(value):
            raise ValueError("edited startAt must use an Asia/Shanghai 30-minute slot")
        return value

    @model_validator(mode="after")
    def require_a_change(self) -> EditedDraft:
        if self.room_id is None and self.start_at is None and self.meeting_id is None:
            raise ValueError("editedDraft must contain roomId, startAt or meetingId")
        return self


class AgentResumeRequest(AgentSchema):
    action: ResumeAction
    confirmation_token: str = Field(min_length=1, max_length=80)
    edited_draft: EditedDraft | None = None
    feedback: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentResumeRequest:
        if self.action is ResumeAction.EDIT and self.edited_draft is None:
            raise ValueError("EDIT requires editedDraft")
        if self.action is not ResumeAction.EDIT and self.edited_draft is not None:
            raise ValueError("editedDraft is only allowed for EDIT")
        return self


class HitlResumeCommand(AgentSchema):
    """Non-secret payload handed to LangGraph after API token verification."""

    action: ResumeAction
    edited_draft: EditedDraft | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> HitlResumeCommand:
        if self.action is ResumeAction.EDIT and self.edited_draft is None:
            raise ValueError("EDIT requires editedDraft")
        if self.action is not ResumeAction.EDIT and self.edited_draft is not None:
            raise ValueError("editedDraft is only allowed for EDIT")
        return self


class BookingResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    CONFLICT = "CONFLICT"


class BookingConflict(AgentSchema):
    type: str = Field(min_length=1, max_length=64)
    room_id: int | None = Field(default=None, ge=1)
    slots: list[int] = Field(default_factory=list, max_length=48)


class BusinessResultCallback(AgentSchema):
    event_id: str = Field(min_length=1, max_length=64)
    request_no: str = Field(min_length=1, max_length=64)
    status: BookingResultStatus
    meeting_id: int | None = Field(default=None, ge=1)
    conflict: BookingConflict | None = None

    @model_validator(mode="after")
    def validate_business_result_shape(self) -> BusinessResultCallback:
        if self.status is BookingResultStatus.SUCCESS and (
            self.meeting_id is None or self.conflict is not None
        ):
            raise ValueError("SUCCESS requires meetingId and must not include conflict")
        if self.status is BookingResultStatus.CONFLICT and (
            self.conflict is None or self.meeting_id is not None
        ):
            raise ValueError("CONFLICT requires conflict and must not include meetingId")
        return self


class SchedulingPlan(AgentSchema):
    tool_names: list[str] = Field(min_length=1, max_length=4)
    summary: str = Field(min_length=1, max_length=240)

    @field_validator("tool_names")
    @classmethod
    def only_read_tools(cls, tool_names: list[str]) -> list[str]:
        allowed = {
            "resolve_employees",
            "get_employee_free_busy",
            "search_available_rooms",
            "get_recent_meeting",
        }
        if any(tool not in allowed for tool in tool_names):
            raise ValueError("Scheduling model output only allows Java READ tools")
        return tool_names


class RequirementFeedbackState(AgentSchema):
    codes: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=500)
    repairable: bool


class ConflictRepairFeedbackState(AgentSchema):
    conflict_type: str = Field(min_length=1, max_length=64)
    failed_candidate_id: str = Field(min_length=1, max_length=64)
    preserved_constraints: list[str] = Field(min_length=1, max_length=20)
    excluded_candidate_ids: list[str] = Field(min_length=1, max_length=3)
    replan_count: int = Field(ge=1, le=2)
    room_id: int | None = Field(default=None, ge=1)
    slots: list[int] = Field(default_factory=list, max_length=48)
    reason: str = Field(min_length=1, max_length=240)


class AgentError(AgentSchema):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=240)


class AgentState(AgentSchema):
    """The Pydantic-only protocol exchanged between every graph node."""

    thread_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    trace_id: str = Field(min_length=1, max_length=64)
    user_id: int = Field(ge=1)
    roles: list[str] = Field(min_length=1, max_length=10)
    message: str = Field(min_length=1, max_length=4000)
    request_time: datetime
    intent: Intent | None = None
    meeting_request: MeetingRequest | None = None
    requirement_draft: RequirementDraft | None = None
    requirement_items: list[RequirementItem] = Field(default_factory=list, max_length=8)
    requirement_revision: int = Field(default=0, ge=0, le=100)
    continuation_turn: bool = False
    continued_from_run_id: str | None = Field(default=None, max_length=64)
    optional_requirements_closed: bool = False
    processed_requirement_inputs: list[ProcessedRequirementInput] = Field(
        default_factory=list, max_length=20
    )
    missing_fields: list[str] = Field(default_factory=list)
    policy_result: PolicyResult | None = None
    resolved_employees: list[Participant] = Field(default_factory=list)
    availability_snapshot: AvailabilitySnapshot | None = None
    schedule_candidates: list[ScheduleCandidate] = Field(default_factory=list, max_length=3)
    selected_candidate_id: str | None = Field(default=None, max_length=64)
    unsat_analysis: UnsatAnalysis | None = None
    user_preferences: SchedulingPreferences | None = None
    operation_type: OperationType | None = None
    draft: MutationDraft | None = None
    confirmation_token: str | None = Field(default=None, max_length=80)
    draft_expires_at: datetime | None = None
    draft_tool_call_id: str | None = Field(default=None, max_length=80)
    draft_generation: int = Field(default=0, ge=0, le=100)
    confirm_tool_call_id: str | None = Field(default=None, max_length=80)
    confirm_idempotency_key: str | None = Field(default=None, max_length=80)
    pending_request_no: str | None = Field(default=None, max_length=64)
    business_result: BusinessResultCallback | None = None
    resume_action: ResumeAction | None = None
    edited_draft: EditedDraft | None = None
    citations: list[Citation] = Field(default_factory=list)
    next_route: Route | None = None
    answer_summary: str | None = Field(default=None, max_length=500)
    step_count: int = Field(default=0, ge=0, le=20)
    model_call_count: int = Field(default=0, ge=0, le=12)
    tool_call_count: int = Field(default=0, ge=0, le=16)
    loop_iteration: int = Field(default=0, ge=0, le=4)
    replan_count: int = Field(default=0, ge=0, le=2)
    executed_tool_fingerprints: list[str] = Field(default_factory=list, max_length=16)
    excluded_candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    requirement_feedback: RequirementFeedbackState | None = None
    normalization_report: NormalizationReport | None = None
    conflict_repair_feedback: ConflictRepairFeedbackState | None = None
    stop_reason: str | None = Field(default=None, max_length=64)
    model_provider: str | None = Field(default=None, max_length=32)
    configured_model: str | None = Field(default=None, max_length=128)
    response_models: list[str] = Field(default_factory=list, max_length=12)
    prompt_version: str = Field(default="meeting-agent-prompts-v7", max_length=64)
    schema_version: str = Field(default="meeting-agent-state-v6", max_length=64)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_hit_tokens: int = Field(default=0, ge=0)
    cache_miss_tokens: int = Field(default=0, ge=0)
    status: RunStatus = RunStatus.RUNNING
    error: AgentError | None = None


class AgentStreamRequest(AgentSchema):
    thread_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=1, max_length=80)
    base_run_id: str | None = Field(default=None, min_length=1, max_length=64)


class AgentInputRequest(AgentSchema):
    message: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=1, le=100)


class ToolCallEvent(AgentSchema):
    tool_call_id: str
    tool_name: str
    risk_level: str
    status: str
    summary: str
    duration_ms: int
