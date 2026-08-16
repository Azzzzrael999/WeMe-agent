"""Deterministic scheduling components.

This package contains candidate construction, the OR-Tools single-selection
model, and an independent post-solve hard-constraint validator.  It is a
deterministic node, not another runtime Agent.
"""

from app.scheduling.models import ScheduleSolveResult
from app.scheduling.solver import (
    CandidateSetBuilder,
    HardConstraintValidator,
    ScheduleSolver,
    ScheduleSolverError,
)

__all__ = [
    "CandidateSetBuilder",
    "HardConstraintValidator",
    "ScheduleSolveResult",
    "ScheduleSolver",
    "ScheduleSolverError",
]
