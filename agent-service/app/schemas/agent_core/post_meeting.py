"""Post-meeting transcript analysis and draft contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.agent_core.base import (
    AgentSchema,
    _is_shanghai_datetime,
    _strip_optional_text,
    _strip_required_text,
)


class PostMeetingParticipant(AgentSchema):
    employee_id: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=64)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return _strip_required_text(value)


class PostMeetingDraftRequest(AgentSchema):
    """Java-authenticated meeting snapshot plus a bounded text record."""

    meeting_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=128)
    meeting_type: str = Field(min_length=1, max_length=32)
    start_at: datetime
    end_at: datetime
    participants: list[PostMeetingParticipant] = Field(min_length=1, max_length=100)
    transcript: str = Field(min_length=1, max_length=20_000)

    @field_validator("title", "meeting_type", "transcript")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _strip_required_text(value)

    @model_validator(mode="after")
    def validate_snapshot(self) -> PostMeetingDraftRequest:
        if not _is_shanghai_datetime(self.start_at) or not _is_shanghai_datetime(self.end_at):
            raise ValueError("meeting timestamps must use the Asia/Shanghai offset")
        if self.end_at <= self.start_at:
            raise ValueError("meeting endAt must be after startAt")
        employee_ids = [item.employee_id for item in self.participants]
        if len(employee_ids) != len(set(employee_ids)):
            raise ValueError("participants must have unique employee IDs")
        return self


class PostMeetingMinutes(AgentSchema):
    background: str = Field(min_length=1, max_length=2_000)
    discussion_summary: str = Field(min_length=1, max_length=10_000)
    conclusion: str = Field(min_length=1, max_length=2_000)

    @field_validator("background", "discussion_summary", "conclusion")
    @classmethod
    def normalize_minutes_text(cls, value: str) -> str:
        return _strip_required_text(value)


class PostMeetingDecision(AgentSchema):
    content: str = Field(min_length=1, max_length=1_000)
    rationale: str | None = Field(default=None, max_length=1_000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        return _strip_required_text(value)

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str | None) -> str | None:
        return _strip_optional_text(value)


class PostMeetingActionItem(AgentSchema):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    assignee_employee_id: int | None = Field(default=None, ge=1)
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return _strip_required_text(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _strip_optional_text(value)

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and not _is_shanghai_datetime(value):
            raise ValueError("action item dueAt must use the Asia/Shanghai offset")
        return value


class PostMeetingDraft(AgentSchema):
    minutes: PostMeetingMinutes
    decisions: list[PostMeetingDecision] = Field(default_factory=list, max_length=20)
    action_items: list[PostMeetingActionItem] = Field(default_factory=list, max_length=50)


class PostMeetingDraftResponse(AgentSchema):
    agent_run_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt_version: Literal["post-meeting-analysis-v1"] = "post-meeting-analysis-v1"
    schema_version: Literal["post-meeting-draft-v1"] = "post-meeting-draft-v1"
    draft: PostMeetingDraft
