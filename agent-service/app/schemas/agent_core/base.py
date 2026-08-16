"""Structured contracts exchanged by WeMe's four runtime agents.

The models deliberately use ``extra='forbid'``.  They are used both for
model-output validation and for the state passed between LangGraph nodes, so
an unexpected field cannot quietly change a business decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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


def _is_shanghai_datetime(value: datetime) -> bool:
    """Return whether an external timestamp carries the frozen +08:00 offset."""

    return value.tzinfo is not None and value.utcoffset() == timedelta(hours=8)


def _strip_required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("text must not be blank")
    return normalized


def _strip_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


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
