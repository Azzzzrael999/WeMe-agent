"""Real DeepSeek component evaluation with trajectory results kept separate."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.agent_loop import READ_TOOL_DEFINITIONS
from app.config import Settings
from app.evaluation.corpus import CASES, DATASET_VERSION
from app.evaluation.models import ConstraintExpectation, EvaluationCase
from app.evaluation.runner import _constraint_scores
from app.providers.base import (
    StructuredModelRunner,
    ToolLoopMessage,
    ToolModelRequest,
)
from app.providers.deepseek import DeepSeekModelProvider
from app.rag.policies import SEED_CHUNKS, InMemoryPolicyRetriever
from app.schemas.agent import AgentState, Intent, Route
from app.tools.java import ResolveEmployeesInput
from app.workflow import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    PolicyAgent,
    RequirementAgent,
    SupervisorAgent,
    WorkflowError,
)

CORE_CASE_IDS = (
    "normal-001",
    "normal-003",
    "normal-005",
    "normal-006",
    "normal-010",
    "normal-012",
    "normal-020",
    "coord-002",
    "coord-005",
    "coord-012",
    "complex-004",
    "complex-008",
    "complex-014",
    "recommend-001",
    "recommend-002",
    "recommend-003",
    "recommend-006",
    "recommend-008",
    "recommend-011",
    "policy-001",
    "policy-002",
    "policy-006",
    "policy-012",
    "change-001",
    "change-004",
    "change-008",
    "change-012",
    "change-018",
    "preference-001",
    "preference-010",
)
_CASES_BY_ID = {case.case_id: case for case in CASES}
CORE_CASES: tuple[EvaluationCase, ...] = tuple(_CASES_BY_ID[case_id] for case_id in CORE_CASE_IDS)


def run_live_evaluation(
    *,
    mode: Literal["component", "trajectory"],
    suite: Literal["core", "full"],
    repeats: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    # Component evaluation never touches persistence. This safe local value
    # only satisfies the shared Settings schema in a standalone process.
    config = settings or Settings.model_validate({"AGENT_DATABASE_URL": "sqlite+pysqlite://"})
    base = {
        "schemaVersion": "live-model-component-v3",
        "datasetVersion": DATASET_VERSION,
        "mode": f"live-model-{mode}",
        "suite": suite,
        "repeats": repeats,
        "generatedAt": datetime.now(UTC).isoformat(),
        "provider": "deepseek",
        "configuredModel": config.deepseek_model or None,
        "promptVersion": PROMPT_VERSION,
        "agentSchemaVersion": SCHEMA_VERSION,
    }
    if mode == "trajectory":
        return {
            **base,
            "status": "SKIPPED",
            "reason": "Trajectory requires the public Java SSE runner and signed safety context.",
            "metrics": {},
            "results": [],
        }
    if not config.deepseek_is_configured:
        return {
            **base,
            "status": "SKIPPED",
            "reason": "DeepSeek provider is not fully configured.",
            "metrics": {
                "samples": 0,
                "uniqueCases": len(CORE_CASES if suite == "core" else CASES),
                "taskSuccessRate": None,
                "stableCaseRate": None,
            },
            "results": [],
        }

    cases = list(CORE_CASES if suite == "core" else CASES)
    if suite == "core" and len(cases) != 30:
        raise ValueError(f"live core suite must contain 30 cases, got {len(cases)}")
    if suite == "full" and len(cases) != 120:
        raise ValueError(f"live full suite must contain 120 cases, got {len(cases)}")

    provider = DeepSeekModelProvider(config)
    runner = StructuredModelRunner()
    supervisor = SupervisorAgent(provider=provider, runner=runner)
    requirement = RequirementAgent(provider=provider, runner=runner)
    policy = PolicyAgent(
        provider=provider,
        runner=runner,
        retriever=InMemoryPolicyRetriever(),
    )
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    route_matches = 0
    intent_matches = 0
    constraint_tp = constraint_fp = constraint_fn = 0
    tool_matches = 0
    source_violations = 0
    citations_checked = citations_valid = 0
    response_models: set[str] = set()
    token_usage = _empty_usage()

    for repeat in range(1, repeats + 1):
        for case in cases:
            started = time.perf_counter()
            failure: str | None = None
            route_match = intent_match = tool_match = False
            case_tp = case_fp = case_fn = 0
            case_source_violations = 0
            case_citations_checked = case_citations_valid = 0
            terminal = "ERROR"
            state = _initial_state(case, repeat, config)
            try:
                state, _, _ = supervisor.execute(state)
                expected_route = (
                    Route.POLICY
                    if case.expected_intent is Intent.QUERY_POLICY
                    else Route.REQUIREMENT
                )
                route_match = state.next_route is expected_route
                if expected_route is Route.POLICY:
                    state, _, _ = policy.execute(state)
                    intent_match = state.intent is Intent.QUERY_POLICY
                    actual_ids = [citation.chunk_id for citation in state.citations]
                    case_citations_checked = len(actual_ids)
                    known_ids = {chunk.chunk_id for chunk in SEED_CHUNKS}
                    expected_ids = set(case.expected_citation_ids)
                    relevant = not expected_ids or bool(expected_ids.intersection(actual_ids))
                    case_citations_valid = (
                        len(actual_ids)
                        if actual_ids and relevant and all(item in known_ids for item in actual_ids)
                        else 0
                    )
                    tool_match = not case.expected_tools
                else:
                    state, _, _, _ = requirement.execute(state)
                    intent_match = state.intent is case.expected_intent
                    observed = _observed_constraints(state)
                    case_tp, case_fp, case_fn = _constraint_scores(
                        expected=_scored_expectation(case),
                        observed=observed,
                    )
                    actual_tools = _selected_tools(state)
                    expected_tools = _expected_tools(case, state=state)
                    tool_match = actual_tools == expected_tools and not set(
                        case.forbidden_tools
                    ).intersection(actual_tools)
                    feedback = state.requirement_feedback
                    if feedback is not None:
                        case_source_violations = sum(
                            code
                            in {
                                "EVIDENCE_NOT_IN_SOURCE",
                                "PARTICIPANT_NOT_IN_SOURCE",
                                "EXPLICIT_PARTICIPANT_OMITTED",
                                "HEADCOUNT_AS_PARTICIPANT",
                                "CAPACITY_SOURCE_MISMATCH",
                                "FEATURE_NOT_IN_SOURCE",
                                "EXPLICIT_TIME_CHANGED",
                                "DURATION_INTERVAL_MISMATCH",
                                "INTENT_SOURCE_MISMATCH",
                            }
                            for code in feedback.codes
                        )
                terminal = state.next_route.value if state.next_route is not None else "NONE"
            except WorkflowError as exc:
                failure = exc.code
            except Exception as exc:  # bounded type only; never provider content or secrets
                failure = type(exc).__name__

            constraints_match = case_fp == 0 and case_fn == 0
            citation_match = (
                case_citations_checked > 0 and case_citations_valid == case_citations_checked
                if case.expected_intent is Intent.QUERY_POLICY
                else True
            )
            case_pass = (
                route_match
                and intent_match
                and constraints_match
                and tool_match
                and case_source_violations == 0
                and citation_match
                and failure is None
            )

            route_matches += int(route_match)
            intent_matches += int(intent_match)
            constraint_tp += case_tp
            constraint_fp += case_fp
            constraint_fn += case_fn
            tool_matches += int(tool_match)
            source_violations += case_source_violations
            citations_checked += case_citations_checked
            citations_valid += case_citations_valid
            response_models.update(state.response_models)
            _add_state_usage(token_usage, state)
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)
            results.append(
                {
                    "caseId": case.case_id,
                    "category": case.category.value,
                    "difficulty": case.difficulty.value,
                    "split": case.split.value,
                    "tags": list(case.tags),
                    "repeat": repeat,
                    "terminal": terminal,
                    "routeMatch": route_match,
                    "intentMatch": intent_match,
                    "constraintCounts": {"tp": case_tp, "fp": case_fp, "fn": case_fn},
                    "plannedToolSetMatch": tool_match,
                    "toolSelectionMatch": tool_match,
                    "sourceFidelityViolations": case_source_violations,
                    "feedbackCodes": (
                        list(state.requirement_feedback.codes)
                        if state.requirement_feedback is not None
                        else []
                    ),
                    "missingFields": list(state.missing_fields),
                    "citationsChecked": case_citations_checked,
                    "citationsValid": case_citations_valid,
                    "latencyMs": round(elapsed, 2),
                    "errorType": failure,
                    "casePass": case_pass,
                }
            )

    native_results = []
    for repeat in range(1, repeats + 1):
        probe, usage, model = _native_tool_probe(provider, repeat)
        native_results.append(probe)
        _add_usage(token_usage, usage)
        if model:
            response_models.add(model)

    total = len(results)
    route_accuracy = route_matches / total if total else 0.0
    intent_accuracy = intent_matches / total if total else 0.0
    constraint_f1 = _f1(constraint_tp, constraint_fp, constraint_fn)
    planned_tool_accuracy = tool_matches / total if total else 0.0
    native_accuracy = sum(item["valid"] for item in native_results) / len(native_results)
    citation_validity = citations_valid / citations_checked if citations_checked else 0.0
    policy_route_pass = all(
        item["routeMatch"]
        for item in results
        if next(case for case in cases if case.case_id == item["caseId"]).expected_intent
        is Intent.QUERY_POLICY
    )
    unique_cases = len({item["caseId"] for item in results})
    task_success_rate = sum(bool(item["casePass"]) for item in results) / total if total else 0.0
    stable_cases = sum(
        all(bool(item["casePass"]) for item in results if item["caseId"] == case_id)
        for case_id in {item["caseId"] for item in results}
    )
    stable_case_rate = stable_cases / unique_cases if unique_cases else 0.0
    passed = (
        total == len(cases) * repeats
        and route_accuracy >= 0.95
        and policy_route_pass
        and intent_accuracy >= 0.90
        and constraint_f1 >= 0.85
        and planned_tool_accuracy >= 0.90
        and source_violations == 0
        and native_accuracy == 1.0
        and citation_validity == 1.0
        and task_success_rate >= 0.90
        and (suite != "core" or stable_case_rate >= 0.85)
    )
    return {
        **base,
        "status": "PASS" if passed else "FAIL",
        "responseModels": sorted(response_models),
        "tokenUsage": token_usage,
        "metrics": {
            "samples": total,
            "uniqueCases": unique_cases,
            "routeAccuracy": route_accuracy,
            "policyRouteAllCorrect": policy_route_pass,
            "intentAccuracy": intent_accuracy,
            "constraintFieldF1": constraint_f1,
            "plannedToolSetAccuracy": planned_tool_accuracy,
            "toolSelectionAccuracy": planned_tool_accuracy,
            "sourceFidelityViolations": source_violations,
            "nativeToolProtocol": native_accuracy,
            "citationValidity": citation_validity,
            "taskSuccessRate": task_success_rate,
            "stableCaseRate": stable_case_rate,
            "latencyP50Ms": _percentile(latencies, 0.50),
            "latencyP95Ms": _percentile(latencies, 0.95),
        },
        "results": results,
        "nativeToolProtocolResults": native_results,
        "limitations": [
            "This component report makes real DeepSeek calls but no Java business writes.",
            "Hard constraints and HITL-before-side-effects are measured by integration and "
            "live-model-trajectory gates.",
            "Policy retrieval uses the versioned in-memory seed corpus; the Policy model "
            "call is real.",
            "plannedToolSetAccuracy is derived from validated structured state; it is not an "
            "observed Tool trajectory metric. toolSelectionAccuracy is retained only as a "
            "compatibility alias for the same planned value.",
        ],
    }


def _initial_state(case: EvaluationCase, repeat: int, config: Settings) -> AgentState:
    return AgentState(
        thread_id=f"eval-{case.case_id}-{repeat}",
        run_id=f"eval-{case.case_id}-{repeat}",
        trace_id=f"eval-{case.case_id}-{repeat}",
        user_id=case.context.user_id,
        roles=["EMPLOYEE"],
        message=case.input,
        request_time=case.context.now,
        model_provider="deepseek",
        configured_model=config.deepseek_model,
    )


def _observed_constraints(state: AgentState) -> ConstraintExpectation:
    request = state.meeting_request
    if request is None:
        return ConstraintExpectation()
    return ConstraintExpectation(
        duration_minutes=request.duration_minutes,
        minimum_capacity=request.minimum_capacity,
        required_features=request.required_features,
        required_participant_names=[item.name for item in request.required_participants],
        target_meeting_id=request.target_meeting_id,
        missing_fields=state.missing_fields,
    )


def _scored_expectation(case: EvaluationCase) -> ConstraintExpectation:
    expected = case.expected_constraints
    if case.expected_intent is Intent.CANCEL_MEETING:
        return ConstraintExpectation(target_meeting_id=expected.target_meeting_id)
    if case.expected_intent is Intent.UPDATE_PREFERENCE:
        return ConstraintExpectation(required_participant_names=expected.required_participant_names)
    return ConstraintExpectation(
        duration_minutes=expected.duration_minutes,
        minimum_capacity=expected.minimum_capacity,
        required_features=expected.required_features,
        required_participant_names=expected.required_participant_names,
        target_meeting_id=expected.target_meeting_id,
    )


def _expected_tools(case: EvaluationCase, *, state: AgentState) -> set[str]:
    if state.missing_fields:
        return set()
    intent = case.expected_intent
    names = bool(case.expected_constraints.required_participant_names)
    if intent is Intent.CREATE_MEETING:
        return {
            *({"resolve_employees"} if names else set()),
            "get_employee_free_busy",
            "search_available_rooms",
            "create_booking_draft",
        }
    if intent is Intent.MODIFY_MEETING:
        return {
            *({"resolve_employees"} if names else set()),
            "get_recent_meeting",
            "get_employee_free_busy",
            "search_available_rooms",
            "create_reschedule_draft",
        }
    if intent is Intent.CANCEL_MEETING:
        return {"get_recent_meeting", "create_cancellation_preview"}
    if intent in {Intent.FIND_COMMON_TIME, Intent.RECOMMEND_ROOM}:
        return {
            *({"resolve_employees"} if names else set()),
            "get_employee_free_busy",
            "search_available_rooms",
        }
    return set()


def _selected_tools(state: AgentState) -> set[str]:
    request = state.meeting_request
    if request is None or state.missing_fields:
        return set()
    names = bool(request.required_participants)
    if request.intent is Intent.CREATE_MEETING:
        return {
            *({"resolve_employees"} if names else set()),
            "get_employee_free_busy",
            "search_available_rooms",
            "create_booking_draft",
        }
    if request.intent is Intent.MODIFY_MEETING:
        return {
            *({"resolve_employees"} if names else set()),
            "get_recent_meeting",
            "get_employee_free_busy",
            "search_available_rooms",
            "create_reschedule_draft",
        }
    if request.intent is Intent.CANCEL_MEETING:
        return {"get_recent_meeting", "create_cancellation_preview"}
    if request.intent in {Intent.FIND_COMMON_TIME, Intent.RECOMMEND_ROOM}:
        return {
            *({"resolve_employees"} if names else set()),
            "get_employee_free_busy",
            "search_available_rooms",
        }
    return set()


def _native_tool_probe(
    provider: DeepSeekModelProvider, repeat: int
) -> tuple[dict[str, Any], Any, str | None]:
    started = time.perf_counter()
    valid = False
    error_type: str | None = None
    response = None
    try:
        response = provider.complete_tools(
            ToolModelRequest(
                agent_name="tool-protocol-evaluation",
                messages=(
                    ToolLoopMessage(
                        role="system",
                        content=(
                            "Call resolve_employees exactly once with names [张三, 李四] and "
                            "departmentNames []. Use the supplied native function; no prose."
                        ),
                    ),
                    ToolLoopMessage(role="user", content="解析张三和李四。"),
                ),
                tools=(READ_TOOL_DEFINITIONS[0],),
                iteration=repeat,
            )
        )
        valid = len(response.tool_calls) == 1 and response.tool_calls[0].name == "resolve_employees"
        if valid:
            arguments = ResolveEmployeesInput.model_validate_json(response.tool_calls[0].arguments)
            valid = arguments.names == ["张三", "李四"] and not arguments.department_names
    except Exception as exc:
        error_type = type(exc).__name__
    usage = response.usage if response is not None else None
    model = response.model if response is not None else None
    return (
        {
            "repeat": repeat,
            "valid": valid,
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
            "errorType": error_type,
        },
        usage,
        model,
    )


def _empty_usage() -> dict[str, int]:
    return {"inputTokens": 0, "outputTokens": 0, "cacheHitTokens": 0, "cacheMissTokens": 0}


def _add_state_usage(target: dict[str, int], state: AgentState) -> None:
    target["inputTokens"] += state.input_tokens
    target["outputTokens"] += state.output_tokens
    target["cacheHitTokens"] += state.cache_hit_tokens
    target["cacheMissTokens"] += state.cache_miss_tokens


def _add_usage(target: dict[str, int], usage: Any) -> None:
    if usage is None:
        return
    target["inputTokens"] += usage.input_tokens
    target["outputTokens"] += usage.output_tokens
    target["cacheHitTokens"] += usage.cache_hit_tokens
    target["cacheMissTokens"] += usage.cache_miss_tokens


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    denominator = 2 * true_positive + false_positive + false_negative
    return 1.0 if denominator == 0 else (2 * true_positive) / denominator


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real-model component evaluation.")
    parser.add_argument("--mode", choices=("component", "trajectory"), default="component")
    parser.add_argument("--suite", choices=("core", "full"), default="core")
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repeats = args.repeats if args.repeats is not None else (3 if args.suite == "core" else 1)
    if repeats < 1 or repeats > 3:
        parser.error("--repeats must be between 1 and 3")
    report = run_live_evaluation(mode=args.mode, suite=args.suite, repeats=repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
