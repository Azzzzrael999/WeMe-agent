"""Structured contracts exchanged by the four Day 4 agents.

The models deliberately use ``extra='forbid'``.  They are used both for
model-output validation and for the state passed between LangGraph nodes, so
an unexpected field cannot quietly change a business decision.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    create_video_conference: bool = False
    target_meeting_id: int | None = Field(default=None, ge=1)


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
    summary: str = Field(min_length=1, max_length=240)


class RequirementExtraction(AgentSchema):
    meeting_request: MeetingRequest
    missing_fields: list[str] = Field(default_factory=list, max_length=10)
    needs_policy: bool = False
    summary: str = Field(min_length=1, max_length=240)


class PolicySelection(AgentSchema):
    answer_summary: str = Field(min_length=1, max_length=500)
    selected_chunk_ids: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    constraints: list[PolicyConstraint] = Field(default_factory=list, max_length=10)


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
            raise ValueError("Day 4 scheduling only allows Java READ tools")
        return tool_names


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
    intent: Intent | None = None
    meeting_request: MeetingRequest | None = None
    missing_fields: list[str] = Field(default_factory=list)
    policy_result: PolicyResult | None = None
    resolved_employees: list[Participant] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    next_route: Route | None = None
    answer_summary: str | None = Field(default=None, max_length=500)
    step_count: int = Field(default=0, ge=0, le=20)
    model_call_count: int = Field(default=0, ge=0, le=8)
    tool_call_count: int = Field(default=0, ge=0, le=12)
    status: RunStatus = RunStatus.RUNNING
    error: AgentError | None = None


class AgentStreamRequest(AgentSchema):
    thread_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=1, max_length=80)


class ToolCallEvent(AgentSchema):
    tool_call_id: str
    tool_name: str
    risk_level: str
    status: str
    summary: str
    duration_ms: int
