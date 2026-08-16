"""Requirement extraction, policy, and clarification contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.agent_core.base import (
    AgentSchema,
    EvidenceProvenance,
    Intent,
    Participant,
    RequirementSlotStatus,
    Route,
)


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
    includes_current_user: bool = False
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
    includes_current_user: bool = False
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
            includes_current_user=draft.includes_current_user,
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
    selected_chunk_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)
    constraints: list[PolicyConstraint] = Field(default_factory=list, max_length=10)
