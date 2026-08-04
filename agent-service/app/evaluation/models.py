"""Schema for the deterministic Day 7 offline Agent evaluation report."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.schemas.agent import AgentSchema, Citation, Intent, Route, RunStatus


class EvaluationCategory(StrEnum):
    NORMAL_BOOKING = "NORMAL_BOOKING"
    MULTI_PARTY_COORDINATION = "MULTI_PARTY_COORDINATION"
    COMPLEX_CONSTRAINT = "COMPLEX_CONSTRAINT"
    RECOMMENDATION_OR_CONFLICT = "RECOMMENDATION_OR_CONFLICT"
    POLICY = "POLICY"
    MODIFY_OR_CANCEL = "MODIFY_OR_CANCEL"
    PREFERENCE_OR_CLARIFICATION = "PREFERENCE_OR_CLARIFICATION"


class EvaluationContext(AgentSchema):
    now: datetime
    user_id: int = Field(ge=1)


class ConstraintExpectation(AgentSchema):
    """Only fields explicitly annotated for a corpus case are scored."""

    duration_minutes: int | None = Field(default=None, ge=30, le=480, multiple_of=30)
    minimum_capacity: int | None = Field(default=None, ge=1, le=10_000)
    required_features: list[str] | None = None
    required_participant_names: list[str] | None = None
    target_meeting_id: int | None = Field(default=None, ge=1)
    missing_fields: list[str] | None = None


class EvaluationCase(AgentSchema):
    case_id: str = Field(pattern=r"^[a-z]+-[0-9]{3}$")
    category: EvaluationCategory
    input: str = Field(min_length=1, max_length=500)
    context: EvaluationContext
    expected_intent: Intent
    expected_constraints: ConstraintExpectation = Field(default_factory=ConstraintExpectation)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_terminal_status: RunStatus
    expected_citation_ids: list[str] = Field(default_factory=list)
    validate_schedule: bool = False


class EvaluationPrediction(AgentSchema):
    supervisor_route: Route
    intent: Intent
    constraints: ConstraintExpectation
    selected_tools: list[str] = Field(default_factory=list)
    terminal_status: RunStatus
    citations: list[Citation] = Field(default_factory=list)


class EvaluationCaseResult(AgentSchema):
    case_id: str
    category: EvaluationCategory
    intent_match: bool
    constraint_true_positive: int = Field(ge=0)
    constraint_false_positive: int = Field(ge=0)
    constraint_false_negative: int = Field(ge=0)
    tool_selection_match: bool
    terminal_status_match: bool
    candidates_checked: int = Field(ge=0)
    hard_constraint_violations: int = Field(ge=0)
    citations_checked: int = Field(ge=0)
    citations_valid: int = Field(ge=0)
    component_success: bool
    prediction: EvaluationPrediction


class EvaluationMetrics(AgentSchema):
    total_cases: int = Field(ge=0)
    category_counts: dict[EvaluationCategory, int]
    intent_accuracy: float = Field(ge=0, le=1)
    constraint_precision: float = Field(ge=0, le=1)
    constraint_recall: float = Field(ge=0, le=1)
    constraint_f1: float = Field(ge=0, le=1)
    tool_selection_accuracy: float = Field(ge=0, le=1)
    hard_constraint_candidates_checked: int = Field(ge=0)
    hard_constraint_violations: int = Field(ge=0)
    hard_constraint_violation_rate: float = Field(ge=0, le=1)
    citations_checked: int = Field(ge=0)
    citations_valid: int = Field(ge=0)
    citation_validity: float = Field(ge=0, le=1)
    component_task_success: float = Field(ge=0, le=1)


class EvaluationReport(AgentSchema):
    schema_version: str
    mode: str
    provider: str
    network_calls: int = Field(ge=0)
    generated_at: datetime
    limitations: list[str]
    metrics: EvaluationMetrics
    results: list[EvaluationCaseResult]
