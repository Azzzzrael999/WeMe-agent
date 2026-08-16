"""Deterministic candidate construction and OR-Tools Top-K schedule solving.

The solver deliberately models one meeting at a time.  It never writes Java
business data and never calls an LLM; Java remains the final transaction and
concurrency authority after a later HITL confirmation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ortools.sat.python import cp_model

from app.scheduling.models import (
    DEFAULT_COST_WEIGHTS,
    CandidateSeed,
    CandidateSetBuildResult,
    HardConstraintViolation,
    ScheduleSolveResult,
)
from app.schemas.agent import (
    BlockingInterval,
    BusyInterval,
    CandidateCostBreakdown,
    DailyTimeRange,
    RoomAvailability,
    ScheduleCandidate,
    SchedulingProblem,
    UnsatAnalysis,
    UnsatCategory,
)

SLOT_DURATION = timedelta(minutes=30)


class ScheduleSolverError(RuntimeError):
    """Raised only when a solver result fails the independent verifier."""


@dataclass(frozen=True)
class CandidateSetBuilder:
    """Build and pre-filter the finite ``(room, startSlot)`` candidate domain."""

    def build(self, problem: SchedulingProblem) -> CandidateSetBuildResult:
        starts = _window_slot_starts(problem)
        facility_rooms = [
            room for room in problem.availability_snapshot.rooms if _facility_allows(room, problem)
        ]
        required_common_starts = [
            start for start in starts if _required_participants_are_free(problem, start)
        ]
        room_available = [
            CandidateSeed(room=room, start_at=start, end_at=_candidate_end(problem, start))
            for room in facility_rooms
            for start in starts
            if _room_has_continuous_slots(room, problem, start)
        ]
        required_available = [
            seed
            for seed in room_available
            if _required_participants_are_free(problem, seed.start_at)
        ]
        policy_allowed = [
            seed for seed in required_available if _all_machine_constraints_allow(problem, seed)
        ]
        return CandidateSetBuildResult(
            seeds=tuple(policy_allowed),
            facility_room_count=len(facility_rooms),
            window_start_count=len(starts),
            required_common_start_count=len(required_common_starts),
            room_available_candidate_count=len(room_available),
            required_candidate_count=len(required_available),
            policy_candidate_count=len(policy_allowed),
        )


@dataclass(frozen=True)
class HardConstraintValidator:
    """Independent semantic validator for every OR-Tools candidate result.

    This intentionally does not consume the builder's pre-filter outcome.  It
    recalculates all hard constraints directly from ``SchedulingProblem`` so a
    future modelling mistake cannot silently return an invalid visible plan.
    """

    def validate(
        self, *, problem: SchedulingProblem, candidate: ScheduleCandidate
    ) -> tuple[HardConstraintViolation, ...]:
        violations: list[HardConstraintViolation] = []
        room = next(
            (
                value
                for value in problem.availability_snapshot.rooms
                if value.room_id == candidate.room_id
            ),
            None,
        )
        if room is None:
            return (HardConstraintViolation("ROOM_NOT_FOUND", "候选会议室不存在"),)

        request = problem.meeting_request
        window = request.time_window
        if window is None:
            return (HardConstraintViolation("TIME_WINDOW_MISSING", "缺少会议时间窗口"),)
        expected_end = candidate.start_at + timedelta(minutes=request.duration_minutes)
        if candidate.end_at != expected_end:
            violations.append(HardConstraintViolation("DURATION", "候选持续时间不符合会议时长约束"))
        if candidate.start_at < window.start or candidate.end_at > window.end:
            violations.append(HardConstraintViolation("TIME_WINDOW", "候选不在请求时间窗口内"))
        if not _validator_room_slots(room, problem, candidate.start_at):
            violations.append(
                HardConstraintViolation("ROOM_SLOT_AVAILABILITY", "会议室没有连续可用槽位")
            )
        if not _validator_required_free(problem, candidate.start_at, candidate.end_at):
            violations.append(
                HardConstraintViolation("REQUIRED_PARTICIPANT_BUSY", "必需参会者存在冲突")
            )
        if room.capacity < _attendee_count(problem):
            violations.append(HardConstraintViolation("CAPACITY", "会议室容量不足"))
        missing_features = _request_features(problem).difference(set(room.features))
        if missing_features:
            violations.append(HardConstraintViolation("FEATURE", "会议室缺少必需设备"))
        violations.extend(_validator_request_constraint_violations(problem, candidate))
        violations.extend(_validator_policy_violations(problem, room, candidate))
        return tuple(violations)

    def is_valid(self, *, problem: SchedulingProblem, candidate: ScheduleCandidate) -> bool:
        return not self.validate(problem=problem, candidate=candidate)


@dataclass(frozen=True)
class ScheduleSolver:
    """CP-SAT single-choice model with deterministic room-diverse Top-K selection."""

    builder: CandidateSetBuilder = CandidateSetBuilder()
    validator: HardConstraintValidator = HardConstraintValidator()

    def solve(self, *, problem: SchedulingProblem, top_k: int = 3) -> ScheduleSolveResult:
        if top_k < 1 or top_k > 3:
            raise ValueError("top_k must be between 1 and 3")
        built = self.builder.build(problem)
        if not built.seeds:
            return ScheduleSolveResult(candidates=(), unsat=_analyze_unsat(problem, built))

        costs = [_cost_breakdown(problem, seed) for seed in built.seeds]
        selected: list[ScheduleCandidate] = []
        selected_indexes: set[int] = set()
        selected_room_ids: set[int] = set()
        distinct_room_target = min(
            top_k, len({seed.room.room_id for seed in built.seeds})
        )

        while len(selected) < distinct_room_target:
            selected_index = _solve_candidate_index(
                seeds=built.seeds,
                costs=costs,
                excluded_indexes=selected_indexes,
                excluded_room_ids=selected_room_ids,
            )
            if selected_index is None:
                break
            candidate = _visible_candidate(built.seeds[selected_index], costs[selected_index])
            violations = self.validator.validate(problem=problem, candidate=candidate)
            if violations:
                codes = ",".join(violation.code for violation in violations)
                raise ScheduleSolverError(f"independent hard-constraint validation failed: {codes}")
            selected.append(candidate)
            selected_indexes.add(selected_index)
            selected_room_ids.add(candidate.room_id)

        while len(selected) < top_k:
            selected_index = _solve_candidate_index(
                seeds=built.seeds,
                costs=costs,
                excluded_indexes=selected_indexes,
                excluded_room_ids=set(),
            )
            if selected_index is None:
                break
            candidate = _visible_candidate(built.seeds[selected_index], costs[selected_index])
            violations = self.validator.validate(problem=problem, candidate=candidate)
            if violations:
                codes = ",".join(violation.code for violation in violations)
                raise ScheduleSolverError(f"independent hard-constraint validation failed: {codes}")
            selected.append(candidate)
            selected_indexes.add(selected_index)

        ordered = tuple(
            sorted(selected, key=lambda candidate: (candidate.total_cost, candidate.candidate_id))
        )
        return ScheduleSolveResult(candidates=ordered, unsat=None)


def _solve_candidate_index(
    *,
    seeds: tuple[CandidateSeed, ...],
    costs: list[CandidateCostBreakdown],
    excluded_indexes: set[int],
    excluded_room_ids: set[int],
) -> int | None:
    model = cp_model.CpModel()
    decisions = [model.NewBoolVar(f"candidate_{index}") for index in range(len(seeds))]
    model.Add(sum(decisions) == 1)
    for index, seed in enumerate(seeds):
        if index in excluded_indexes or seed.room.room_id in excluded_room_ids:
            model.Add(decisions[index] == 0)

    # The second term is a stable tie-breaker only. It never changes the
    # visible objective cost, because the multiplier exceeds all indexes.
    tie_multiplier = len(decisions) + 1
    model.Minimize(
        sum(
            decision * (cost.total * tie_multiplier + index)
            for index, (decision, cost) in enumerate(zip(decisions, costs, strict=True))
        )
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    if solver.StatusName(status) not in {"OPTIMAL", "FEASIBLE"}:
        return None
    return next(index for index, decision in enumerate(decisions) if solver.Value(decision) == 1)


def _window_slot_starts(problem: SchedulingProblem) -> list[datetime]:
    window = problem.meeting_request.time_window
    if window is None:
        return []
    latest_start = window.end - timedelta(minutes=problem.meeting_request.duration_minutes)
    if latest_start < window.start:
        return []
    starts: list[datetime] = []
    current = window.start
    while current <= latest_start:
        starts.append(current)
        current += SLOT_DURATION
    return starts


def _candidate_end(problem: SchedulingProblem, start: datetime) -> datetime:
    return start + timedelta(minutes=problem.meeting_request.duration_minutes)


def _candidate_slots(problem: SchedulingProblem, start: datetime) -> tuple[datetime, ...]:
    count = problem.meeting_request.duration_minutes // 30
    return tuple(start + SLOT_DURATION * index for index in range(count))


def _facility_allows(room: RoomAvailability, problem: SchedulingProblem) -> bool:
    return room.capacity >= _attendee_count(problem) and _request_features(problem).issubset(
        set(room.features)
    )


def _request_features(problem: SchedulingProblem) -> set[str]:
    return set(problem.meeting_request.required_features)


def _attendee_count(problem: SchedulingProblem) -> int:
    participant_ids = set(problem.required_participant_ids).union(problem.optional_participant_ids)
    if problem.organizer_id is not None:
        participant_ids.add(problem.organizer_id)
    explicit_names = {
        participant.name for participant in problem.meeting_request.required_participants
    }
    requested_minimum = problem.meeting_request.minimum_capacity or 0
    return max(1, len(participant_ids), len(explicit_names), requested_minimum)


def _room_has_continuous_slots(
    room: RoomAvailability, problem: SchedulingProblem, start: datetime
) -> bool:
    end = _candidate_end(problem, start)
    if any(
        _intervals_overlap(start, end, interval.start_at, interval.end_at)
        for interval in room.busy_intervals
    ):
        return False
    available_slots = room.available_slot_starts
    if available_slots is None:
        return True
    available = set(available_slots)
    return all(slot in available for slot in _candidate_slots(problem, start))


def _required_participants_are_free(problem: SchedulingProblem, start: datetime) -> bool:
    end = _candidate_end(problem, start)
    intervals_by_employee = _busy_intervals_by_employee(problem)
    for employee_id in problem.required_participant_ids:
        if any(
            _intervals_overlap(start, end, interval.start_at, interval.end_at)
            for interval in intervals_by_employee.get(employee_id, ())
        ):
            return False
    return True


def _busy_intervals_by_employee(problem: SchedulingProblem) -> dict[int, tuple[BusyInterval, ...]]:
    return {
        employee.employee_id: tuple(employee.busy_intervals)
        for employee in problem.availability_snapshot.employee_busy_slots
    }


def _intervals_overlap(
    left_start: datetime, left_end: datetime, right_start: datetime, right_end: datetime
) -> bool:
    return left_start < right_end and right_start < left_end


def _all_machine_constraints_allow(problem: SchedulingProblem, seed: CandidateSeed) -> bool:
    return _request_hard_constraints_allow(problem, seed) and _policy_allows_for_build(
        problem, seed
    )


def _request_hard_constraints_allow(problem: SchedulingProblem, seed: CandidateSeed) -> bool:
    for constraint in problem.meeting_request.hard_constraints:
        target = _parse_clock(constraint.value)
        if target is None:
            return False
        if constraint.type == "END_BEFORE" and seed.end_at.time().replace(tzinfo=None) > target:
            return False
        if constraint.type == "START_AFTER" and seed.start_at.time().replace(tzinfo=None) < target:
            return False
        if constraint.type == "START_BEFORE" and seed.start_at.time().replace(tzinfo=None) > target:
            return False
        if constraint.type not in {"END_BEFORE", "START_AFTER", "START_BEFORE"}:
            return False
    return True


def _policy_allows_for_build(problem: SchedulingProblem, seed: CandidateSeed) -> bool:
    request = problem.meeting_request
    for constraint in problem.policy_constraints:
        kind = constraint.type
        if kind == "ADVISORY_ONLY":
            continue
        if kind == "MAX_DURATION_MINUTES":
            maximum = _parse_positive_int(constraint.value)
            if maximum is None or request.duration_minutes > maximum:
                return False
        elif kind == "ALLOWED_ROOM_TYPES":
            allowed_types = _policy_tokens(constraint.value)
            if not allowed_types or seed.room.room_type not in allowed_types:
                return False
        elif kind == "REQUIRED_ROOM_FEATURE":
            required_features = _policy_tokens(constraint.value)
            if not required_features or not set(required_features).issubset(
                set(seed.room.features)
            ):
                return False
        elif kind == "DISALLOWED_TIME_WINDOW":
            daily_range = _parse_daily_range(constraint.value)
            if daily_range is None or _overlaps_daily_range(
                seed.start_at, seed.end_at, daily_range
            ):
                return False
        else:
            return False
    return True


def _cost_breakdown(problem: SchedulingProblem, seed: CandidateSeed) -> CandidateCostBreakdown:
    optional_conflicts = _optional_participant_conflict_count(problem, seed.start_at, seed.end_at)
    preferred_deviation = _preferred_time_deviation_slots(problem, seed.start_at)
    building_distance = _building_distance_score(problem, seed.room)
    capacity_waste = max(0, seed.room.capacity - _attendee_count(problem))
    preference_violations = _preference_violation_count(problem, seed.start_at, seed.end_at)
    room_changed = (
        problem.previous_room_id is not None and problem.previous_room_id != seed.room.room_id
    )
    return CandidateCostBreakdown(
        optional_participant_conflict=(
            optional_conflicts * DEFAULT_COST_WEIGHTS.optional_participant_conflict
        ),
        preferred_time_deviation=(
            preferred_deviation * DEFAULT_COST_WEIGHTS.preferred_time_deviation
        ),
        building_distance=building_distance * DEFAULT_COST_WEIGHTS.building_distance,
        capacity_waste=capacity_waste * DEFAULT_COST_WEIGHTS.capacity_waste,
        preference_violation=(preference_violations * DEFAULT_COST_WEIGHTS.preference_violation),
        room_change=DEFAULT_COST_WEIGHTS.room_change if room_changed else 0,
    )


def _optional_participant_conflict_count(
    problem: SchedulingProblem, start: datetime, end: datetime
) -> int:
    intervals_by_employee = _busy_intervals_by_employee(problem)
    return sum(
        1
        for employee_id in problem.optional_participant_ids
        if any(
            _intervals_overlap(start, end, interval.start_at, interval.end_at)
            for interval in intervals_by_employee.get(employee_id, ())
        )
    )


def _preferred_time_deviation_slots(problem: SchedulingProblem, start: datetime) -> int:
    ranges = list(problem.user_preferences.preferred_time_ranges)
    deviations = [_distance_to_daily_range_slots(start, daily_range) for daily_range in ranges]
    for constraint in problem.meeting_request.soft_constraints:
        parsed = _parse_clock(constraint.value)
        if parsed is None:
            continue
        start_minutes = start.hour * 60 + start.minute
        target_minutes = parsed.hour * 60 + parsed.minute
        if constraint.type == "START_AFTER":
            deviations.append(max(0, target_minutes - start_minutes) // 30)
        elif constraint.type == "START_BEFORE":
            deviations.append(max(0, start_minutes - target_minutes) // 30)
        elif constraint.type == "PREFER_START_AT":
            deviations.append(abs(start_minutes - target_minutes) // 30)
    return sum(deviations)


def _building_distance_score(problem: SchedulingProblem, room: RoomAvailability) -> int:
    if problem.building_distance_scores:
        return problem.building_distance_scores.get(room.building, 1)
    preferred = set(problem.user_preferences.preferred_buildings).union(
        problem.meeting_request.preferred_buildings
    )
    return 0 if not preferred or room.building in preferred else 1


def _preference_violation_count(problem: SchedulingProblem, start: datetime, end: datetime) -> int:
    preferences = problem.user_preferences
    violations = int(start.weekday() in preferences.avoid_weekdays)
    violations += sum(
        1
        for daily_range in preferences.avoid_time_ranges
        if _overlaps_daily_range(start, end, daily_range)
    )
    if preferences.preferred_time_ranges and not any(
        _overlaps_daily_range(start, end, daily_range)
        for daily_range in preferences.preferred_time_ranges
    ):
        violations += 1
    return violations


def _visible_candidate(seed: CandidateSeed, cost: CandidateCostBreakdown) -> ScheduleCandidate:
    return ScheduleCandidate(
        candidate_id=seed.candidate_id,
        room_id=seed.room.room_id,
        room_name=seed.room.room_name,
        building=seed.room.building,
        start_at=seed.start_at,
        end_at=seed.end_at,
        total_cost=cost.total,
        cost_breakdown=cost,
    )


def _analyze_unsat(problem: SchedulingProblem, built: CandidateSetBuildResult) -> UnsatAnalysis:
    """Classify infeasibility in the prescribed stable diagnostic order."""

    window = problem.meeting_request.time_window
    assert window is not None
    if built.facility_room_count == 0:
        return UnsatAnalysis(
            category=UnsatCategory.FACILITY_CAPACITY,
            summary=(
                f"{_visible_window(window.start, window.end)}、"
                f"连续 {problem.meeting_request.duration_minutes} 分钟的请求无可用方案："
                "没有同时满足容量和必需设备的会议室。"
            ),
            requested_window=window,
            duration_minutes=problem.meeting_request.duration_minutes,
            relaxation_suggestions=["降低最小容量或移除部分必需设备后重新查询。"],
        )
    # A window which cannot fit the requested duration has no meaningful
    # required-participant common-availability calculation.
    if built.window_start_count == 0:
        return UnsatAnalysis(
            category=UnsatCategory.TIME_WINDOW_DURATION,
            summary=(
                f"{_visible_window(window.start, window.end)} 无法容纳连续 "
                f"{problem.meeting_request.duration_minutes} 分钟的会议。"
            ),
            requested_window=window,
            duration_minutes=problem.meeting_request.duration_minutes,
            relaxation_suggestions=["延长时间窗口或缩短会议时长后重新查询。"],
        )
    if built.required_common_start_count == 0:
        blockers = _required_availability_blockers(problem)
        evidence = "；".join(
            f"员工 {item.resource_id} 在 {item.start_at:%H:%M}-{item.end_at:%H:%M}"
            + (f" 有会议 {item.meeting_id}" if item.meeting_id is not None else "已有安排")
            for item in blockers[:3]
        )
        return UnsatAnalysis(
            category=UnsatCategory.REQUIRED_AVAILABILITY,
            summary=(
                f"{_visible_window(window.start, window.end)}、连续 "
                f"{problem.meeting_request.duration_minutes} 分钟的请求无共同空闲时间"
                + (f"：{evidence}。" if evidence else "。")
            ),
            requested_window=window,
            duration_minutes=problem.meeting_request.duration_minutes,
            blocking_intervals=blockers,
            relaxation_suggestions=["调整时间窗口或将非关键参会者改为可选。"],
        )
    if built.room_available_candidate_count == 0 or built.required_candidate_count == 0:
        blockers = _room_availability_blockers(problem)
        return UnsatAnalysis(
            category=UnsatCategory.TIME_WINDOW_DURATION,
            summary=(
                f"{_visible_window(window.start, window.end)}、连续 "
                f"{problem.meeting_request.duration_minutes} 分钟的请求无可用会议室槽位。"
            ),
            requested_window=window,
            duration_minutes=problem.meeting_request.duration_minutes,
            blocking_intervals=blockers,
            relaxation_suggestions=["延长时间窗口或缩短会议时长后重新查询。"],
        )
    return UnsatAnalysis(
        category=UnsatCategory.POLICY,
        summary=(
            f"{_visible_window(window.start, window.end)}、连续 "
            f"{problem.meeting_request.duration_minutes} 分钟的请求被会议制度硬约束排除。"
        ),
        requested_window=window,
        duration_minutes=problem.meeting_request.duration_minutes,
        relaxation_suggestions=["调整会议类型或政策约束后重新确认。"],
    )


def _required_availability_blockers(problem: SchedulingProblem) -> list[BlockingInterval]:
    required_ids = set(problem.required_participant_ids)
    blockers: list[BlockingInterval] = []
    for employee in problem.availability_snapshot.employee_busy_slots:
        if employee.employee_id not in required_ids:
            continue
        for busy in employee.busy_intervals:
            blockers.append(
                BlockingInterval(
                    resource_type="EMPLOYEE",
                    resource_id=employee.employee_id,
                    meeting_id=busy.meeting_id,
                    start_at=busy.start_at,
                    end_at=busy.end_at,
                    reason="必需参会者已有会议",
                )
            )
    return sorted(
        blockers,
        key=lambda item: (item.start_at, item.end_at, item.resource_id or 0, item.meeting_id or 0),
    )[:10]


def _room_availability_blockers(problem: SchedulingProblem) -> list[BlockingInterval]:
    blockers = [
        BlockingInterval(
            resource_type="ROOM",
            resource_id=room.room_id,
            resource_name=room.room_name,
            meeting_id=busy.meeting_id,
            start_at=busy.start_at,
            end_at=busy.end_at,
            reason="已有其他会议占用",
        )
        for room in problem.availability_snapshot.rooms
        for busy in room.busy_intervals
    ]
    return sorted(
        blockers,
        key=lambda item: (item.start_at, item.end_at, item.resource_id or 0, item.meeting_id or 0),
    )[:10]


def _visible_window(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M}-{end:%Y-%m-%d %H:%M}"


def _validator_room_slots(
    room: RoomAvailability, problem: SchedulingProblem, start: datetime
) -> bool:
    end = _candidate_end(problem, start)
    if any(
        _intervals_overlap(start, end, interval.start_at, interval.end_at)
        for interval in room.busy_intervals
    ):
        return False
    if room.available_slot_starts is None:
        return True
    available_slots = set(room.available_slot_starts)
    for offset in range(problem.meeting_request.duration_minutes // 30):
        if start + SLOT_DURATION * offset not in available_slots:
            return False
    return True


def _validator_required_free(problem: SchedulingProblem, start: datetime, end: datetime) -> bool:
    intervals_by_employee = {
        employee.employee_id: employee.busy_intervals
        for employee in problem.availability_snapshot.employee_busy_slots
    }
    for employee_id in problem.required_participant_ids:
        for busy in intervals_by_employee.get(employee_id, []):
            if start < busy.end_at and busy.start_at < end:
                return False
    return True


def _validator_policy_violations(
    problem: SchedulingProblem, room: RoomAvailability, candidate: ScheduleCandidate
) -> list[HardConstraintViolation]:
    violations: list[HardConstraintViolation] = []
    for constraint in problem.policy_constraints:
        if constraint.type == "ADVISORY_ONLY":
            continue
        if constraint.type == "MAX_DURATION_MINUTES":
            maximum = _parse_positive_int(constraint.value)
            if maximum is None or problem.meeting_request.duration_minutes > maximum:
                violations.append(
                    HardConstraintViolation("POLICY_MAX_DURATION", "会议时长不符合制度")
                )
        elif constraint.type == "ALLOWED_ROOM_TYPES":
            allowed = _policy_tokens(constraint.value)
            if not allowed or room.room_type not in allowed:
                violations.append(
                    HardConstraintViolation("POLICY_ROOM_TYPE", "会议室类型不符合制度")
                )
        elif constraint.type == "REQUIRED_ROOM_FEATURE":
            required = _policy_tokens(constraint.value)
            if not required or not set(required).issubset(set(room.features)):
                violations.append(HardConstraintViolation("POLICY_FEATURE", "会议室设备不符合制度"))
        elif constraint.type == "DISALLOWED_TIME_WINDOW":
            daily_range = _parse_daily_range(constraint.value)
            if daily_range is None or _overlaps_daily_range(
                candidate.start_at, candidate.end_at, daily_range
            ):
                violations.append(
                    HardConstraintViolation("POLICY_TIME_WINDOW", "会议时间不符合制度")
                )
        else:
            violations.append(HardConstraintViolation("POLICY_INVALID", "存在不可执行的制度约束"))
    return violations


def _validator_request_constraint_violations(
    problem: SchedulingProblem, candidate: ScheduleCandidate
) -> list[HardConstraintViolation]:
    """Independent check for bounded MeetingRequest hard clock constraints."""

    violations: list[HardConstraintViolation] = []
    for constraint in problem.meeting_request.hard_constraints:
        target = _parse_clock(constraint.value)
        if target is None:
            violations.append(
                HardConstraintViolation("HARD_CONSTRAINT_INVALID", "硬约束时间格式无法执行")
            )
        elif constraint.type == "END_BEFORE":
            if candidate.end_at.time().replace(tzinfo=None) > target:
                violations.append(HardConstraintViolation("END_BEFORE", "会议结束时间超过硬约束"))
        elif constraint.type == "START_AFTER":
            if candidate.start_at.time().replace(tzinfo=None) < target:
                violations.append(HardConstraintViolation("START_AFTER", "会议开始时间早于硬约束"))
        elif constraint.type == "START_BEFORE":
            if candidate.start_at.time().replace(tzinfo=None) > target:
                violations.append(HardConstraintViolation("START_BEFORE", "会议开始时间晚于硬约束"))
        else:
            violations.append(
                HardConstraintViolation("HARD_CONSTRAINT_UNSUPPORTED", "存在不可执行的硬约束")
            )
    return violations


def _policy_tokens(value: str) -> tuple[str, ...]:
    value = value.strip()
    if not value:
        return ()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            return ()
        return tuple(item.strip() for item in parsed if item.strip())
    return tuple(token for token in re.split(r"[,|\s]+", value) if token)


def _parse_positive_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_clock(value: str) -> time | None:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def _parse_daily_range(value: str) -> tuple[time, time] | None:
    parts = value.split("-", maxsplit=1)
    if len(parts) != 2:
        return None
    start = _parse_clock(parts[0].strip())
    end = _parse_clock(parts[1].strip())
    return (start, end) if start is not None and end is not None else None


def _overlaps_daily_range(
    candidate_start: datetime,
    candidate_end: datetime,
    daily_range: DailyTimeRange | tuple[time, time],
) -> bool:
    if isinstance(daily_range, DailyTimeRange):
        parsed = _parse_daily_range(f"{daily_range.start}-{daily_range.end}")
        if parsed is None:
            return False
        daily_range = parsed
    range_start_time, range_end_time = daily_range
    day = candidate_start.date() - timedelta(days=1)
    last_day = candidate_end.date()
    while day <= last_day:
        range_start = datetime.combine(day, range_start_time, tzinfo=candidate_start.tzinfo)
        range_end = datetime.combine(day, range_end_time, tzinfo=candidate_start.tzinfo)
        if range_end <= range_start:
            range_end += timedelta(days=1)
        if _intervals_overlap(candidate_start, candidate_end, range_start, range_end):
            return True
        day += timedelta(days=1)
    return False


def _distance_to_daily_range_slots(start: datetime, daily_range: DailyTimeRange) -> int:
    # The caller only supplies DailyTimeRange values. Keeping this parser here
    # avoids turning a user preference into a hard constraint if it is malformed.
    parsed = _parse_daily_range(f"{daily_range.start}-{daily_range.end}")
    if parsed is None:
        return 0
    range_start, range_end = parsed
    current = start.time().replace(tzinfo=None)
    if range_start <= range_end:
        if range_start <= current <= range_end:
            return 0
        if current < range_start:
            return _minutes_to_slots(_minutes_between(current, range_start))
        return _minutes_to_slots(_minutes_between(range_end, current))
    if current >= range_start or current <= range_end:
        return 0
    return min(
        _minutes_to_slots(_minutes_between(current, range_start)),
        _minutes_to_slots(_minutes_between(range_end, current)),
    )


def _minutes_between(left: time, right: time) -> int:
    return abs((right.hour * 60 + right.minute) - (left.hour * 60 + left.minute))


def _minutes_to_slots(minutes: int) -> int:
    return (minutes + 29) // 30
