"""HITL, callback, trace, and persisted LangGraph state contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from app.schemas.agent_core.base import (
    AgentSchema,
    Intent,
    OperationType,
    Participant,
    Route,
    RunStatus,
    _is_shanghai_slot,
)
from app.schemas.agent_core.requirements import (
    Citation,
    MeetingRequest,
    NormalizationReport,
    PolicyResult,
    ProcessedRequirementInput,
    RequirementDraft,
    RequirementItem,
)
from app.schemas.agent_core.scheduling import (
    AvailabilitySnapshot,
    MutationDraft,
    ScheduleCandidate,
    SchedulingPreferences,
    UnsatAnalysis,
)


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
    loop_iteration: int = Field(default=0, ge=0, le=6)
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
    prompt_version: str = Field(default="meeting-agent-prompts-v11", max_length=64)
    schema_version: str = Field(default="meeting-agent-state-v7", max_length=64)
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
