"""Offline, deterministic evaluator for the versioned Day 7 corpus.

The runner exercises the structured fixture provider, the in-memory policy
retriever, and the independent OR-Tools hard-constraint validator.  It makes
no HTTP, Qdrant, Redis, or DeepSeek calls, so results are reproducible locally
and in CI.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from typing import TypeVar

from pydantic import BaseModel

from app.evaluation.corpus import EXPECTED_CATEGORY_COUNTS, load_day7_cases
from app.evaluation.models import (
    ConstraintExpectation,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationPrediction,
    EvaluationReport,
)
from app.providers.base import ModelRequest, StructuredModelRunner
from app.providers.fixture import FixtureModelProvider
from app.rag.policies import SEED_CHUNKS, InMemoryPolicyRetriever
from app.scheduling import HardConstraintValidator, ScheduleSolver
from app.schemas.agent import (
    AvailabilitySnapshot,
    Intent,
    MeetingRequest,
    PolicySelection,
    RequirementExtraction,
    RoomAvailability,
    Route,
    RunStatus,
    SchedulingProblem,
    SupervisorDecision,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class OfflineEvaluationRunner:
    """Execute component assertions against the deterministic fixture boundary."""

    def __init__(self) -> None:
        self._structured_runner = StructuredModelRunner()
        self._retriever = InMemoryPolicyRetriever()
        self._solver = ScheduleSolver()
        self._validator = HardConstraintValidator()

    def run(self, cases: tuple[EvaluationCase, ...] | None = None) -> EvaluationReport:
        resolved_cases = cases or load_day7_cases()
        results = [self._evaluate_case(case) for case in resolved_cases]
        return EvaluationReport(
            schema_version="component-fixture-evaluation-v2",
            mode="component-fixture",
            provider="FixtureModelProvider + InMemoryPolicyRetriever + ScheduleSolver",
            network_calls=0,
            generated_at=resolved_cases[0].context.now,
            limitations=[
                "This report validates the deterministic fixture baseline, not a live DeepSeek "
                "model.",
                "It does not replace the Java/Docker end-to-end smoke tests for business writes.",
                "Citation validation is limited to the versioned in-memory policy seed corpus.",
            ],
            metrics=_metrics(results),
            results=results,
        )

    def _evaluate_case(self, case: EvaluationCase) -> EvaluationCaseResult:
        provider = FixtureModelProvider(case.context.now)
        supervisor = self._invoke(
            provider=provider,
            agent_name="supervisor",
            prompt=case.input,
            output_type=SupervisorDecision,
        )
        if supervisor.route is Route.POLICY:
            return self._evaluate_policy_case(case, supervisor.route, provider)
        return self._evaluate_requirement_case(case, supervisor.route, provider)

    def _evaluate_policy_case(
        self,
        case: EvaluationCase,
        route: Route,
        provider: FixtureModelProvider,
    ) -> EvaluationCaseResult:
        selection = self._invoke(
            provider=provider,
            agent_name="policy",
            prompt=case.input,
            output_type=PolicySelection,
        )
        candidates = self._retriever.search(case.input)
        opened = self._retriever.open_candidates(
            candidates=candidates,
            selected_chunk_ids=selection.selected_chunk_ids,
        )
        citations = [chunk.citation() for chunk in opened]
        known_chunks = {chunk.chunk_id: chunk for chunk in SEED_CHUNKS}
        citations_valid = sum(
            citation.chunk_id in known_chunks
            and citation.title == known_chunks[citation.chunk_id].title
            and tuple(citation.heading_path) == known_chunks[citation.chunk_id].heading_path
            and citation.page == known_chunks[citation.chunk_id].page
            for citation in citations
        )
        expected_ids = case.expected_citation_ids
        actual_ids = [citation.chunk_id for citation in citations]
        intent_match = route is Route.POLICY and case.expected_intent is Intent.QUERY_POLICY
        tool_match = _tools_match(case, [])
        terminal_match = case.expected_terminal_status is RunStatus.SUCCEEDED
        citation_match = actual_ids == expected_ids and citations_valid == len(citations)
        prediction = EvaluationPrediction(
            supervisor_route=route,
            intent=Intent.QUERY_POLICY,
            constraints=ConstraintExpectation(),
            selected_tools=[],
            terminal_status=RunStatus.SUCCEEDED,
            citations=citations,
        )
        return EvaluationCaseResult(
            case_id=case.case_id,
            category=case.category,
            intent_match=intent_match,
            constraint_true_positive=0,
            constraint_false_positive=0,
            constraint_false_negative=0,
            tool_selection_match=tool_match,
            terminal_status_match=terminal_match,
            candidates_checked=0,
            hard_constraint_violations=0,
            citations_checked=len(citations),
            citations_valid=citations_valid,
            component_success=intent_match and tool_match and terminal_match and citation_match,
            prediction=prediction,
        )

    def _evaluate_requirement_case(
        self,
        case: EvaluationCase,
        route: Route,
        provider: FixtureModelProvider,
    ) -> EvaluationCaseResult:
        extraction = self._invoke(
            provider=provider,
            agent_name="requirement",
            prompt=case.input,
            output_type=RequirementExtraction,
        )
        prediction_constraints = _observed_constraints(extraction)
        true_positive, false_positive, false_negative = _constraint_scores(
            expected=case.expected_constraints,
            observed=prediction_constraints,
        )
        selected_tools = _selected_tools(extraction)
        terminal_status = _terminal_status(extraction)
        candidates_checked, hard_constraint_violations = self._validate_schedule(
            extraction=extraction,
            required=case.validate_schedule,
        )
        route_match = route is Route.REQUIREMENT
        intent_match = route_match and extraction.meeting_request.intent is case.expected_intent
        tool_match = _tools_match(case, selected_tools)
        terminal_match = terminal_status is case.expected_terminal_status
        constraints_match = false_positive == 0 and false_negative == 0
        hard_constraints_match = hard_constraint_violations == 0
        prediction = EvaluationPrediction(
            supervisor_route=route,
            intent=extraction.meeting_request.intent,
            constraints=prediction_constraints,
            selected_tools=selected_tools,
            terminal_status=terminal_status,
            citations=[],
        )
        return EvaluationCaseResult(
            case_id=case.case_id,
            category=case.category,
            intent_match=intent_match,
            constraint_true_positive=true_positive,
            constraint_false_positive=false_positive,
            constraint_false_negative=false_negative,
            tool_selection_match=tool_match,
            terminal_status_match=terminal_match,
            candidates_checked=candidates_checked,
            hard_constraint_violations=hard_constraint_violations,
            citations_checked=0,
            citations_valid=0,
            component_success=(
                intent_match
                and constraints_match
                and tool_match
                and terminal_match
                and hard_constraints_match
            ),
            prediction=prediction,
        )

    def _validate_schedule(
        self,
        *,
        extraction: RequirementExtraction,
        required: bool,
    ) -> tuple[int, int]:
        request = extraction.meeting_request
        if not required:
            return 0, 0
        if extraction.missing_fields or request.intent is not Intent.CREATE_MEETING:
            return 0, 1
        problem = _schedule_probe(request)
        result = self._solver.solve(problem=problem, top_k=3)
        if not result.candidates:
            return 0, 1
        violations = sum(
            len(self._validator.validate(problem=problem, candidate=candidate))
            for candidate in result.candidates
        )
        return len(result.candidates), violations

    def _invoke(
        self,
        *,
        provider: FixtureModelProvider,
        agent_name: str,
        prompt: str,
        output_type: type[ModelT],
    ) -> ModelT:
        return self._structured_runner.invoke(
            provider=provider,
            request=ModelRequest(
                agent_name=agent_name,
                system_prompt="Offline Day 7 evaluation fixture.",
                user_prompt=prompt,
                schema_name=output_type.__name__,
                schema=output_type.model_json_schema(by_alias=True),
            ),
            output_type=output_type,
        )


def run_day7_evaluation() -> EvaluationReport:
    """Run the fixed corpus and return a JSON-serialisable report."""

    return OfflineEvaluationRunner().run()


def report_as_json(report: EvaluationReport) -> str:
    """Return stable, human-readable JSON without a current-clock timestamp."""

    return json.dumps(
        report.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _observed_constraints(extraction: RequirementExtraction) -> ConstraintExpectation:
    request = extraction.meeting_request
    return ConstraintExpectation(
        duration_minutes=request.duration_minutes,
        minimum_capacity=request.minimum_capacity,
        required_features=request.required_features,
        required_participant_names=[item.name for item in request.required_participants],
        create_video_conference=request.create_video_conference,
        target_meeting_id=request.target_meeting_id,
        missing_fields=extraction.missing_fields,
    )


def _selected_tools(extraction: RequirementExtraction) -> list[str]:
    request = extraction.meeting_request
    if extraction.missing_fields:
        return []
    if request.intent is Intent.CREATE_MEETING:
        return [
            "resolve_employees",
            "get_employee_free_busy",
            "search_available_rooms",
            "create_booking_draft",
        ]
    if request.required_participants:
        return ["resolve_employees"]
    return []


def _terminal_status(extraction: RequirementExtraction) -> RunStatus:
    if extraction.missing_fields:
        return RunStatus.WAITING_USER_INPUT
    if extraction.meeting_request.intent is Intent.CREATE_MEETING:
        return RunStatus.WAITING_CONFIRMATION
    return RunStatus.SUCCEEDED


def _tools_match(case: EvaluationCase, selected_tools: list[str]) -> bool:
    selected = set(selected_tools)
    return set(case.expected_tools).issubset(selected) and not selected.intersection(
        case.forbidden_tools
    )


def _constraint_scores(
    *, expected: ConstraintExpectation, observed: ConstraintExpectation
) -> tuple[int, int, int]:
    expected_values = expected.model_dump(exclude_none=True, mode="json")
    observed_values = observed.model_dump(exclude_none=True, mode="json")
    expected_tokens = {
        _constraint_token(field, value)
        for field, value in expected_values.items()
    }
    observed_tokens = {
        _constraint_token(field, observed_values[field])
        for field in expected_values
        if field in observed_values
    }
    return (
        len(expected_tokens.intersection(observed_tokens)),
        len(observed_tokens.difference(expected_tokens)),
        len(expected_tokens.difference(observed_tokens)),
    )


def _constraint_token(field: str, value: object) -> str:
    if isinstance(value, list):
        normalized: object = sorted(value)
    else:
        normalized = value
    return f"{field}={json.dumps(normalized, ensure_ascii=False, sort_keys=True)}"


def _schedule_probe(request: MeetingRequest) -> SchedulingProblem:
    if request.time_window is None:
        raise ValueError("Day 7 schedule probe requires a time window")
    window = request.time_window
    available_slots = []
    current = window.start
    while current < window.end:
        available_slots.append(current)
        current += timedelta(minutes=30)
    room = RoomAvailability(
        room_id=999,
        room_name="Day 7 deterministic evaluation room",
        building="Evaluation",
        capacity=10_000,
        room_type="GENERAL",
        features=["LARGE_SCREEN", "WHITEBOARD", "VIDEO_CONFERENCE"],
        available_slot_starts=available_slots,
    )
    return SchedulingProblem(
        meeting_request=request,
        availability_snapshot=AvailabilitySnapshot(rooms=[room]),
        organizer_id=1001,
        required_participant_ids=[1001],
    )


def _metrics(results: list[EvaluationCaseResult]) -> EvaluationMetrics:
    total = len(results)
    category_counts = Counter(result.category for result in results)
    true_positive = sum(result.constraint_true_positive for result in results)
    false_positive = sum(result.constraint_false_positive for result in results)
    false_negative = sum(result.constraint_false_negative for result in results)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2 * precision * recall, precision + recall)
    candidates_checked = sum(result.candidates_checked for result in results)
    hard_violations = sum(result.hard_constraint_violations for result in results)
    citations_checked = sum(result.citations_checked for result in results)
    citations_valid = sum(result.citations_valid for result in results)
    return EvaluationMetrics(
        total_cases=total,
        category_counts={
            category: category_counts.get(category, 0) for category in EXPECTED_CATEGORY_COUNTS
        },
        intent_accuracy=_ratio(sum(result.intent_match for result in results), total),
        constraint_precision=precision,
        constraint_recall=recall,
        constraint_f1=f1,
        tool_selection_accuracy=_ratio(
            sum(result.tool_selection_match for result in results), total
        ),
        hard_constraint_candidates_checked=candidates_checked,
        hard_constraint_violations=hard_violations,
        hard_constraint_violation_rate=_ratio(hard_violations, candidates_checked),
        citations_checked=citations_checked,
        citations_valid=citations_valid,
        citation_validity=_ratio(citations_valid, citations_checked),
        component_task_success=_ratio(sum(result.component_success for result in results), total),
    )


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0
