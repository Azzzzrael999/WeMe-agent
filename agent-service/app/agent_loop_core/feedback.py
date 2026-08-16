"""Loop stop reasons and structured evaluator feedback."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.schemas.agent import (
    AgentSchema,
)


class LoopStopReason(StrEnum):
    READY_FOR_CONFIRMATION = "READY_FOR_CONFIRMATION"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    NO_SOLUTION = "NO_SOLUTION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    WAITING_BUSINESS_RESULT = "WAITING_BUSINESS_RESULT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RequirementFeedback(AgentSchema):
    codes: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=500)
    repairable: bool


class ConflictRepairFeedback(AgentSchema):
    conflict_type: str = Field(min_length=1, max_length=64)
    failed_candidate_id: str = Field(min_length=1, max_length=64)
    preserved_constraints: list[str] = Field(min_length=1, max_length=20)
    excluded_candidate_ids: list[str] = Field(min_length=1, max_length=3)
    replan_count: int = Field(ge=1, le=2)
    room_id: int | None = Field(default=None, ge=1)
    slots: list[int] = Field(default_factory=list, max_length=48)
    reason: str = Field(min_length=1, max_length=240)


class RouteFeedback(AgentSchema):
    codes: list[str] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=1, max_length=300)
