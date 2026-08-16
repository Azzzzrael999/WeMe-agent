"""Scheduling problem, candidate, availability, and draft contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.agent_core.base import AgentSchema, OperationType, _is_shanghai_slot
from app.schemas.agent_core.requirements import MeetingRequest, PolicyConstraint, TimeWindow


class CandidateCostBreakdown(AgentSchema):
    """Weighted, user-visible cost components for one schedule candidate.

    Each value already includes the fixed weight from the OR-Tools
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
    """A candidate safe for the ``plan.candidates`` SSE event."""

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
