"""Deterministic Day 5 OR-Tools tests; no model provider is involved."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.scheduling import HardConstraintValidator, ScheduleSolver
from app.schemas.agent import (
    AvailabilitySnapshot,
    BusyInterval,
    CandidateCostBreakdown,
    Constraint,
    DailyTimeRange,
    EmployeeBusySlots,
    Intent,
    MeetingRequest,
    Participant,
    PolicyConstraint,
    RoomAvailability,
    ScheduleCandidate,
    SchedulingPreferences,
    SchedulingProblem,
    TimeWindow,
    UnsatCategory,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 19, hour, minute, tzinfo=SHANGHAI)


def room(
    room_id: int = 101,
    *,
    capacity: int = 4,
    features: list[str] | None = None,
    room_type: str = "STANDARD",
    busy_intervals: list[BusyInterval] | None = None,
    available_slot_starts: list[datetime] | None = None,
) -> RoomAvailability:
    return RoomAvailability(
        room_id=room_id,
        room_name=f"研发楼 {room_id}",
        building="研发楼",
        capacity=capacity,
        room_type=room_type,
        features=features if features is not None else ["LARGE_SCREEN"],
        busy_intervals=busy_intervals or [],
        available_slot_starts=available_slot_starts,
    )


def problem(
    *,
    rooms: list[RoomAvailability] | None = None,
    busy_slots: list[EmployeeBusySlots] | None = None,
    duration_minutes: int = 60,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    required_features: list[str] | None = None,
    hard_constraints: list[Constraint] | None = None,
    policy_constraints: list[PolicyConstraint] | None = None,
    optional_participant_ids: list[int] | None = None,
    preferences: SchedulingPreferences | None = None,
) -> SchedulingProblem:
    return SchedulingProblem(
        meeting_request=MeetingRequest(
            intent=Intent.CREATE_MEETING,
            title="架构评审",
            meeting_type="ARCHITECTURE_REVIEW",
            duration_minutes=duration_minutes,
            time_window=TimeWindow(start=window_start or at(13), end=window_end or at(15)),
            required_participants=[Participant(name="李四", employee_id=1002)],
            required_features=required_features
            if required_features is not None
            else ["LARGE_SCREEN"],
            minimum_capacity=2,
            hard_constraints=hard_constraints or [],
        ),
        availability_snapshot=AvailabilitySnapshot(
            rooms=rooms if rooms is not None else [room()],
            employee_busy_slots=busy_slots or [],
        ),
        organizer_id=1001,
        required_participant_ids=[1002],
        optional_participant_ids=optional_participant_ids or [],
        policy_constraints=policy_constraints or [],
        user_preferences=preferences or SchedulingPreferences(),
    )


def test_single_feasible_candidate_is_returned_and_independently_validated() -> None:
    schedule_problem = problem(
        rooms=[room(available_slot_starts=[at(13), at(13, 30)])],
        window_end=at(14),
    )

    result = ScheduleSolver().solve(problem=schedule_problem)

    assert result.unsat is None
    assert [
        (candidate.room_id, candidate.start_at, candidate.end_at) for candidate in result.candidates
    ] == [(101, at(13), at(14))]
    assert HardConstraintValidator().is_valid(
        problem=schedule_problem, candidate=result.candidates[0]
    )


def test_multi_room_solution_prefers_lower_capacity_waste_cost() -> None:
    schedule_problem = problem(
        rooms=[
            room(101, capacity=4, available_slot_starts=[at(13), at(13, 30)]),
            room(102, capacity=10, available_slot_starts=[at(13), at(13, 30)]),
        ],
        window_end=at(14),
    )

    candidates = ScheduleSolver().solve(problem=schedule_problem, top_k=2).candidates

    assert [candidate.room_id for candidate in candidates] == [101, 102]
    assert candidates[0].cost_breakdown.capacity_waste == 4
    assert candidates[1].cost_breakdown.capacity_waste == 16


def test_required_conflict_is_never_returned_but_optional_conflict_is_penalized() -> None:
    required_busy = EmployeeBusySlots(
        employee_id=1002,
        busy_intervals=[BusyInterval(start_at=at(13), end_at=at(14))],
    )
    required_problem = problem(busy_slots=[required_busy])

    required_result = ScheduleSolver().solve(problem=required_problem)

    assert [candidate.start_at for candidate in required_result.candidates] == [at(14)]
    assert all(
        HardConstraintValidator().is_valid(problem=required_problem, candidate=candidate)
        for candidate in required_result.candidates
    )

    optional_busy = EmployeeBusySlots(
        employee_id=1003,
        busy_intervals=[BusyInterval(start_at=at(13), end_at=at(14))],
    )
    optional_problem = problem(busy_slots=[optional_busy], optional_participant_ids=[1003])

    optional_result = ScheduleSolver().solve(problem=optional_problem)

    assert optional_result.candidates[0].start_at == at(14)
    by_start = {candidate.start_at: candidate for candidate in optional_result.candidates}
    assert by_start[at(13)].cost_breakdown.optional_participant_conflict == 100


def test_capacity_and_feature_failures_are_prefiltered() -> None:
    schedule_problem = problem(
        rooms=[
            room(101, capacity=1, available_slot_starts=[at(13), at(13, 30)]),
            room(
                102,
                capacity=4,
                features=[],
                available_slot_starts=[at(13), at(13, 30)],
            ),
            room(103, capacity=4, available_slot_starts=[at(13), at(13, 30)]),
        ],
        window_end=at(14),
    )

    result = ScheduleSolver().solve(problem=schedule_problem)

    assert [candidate.room_id for candidate in result.candidates] == [103]


def test_ninety_minute_meeting_requires_three_continuous_slots() -> None:
    schedule_problem = problem(
        rooms=[room(available_slot_starts=[at(13), at(13, 30), at(14)])],
        duration_minutes=90,
        window_end=at(15),
    )

    result = ScheduleSolver().solve(problem=schedule_problem)

    assert [(candidate.start_at, candidate.end_at) for candidate in result.candidates] == [
        (at(13), at(14, 30))
    ]


def test_preferred_time_range_is_scored_before_other_equal_candidates() -> None:
    schedule_problem = problem(
        window_end=at(17),
        preferences=SchedulingPreferences(
            preferred_time_ranges=[DailyTimeRange(start="15:00", end="17:00")]
        ),
    )

    result = ScheduleSolver().solve(problem=schedule_problem)

    assert result.candidates[0].start_at == at(15)
    assert result.candidates[0].cost_breakdown.preferred_time_deviation == 0


def test_top_three_candidates_are_unique_and_cost_sorted() -> None:
    schedule_problem = problem(duration_minutes=30)

    result = ScheduleSolver().solve(problem=schedule_problem)

    assert len(result.candidates) == 3
    assert [candidate.start_at for candidate in result.candidates] == [at(13), at(13, 30), at(14)]
    assert len({candidate.candidate_id for candidate in result.candidates}) == 3
    assert [candidate.total_cost for candidate in result.candidates] == sorted(
        candidate.total_cost for candidate in result.candidates
    )


def test_unsat_categories_follow_stable_diagnostic_order() -> None:
    facility_result = ScheduleSolver().solve(problem=problem(rooms=[room(capacity=1)]))
    assert facility_result.unsat is not None
    assert facility_result.unsat.category is UnsatCategory.FACILITY_CAPACITY

    required_result = ScheduleSolver().solve(
        problem=problem(
            busy_slots=[
                EmployeeBusySlots(
                    employee_id=1002,
                    busy_intervals=[
                        BusyInterval(meeting_id=9002, start_at=at(13), end_at=at(15))
                    ],
                )
            ]
        )
    )
    assert required_result.unsat is not None
    assert required_result.unsat.category is UnsatCategory.REQUIRED_AVAILABILITY
    assert required_result.unsat.requested_window == TimeWindow(start=at(13), end=at(15))
    assert required_result.unsat.duration_minutes == 60
    assert required_result.unsat.blocking_intervals[0].resource_id == 1002
    assert required_result.unsat.blocking_intervals[0].meeting_id == 9002

    short_window_result = ScheduleSolver().solve(
        problem=problem(duration_minutes=90, window_end=at(14))
    )
    assert short_window_result.unsat is not None
    assert short_window_result.unsat.category is UnsatCategory.TIME_WINDOW_DURATION

    room_slot_loss_result = ScheduleSolver().solve(
        problem=problem(rooms=[room(available_slot_starts=[at(13)])], window_end=at(14))
    )
    assert room_slot_loss_result.unsat is not None
    assert room_slot_loss_result.unsat.category is UnsatCategory.TIME_WINDOW_DURATION

    policy_result = ScheduleSolver().solve(
        problem=problem(
            policy_constraints=[PolicyConstraint(type="ALLOWED_ROOM_TYPES", value="VIP")]
        )
    )
    assert policy_result.unsat is not None
    assert policy_result.unsat.category is UnsatCategory.POLICY


def test_room_partial_occupancy_keeps_later_continuous_candidates() -> None:
    result = ScheduleSolver().solve(
        problem=problem(
            rooms=[
                room(
                    busy_intervals=[
                        BusyInterval(meeting_id=9100, start_at=at(13), end_at=at(14))
                    ]
                )
            ],
            window_end=at(16),
        )
    )

    assert result.unsat is None
    assert result.candidates
    assert result.candidates[0].start_at == at(14)


def test_room_occupancy_unsat_contains_room_time_and_meeting_evidence() -> None:
    result = ScheduleSolver().solve(
        problem=problem(
            rooms=[
                room(
                    room_id=109,
                    busy_intervals=[
                        BusyInterval(meeting_id=9101, start_at=at(13), end_at=at(15))
                    ],
                )
            ]
        )
    )

    assert result.unsat is not None
    assert result.unsat.category is UnsatCategory.TIME_WINDOW_DURATION
    blocker = result.unsat.blocking_intervals[0]
    assert blocker.resource_type == "ROOM"
    assert blocker.resource_id == 109
    assert blocker.resource_name == "研发楼 109"
    assert blocker.meeting_id == 9101
    assert blocker.start_at == at(13)
    assert blocker.end_at == at(15)


def test_machine_executable_hard_constraints_are_enforced_twice() -> None:
    schedule_problem = problem(
        window_end=at(17),
        hard_constraints=[Constraint(type="END_BEFORE", value="14:30")],
    )

    result = ScheduleSolver().solve(problem=schedule_problem)

    assert [candidate.start_at for candidate in result.candidates] == [at(13), at(13, 30)]
    forbidden_candidate = ScheduleCandidate(
        candidate_id="cand_manual_forbidden",
        room_id=101,
        room_name="研发楼 101",
        building="研发楼",
        start_at=at(14),
        end_at=at(15),
        total_cost=0,
        cost_breakdown=CandidateCostBreakdown(),
    )
    assert {
        violation.code
        for violation in HardConstraintValidator().validate(
            problem=schedule_problem, candidate=forbidden_candidate
        )
    } == {"END_BEFORE"}

    unsupported_result = ScheduleSolver().solve(
        problem=problem(hard_constraints=[Constraint(type="UNSUPPORTED", value="x")])
    )
    assert unsupported_result.unsat is not None
    assert unsupported_result.unsat.category is UnsatCategory.POLICY
