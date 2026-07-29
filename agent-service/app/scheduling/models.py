"""Internal, deterministic structures used by the Day 5 scheduling node."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.schemas.agent import RoomAvailability, ScheduleCandidate, UnsatAnalysis


@dataclass(frozen=True)
class CostWeights:
    """Frozen objective weights from ``docs/04-agent-spec.md`` section 10.4."""

    optional_participant_conflict: int = 100
    preferred_time_deviation: int = 20
    building_distance: int = 10
    capacity_waste: int = 2
    preference_violation: int = 15
    room_change: int = 30


DEFAULT_COST_WEIGHTS = CostWeights()


@dataclass(frozen=True)
class CandidateSeed:
    """A hard-constraint-valid ``(room, startSlot)`` decision domain member."""

    room: RoomAvailability
    start_at: datetime
    end_at: datetime

    @property
    def candidate_id(self) -> str:
        timestamp = self.start_at.strftime("%Y%m%dT%H%M%z").replace("+", "p")
        return f"cand_{self.room.room_id}_{timestamp}"


@dataclass(frozen=True)
class CandidateSetBuildResult:
    """Candidate domain plus enough diagnostics for ordered unsat analysis."""

    seeds: tuple[CandidateSeed, ...]
    facility_room_count: int
    window_start_count: int
    required_common_start_count: int
    room_available_candidate_count: int
    required_candidate_count: int
    policy_candidate_count: int


@dataclass(frozen=True)
class HardConstraintViolation:
    code: str
    summary: str


@dataclass(frozen=True)
class ScheduleSolveResult:
    candidates: tuple[ScheduleCandidate, ...]
    unsat: UnsatAnalysis | None

    @property
    def has_solution(self) -> bool:
        return bool(self.candidates)
