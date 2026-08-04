"""Day 5 LangGraph orchestration for exactly four specialised runtime Agents.

``SupervisorAgent``, ``RequirementAgent``, ``PolicyAgent`` and
``SchedulingAgent`` remain the only runtime Agents.  Solver, HITL and booking
operations below are deterministic graph nodes; model output never receives a
general write Tool surface.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.agent_loop import (
    READ_TOOL_DEFINITIONS,
    LoopStopReason,
    ReadToolGate,
    RequirementEvaluator,
    RequirementNormalizer,
    RouteEvaluator,
    SourceFidelityEvaluator,
    ToolGateError,
)
from app.checkpoints import checkpoint_thread_id
from app.checkpoints.redis import RedisCheckpointError
from app.config import Settings
from app.persistence import MetadataRepository
from app.providers.base import (
    ModelCompletion,
    ModelOutputError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    StructuredModelRunner,
    ToolLoopMessage,
    ToolModelRequest,
)
from app.rag.policies import PolicyRetrievalError, PolicyRetriever
from app.scheduling import ScheduleSolver, ScheduleSolverError
from app.schemas.agent import (
    AgentResumeRequest,
    AgentState,
    AvailabilitySnapshot,
    BusyInterval,
    CancellationDraftView,
    ConflictRepairFeedbackState,
    CreateDraftView,
    EmployeeBusySlots,
    HitlResumeCommand,
    Intent,
    MeetingRequest,
    MeetingView,
    OperationType,
    Participant,
    PolicyResult,
    PolicySelection,
    RequirementDraft,
    RequirementExtraction,
    RequirementFeedbackState,
    RescheduleDraftView,
    ResumeAction,
    RoomAvailability,
    Route,
    RunStatus,
    SchedulingPreferences,
    SchedulingProblem,
    SupervisorDecision,
    TimeWindow,
)
from app.security import AgentContext
from app.tools.java import (
    CreateBookingDraftInput,
    JavaReadToolClient,
    JavaToolError,
    RescheduleDraftInput,
    ToolOutcome,
    stable_idempotency_identity,
    stable_tool_identity,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "meeting-agent-prompts-v3"
SCHEMA_VERSION = "meeting-agent-state-v3"

SUPERVISOR_PROMPT = """You are the Supervisor Agent for an enterprise meeting scheduler.
Only classify the current objective. Initial routes are POLICY, REQUIREMENT, or CLARIFICATION.
Never route directly to SCHEDULING, HITL, WAIT_BUSINESS_RESULT, FINAL, or FAIL. POLICY is only a
pure rule/restriction/permission question without a mutation request. REQUIREMENT covers create,
find time, room recommendation, modify, cancel, and explicit preference updates. evidence must be
one continuous verbatim substring of USER_MESSAGE. Return only the schema JSON; no reasoning."""

REQUIREMENT_PROMPT = """You are the Requirement Agent. Extract only source-supported facts into
RequirementDraft. Missing facts remain null/empty. Never invent names from a headcount. Copy named
participants exactly. Preserve explicit start/end timestamps and derive duration from that interval.
Supported features: 白板=WHITEBOARD, 大屏=LARGE_SCREEN, 视频会议=VIDEO_CONFERENCE,
投影=PROJECTOR. title and meetingType may be null because deterministic code owns safe defaults.
Every populated user-derived field needs fieldEvidence whose source is a continuous verbatim
substring of USER_MESSAGE. Do not call tools, create drafts, confirm, or expose reasoning."""

REQUIREMENT_REPAIR_PROMPT = """Repair RequirementDraft using only USER_MESSAGE,
SERVER_REQUEST_TIME, and EVALUATOR_FEEDBACK. Correct only rejected fields. Unsupported facts must
be null/empty. Return only the corrected schema JSON; no reasoning."""


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class EventSink:
    _events: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def emit(self, event_name: str, data: dict[str, object]) -> None:
        self._events.append((event_name, data))

    def drain(self) -> list[tuple[str, dict[str, object]]]:
        events = self._events[:]
        self._events.clear()
        return events


@dataclass(frozen=True)
class SupervisorAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    evaluator: RouteEvaluator = field(default_factory=RouteEvaluator)

    def execute(self, state: AgentState) -> tuple[AgentState, str, int]:
        decision, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="supervisor",
            system_prompt=SUPERVISOR_PROMPT,
            user_prompt=state.message,
            output_type=SupervisorDecision,
        )
        feedback = self.evaluator.evaluate(decision, state.message)
        if feedback is not None:
            try:
                repaired, repair_completions = _model_output_with_count(
                    provider=self.provider,
                    runner=self.runner,
                    agent_name="supervisor",
                    system_prompt=SUPERVISOR_PROMPT,
                    user_prompt=(
                        f"USER_MESSAGE={state.message}\nROUTE_FEEDBACK="
                        f"{feedback.model_dump_json(by_alias=True)}"
                    ),
                    output_type=SupervisorDecision,
                )
                completions.extend(repair_completions)
                decision = repaired
                feedback = self.evaluator.evaluate(decision, state.message)
            except WorkflowError:
                feedback = feedback
        if feedback is None:
            route = decision.route
            intent = decision.intent_hint
        else:
            route, intent = self.evaluator.fallback(state.message)
        if route is Route.POLICY:
            intent = Intent.QUERY_POLICY
        updated = _apply_completions(state, completions)
        return (
            updated.model_copy(update={"next_route": route, "intent": intent}),
            decision.summary,
            len(completions),
        )


@dataclass(frozen=True)
class RequirementAgent:
    provider: ModelProvider
    runner: StructuredModelRunner

    evaluator: RequirementEvaluator = field(default_factory=RequirementEvaluator)
    fidelity: SourceFidelityEvaluator = field(default_factory=SourceFidelityEvaluator)
    normalizer: RequirementNormalizer = field(default_factory=RequirementNormalizer)

    def execute(self, state: AgentState) -> tuple[AgentState, str, int]:
        prompt = _requirement_prompt(state)
        extraction, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="requirement",
            system_prompt=REQUIREMENT_PROMPT,
            user_prompt=prompt,
            output_type=RequirementExtraction,
        )
        draft = extraction.requirement_draft
        draft = _apply_explicit_meeting_defaults(
            draft, state.message, request_time=state.request_time
        )
        feedback = self.fidelity.evaluate(draft, state.message)
        request = None
        report = None
        if feedback is None:
            request, report = self.normalizer.normalize(draft)
            semantic = self.evaluator.evaluate(request, request_time=state.request_time)
            feedback = semantic
        if feedback is not None and feedback.repairable:
            extraction, repair_completions = _model_output_with_count(
                provider=self.provider,
                runner=self.runner,
                agent_name="requirement",
                system_prompt=REQUIREMENT_REPAIR_PROMPT,
                user_prompt=(
                    f"{prompt}\nEVALUATOR_FEEDBACK="
                    f"{feedback.model_dump_json(by_alias=True)}"
                ),
                output_type=RequirementExtraction,
            )
            completions.extend(repair_completions)
            draft = extraction.requirement_draft
            draft = _apply_explicit_meeting_defaults(
                draft, state.message, request_time=state.request_time
            )
            feedback = self.fidelity.evaluate(draft, state.message)
            if (
                feedback is not None
                and "INTENT_SOURCE_MISMATCH" in feedback.codes
                and state.intent is not None
            ):
                # The Supervisor route boundary has already applied the same
                # high-confidence anchors.  After one failed model repair,
                # reuse that safe intent instead of asking the user to restate
                # an unambiguous “预约/改到/取消” verb.
                draft = draft.model_copy(update={"intent": state.intent})
                feedback = self.fidelity.evaluate(draft, state.message)
            if feedback is None:
                request, report = self.normalizer.normalize(draft)
                feedback = self.evaluator.evaluate(request, request_time=state.request_time)
        if request is None:
            request, report = self.normalizer.normalize(draft)
        assert report is not None
        # EDIT is intentionally revalidated by Requirement before it reaches
        # Scheduling.  Only the documented bounded fields may override it.
        if state.edited_draft is not None and state.edited_draft.start_at is not None:
            start_at = state.edited_draft.start_at
            request = request.model_copy(
                update={
                    "time_window": TimeWindow(
                        start=start_at,
                        end=start_at + timedelta(minutes=request.duration_minutes or 0),
                    )
                }
            )
        if state.edited_draft is not None and state.edited_draft.meeting_id is not None:
            request = request.model_copy(
                update={"target_meeting_id": state.edited_draft.meeting_id}
            )
        feedback_state = (
            RequirementFeedbackState.model_validate(feedback.model_dump())
            if feedback is not None
            else None
        )
        semantic_missing = [] if feedback is None else feedback.codes
        # Safe defaults and derived values are owned by the normalizer.  A
        # stale model missingFields entry must not undo that deterministic
        # contract or force the user to supply an internal enum.
        normalizer_owned = {
            *report.defaults_applied,
            *report.derived_fields,
            "title",
            "meetingType",
            "preferredBuildings",
            "optionalGroups",
            "durationMinutes" if request.duration_minutes else "",
            "timeWindow" if request.time_window is not None else "",
            "targetMeetingReference" if request.target_meeting_reference else "",
            "targetMeetingId" if request.target_meeting_id is not None else "",
            "hardConstraints",
            "softConstraints",
            "createVideoConference",
            "needsPolicy",
        }
        extraction_missing = [
            item
            for item in extraction.missing_fields
            if item not in normalizer_owned
            and not (item == "targetMeetingId" and request.target_meeting_reference)
            and not (
                item == "targetMeetingReference" and request.target_meeting_id is not None
            )
            and not (
                item in {"requiredParticipants", "requiredParticipantNames"}
                and request.intent is Intent.CREATE_MEETING
            )
        ]
        missing_fields = list(dict.fromkeys([*extraction_missing, *semantic_missing]))
        next_route = (
            Route.CLARIFICATION
            if missing_fields
            else Route.POLICY
            if draft.needs_policy
            else Route.SCHEDULING
        )
        return (
            _apply_completions(state, completions).model_copy(
                update={
                    "intent": request.intent,
                    "meeting_request": request,
                    "missing_fields": missing_fields,
                    "requirement_feedback": feedback_state,
                    "normalization_report": report,
                    "next_route": next_route,
                }
            ),
            draft.summary if feedback is None else feedback.summary,
            len(completions),
        )


@dataclass(frozen=True)
class PolicyAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    retriever: PolicyRetriever

    def execute(self, state: AgentState) -> tuple[AgentState, str, int]:
        try:
            candidates = self.retriever.search(state.message)
        except PolicyRetrievalError as exc:
            raise WorkflowError("POLICY_RETRIEVAL_UNAVAILABLE", "会议制度检索暂不可用") from exc
        if not candidates:
            result = PolicyResult(
                summary="未找到可验证的会议制度证据。",
                confidence=0.0,
                verification_status="UNVERIFIED",
                constraints=[],
                citations=[],
            )
            return state.model_copy(
                update={"policy_result": result, "citations": []}
            ), result.summary, 0

        candidate_summary = "; ".join(
            f"{chunk.chunk_id}: {chunk.title}" for chunk in candidates[:5]
        )
        selection, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="policy",
            system_prompt=(
                "You are the Policy Agent. Select only evidence chunk IDs supplied by "
                "the retriever and return a concise rule answer. Never invent citations or "
                "make a booking decision."
            ),
            user_prompt=f"Question: {state.message}\nCandidates: {candidate_summary}",
            output_type=PolicySelection,
        )
        try:
            opened = self.retriever.open_candidates(
                candidates=candidates,
                selected_chunk_ids=selection.selected_chunk_ids,
            )
        except PolicyRetrievalError as exc:
            raise WorkflowError("POLICY_CITATION_INVALID", "规则引用不在本轮检索结果中") from exc
        citations = [chunk.citation() for chunk in opened]
        result = PolicyResult(
            summary=selection.answer_summary,
            confidence=selection.confidence,
            verification_status="VERIFIED" if citations else "UNVERIFIED",
            constraints=selection.constraints,
            citations=citations,
        )
        next_route = Route.FINAL if state.intent is Intent.QUERY_POLICY else Route.SCHEDULING
        return (
            _apply_completions(state, completions).model_copy(
                update={"policy_result": result, "citations": citations, "next_route": next_route}
            ),
            result.summary,
            len(completions),
        )


@dataclass(frozen=True)
class SchedulingAgent:
    """One specialised Agent plus deterministic candidate construction.

    The model is limited to its structured READ plan.  The Java READ calls,
    candidate solver, independent validation and DRAFT call happen in bounded
    code after that plan was accepted.
    """

    provider: ModelProvider
    runner: StructuredModelRunner
    tools: JavaReadToolClient
    solver: ScheduleSolver = field(default_factory=ScheduleSolver)
    max_model_calls: int = 12
    max_tool_calls: int = 16

    def execute(
        self, state: AgentState, context: AgentContext
    ) -> tuple[AgentState, str, list[ToolOutcome], int]:
        request = state.meeting_request
        if request is None:
            raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
        names = list(dict.fromkeys(item.name for item in request.required_participants))
        messages = [
            ToolLoopMessage(
                role="system",
                content=_scheduling_system_prompt(state=state, context=context),
            ),
            ToolLoopMessage(role="user", content=state.message),
        ]
        outcomes: list[ToolOutcome] = []
        resolved = list(state.resolved_employees)
        free_busy_data: dict[str, Any] | None = None
        rooms_data: dict[str, Any] | None = None
        recent_data: dict[str, Any] | None = None
        fingerprints = set(state.executed_tool_fingerprints)
        gate = ReadToolGate(self.tools)
        model_calls = 0
        tool_usage: list[ModelCompletion] = []
        loop_iteration = state.loop_iteration
        max_iterations = 4
        for _ in range(max_iterations):
            if state.model_call_count + model_calls + 1 > self.max_model_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "调度模型调用预算已耗尽")
            if state.tool_call_count + len(outcomes) >= self.max_tool_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "调度工具调用预算已耗尽")
            loop_iteration += 1
            model_calls += 1
            try:
                tool_response = self.provider.complete_tools(
                    ToolModelRequest(
                        agent_name="scheduling",
                        messages=tuple(messages),
                        tools=READ_TOOL_DEFINITIONS,
                        iteration=loop_iteration,
                    )
                )
            except ModelProviderError as exc:
                raise WorkflowError("MODEL_UNAVAILABLE", "模型服务暂不可用") from exc
            messages.append(
                ToolLoopMessage(
                    role="assistant",
                    content=tool_response.content,
                    tool_calls=tool_response.tool_calls,
                )
            )
            tool_usage.append(
                ModelCompletion(
                    content=tool_response.content,
                    tool_calls=tool_response.tool_calls,
                    usage=tool_response.usage,
                    model=tool_response.model,
                )
            )
            if not tool_response.tool_calls:
                if _read_facts_ready(request, free_busy_data, rooms_data, recent_data):
                    break
                messages.append(
                    ToolLoopMessage(
                        role="user",
                        content=(
                            "VERIFY_FEEDBACK={\"codes\":[\"REQUIRED_FACTS_MISSING\"],"
                            "\"instruction\":\"Call the missing READ tools only.\"}"
                        ),
                    )
                )
                continue
            for call in tool_response.tool_calls:
                if state.tool_call_count + len(outcomes) + 1 > self.max_tool_calls:
                    raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "调度工具调用预算已耗尽")
                try:
                    gated_result = gate.execute(
                        call=call,
                        state=state,
                        context=context,
                        resolved_employees=resolved,
                        fingerprints=fingerprints,
                    )
                except ToolGateError as exc:
                    messages.append(
                        ToolLoopMessage(
                            role="tool",
                            tool_call_id=call.id,
                            content=json.dumps(
                                {"ok": False, "errorCode": exc.code},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                    )
                    if not exc.recoverable:
                        raise WorkflowError(exc.code, "只读工具调用未通过安全校验") from exc
                    continue
                outcomes.append(gated_result.outcome)
                fingerprints.add(gated_result.fingerprint)
                messages.append(
                    ToolLoopMessage(
                        role="tool", tool_call_id=call.id, content=gated_result.observation
                    )
                )
                if call.name == "resolve_employees":
                    resolved = _participants_from_java(gated_result.outcome.data)
                    unresolved = gated_result.outcome.data.get("unresolvedNames", [])
                    if not isinstance(unresolved, list) or unresolved or len(resolved) < len(names):
                        raise WorkflowError("EMPLOYEE_UNRESOLVED", "存在无法解析的必需参会者")
                elif call.name == "get_employee_free_busy":
                    free_busy_data = gated_result.outcome.data
                elif call.name == "search_available_rooms":
                    rooms_data = gated_result.outcome.data
                elif call.name == "get_recent_meeting":
                    recent_data = gated_result.outcome.data
                    recent = _recent_meeting(recent_data, request.target_meeting_id)
                    if request.intent is Intent.MODIFY_MEETING and recent is not None:
                        if request.time_window is None:
                            start_at = recent.start_at
                            request = request.model_copy(
                                update={
                                    "time_window": TimeWindow(
                                        start=start_at,
                                        end=start_at
                                        + timedelta(minutes=request.duration_minutes),
                                    )
                                }
                            )
                            state = state.model_copy(update={"meeting_request": request})
                            messages[0] = ToolLoopMessage(
                                role="system",
                                content=_scheduling_system_prompt(
                                    state=state, context=context
                                ),
                            )
                        resolved = [
                            Participant(name=item.display_name, employee_id=item.employee_id)
                            for item in recent.participants
                            if item.participant_type == "REQUIRED"
                        ]
            # Even when deterministic facts are now complete, send the
            # resulting role=tool observations back through one assistant
            # turn. A tool-free response is the explicit protocol boundary
            # that lets the verifier advance to deterministic solving.
        if not _read_facts_ready(request, free_busy_data, rooms_data, recent_data):
            raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "调度循环在预算内未取得完整事实")

        usage_state = _apply_completions(state, tool_usage)
        common_update: dict[str, object] = {
            "resolved_employees": resolved,
            "executed_tool_fingerprints": sorted(fingerprints),
            "loop_iteration": loop_iteration,
        }
        if request.intent is Intent.CANCEL_MEETING:
            target = request.target_meeting_id or _recent_meeting_id(recent_data)
            if target is None:
                return (
                    usage_state.model_copy(
                        update={
                            **common_update,
                            "status": RunStatus.WAITING_USER_INPUT,
                            "missing_fields": ["uniqueTargetMeeting"],
                            "next_route": Route.FINAL,
                        }
                    ),
                    "请明确唯一要取消的会议。",
                    outcomes,
                    model_calls,
                )
            generation = state.draft_generation + 1
            call_id = stable_tool_identity(
                state.run_id, "create_cancellation_preview", f"{target}:{generation}"
            )
            try:
                outcome, cancellation = self.tools.create_cancellation_preview(
                    context=context, meeting_id=target, tool_call_id=call_id
                )
            except JavaToolError as exc:
                raise WorkflowError(exc.code, "取消预览创建暂不可用") from exc
            outcomes.append(outcome)
            return (
                usage_state.model_copy(
                    update={
                        **common_update,
                        "operation_type": OperationType.CANCEL,
                            "draft": CancellationDraftView(meeting=cancellation.meeting),
                            "confirmation_token": cancellation.confirmation_token,
                            "draft_expires_at": cancellation.expires_at,
                        "draft_generation": generation,
                        "status": RunStatus.WAITING_CONFIRMATION,
                        "next_route": Route.HITL,
                        "stop_reason": LoopStopReason.READY_FOR_CONFIRMATION.value,
                    }
                ),
                "已生成取消预览，等待用户确认",
                outcomes,
                model_calls,
            )
        if request.intent not in {Intent.CREATE_MEETING, Intent.MODIFY_MEETING}:
            return (
                usage_state.model_copy(update={**common_update, "next_route": Route.FINAL}),
                "已通过受控工具循环完成只读事实查询",
                outcomes,
                model_calls,
            )
        assert free_busy_data is not None and rooms_data is not None
        snapshot = _snapshot_from_java(free_busy_data, rooms_data)
        required_ids = sorted(
            {context.user_id, *(item.employee_id for item in resolved if item.employee_id)}
        )
        problem = _scheduling_problem(
            state=state,
            request_required_ids=required_ids,
            snapshot=snapshot,
        )
        try:
            result = self.solver.solve(problem=problem, top_k=3)
        except (ScheduleSolverError, ValueError) as exc:
            raise WorkflowError(
                "SCHEDULE_VALIDATION_FAILED", "候选方案未通过独立硬约束校验"
            ) from exc

        if not result.has_solution:
            assert result.unsat is not None
            return (
                usage_state.model_copy(
                    update={
                        "resolved_employees": resolved,
                        "availability_snapshot": snapshot,
                        "schedule_candidates": [],
                        "selected_candidate_id": None,
                        "unsat_analysis": result.unsat,
                        "answer_summary": result.unsat.summary,
                        "next_route": Route.FINAL,
                        "stop_reason": LoopStopReason.NO_SOLUTION.value,
                        **common_update,
                    }
                ),
                result.unsat.summary,
                outcomes,
                model_calls,
            )

        candidates = [
            candidate
            for candidate in result.candidates
            if candidate.candidate_id not in state.excluded_candidate_ids
        ]
        if not candidates:
            answer = "冲突后没有产生满足原硬约束的新候选，请调整时间或会议室。"
            return (
                usage_state.model_copy(
                    update={
                        "resolved_employees": resolved,
                        "availability_snapshot": snapshot,
                        "schedule_candidates": [],
                        "selected_candidate_id": None,
                        "answer_summary": answer,
                        "status": RunStatus.WAITING_USER_INPUT,
                        "next_route": Route.FINAL,
                        "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                        **common_update,
                    }
                ),
                answer,
                outcomes,
                model_calls,
            )
        # The solver already invokes this validator.  Keeping this explicit
        # boundary makes the public event invariant obvious to future changes.
        if any(
            not self.solver.validator.is_valid(problem=problem, candidate=item)
            for item in candidates
        ):
            raise WorkflowError("SCHEDULE_VALIDATION_FAILED", "候选方案未通过独立硬约束校验")
        selected = candidates[0]
        generation = state.draft_generation + 1
        draft_operation = (
            "create_reschedule_draft"
            if request.intent is Intent.MODIFY_MEETING
            else "create_booking_draft"
        )
        draft_call_id = stable_tool_identity(
            state.run_id, draft_operation, f"{selected.candidate_id}:{generation}"
        )
        try:
            if request.intent is Intent.MODIFY_MEETING:
                target = request.target_meeting_id or _recent_meeting_id(recent_data)
                meeting = _recent_meeting(recent_data, target)
                if target is None or meeting is None:
                    raise WorkflowError("TARGET_MEETING_NOT_FOUND", "待改期会议不存在或不可见")
                original_required_ids = sorted(
                    {
                        item.employee_id
                        for item in meeting.participants
                        if item.participant_type == "REQUIRED"
                    }
                )
                original_optional_ids = sorted(
                    {
                        item.employee_id
                        for item in meeting.participants
                        if item.participant_type == "OPTIONAL"
                    }
                )
                draft_outcome, mutation_response = self.tools.create_reschedule_draft(
                    context=context,
                    tool_call_id=draft_call_id,
                    payload=RescheduleDraftInput(
                        meeting_id=target,
                        title=meeting.title,
                        meeting_type=meeting.meeting_type,
                        room_id=selected.room_id,
                        start_at=selected.start_at,
                        end_at=selected.end_at,
                        required_participant_ids=original_required_ids,
                        optional_participant_ids=original_optional_ids,
                        # The current MeetingView has no video-link flag.  Do
                        # not let a fresh extraction turn “其他不变” into a new
                        # external side effect.
                        create_video_conference=False,
                        expected_version=meeting.version,
                    ),
                )
                draft_view: CreateDraftView | RescheduleDraftView = RescheduleDraftView(
                    original_meeting=mutation_response.before,
                    proposed_meeting=mutation_response.after,
                )
                token = mutation_response.confirmation_token
                expires = mutation_response.expires_at
                operation = OperationType.RESCHEDULE
            else:
                draft_outcome, draft_response = self.tools.create_booking_draft(
                context=context,
                tool_call_id=draft_call_id,
                payload=CreateBookingDraftInput(
                    title=request.title,
                    meeting_type=request.meeting_type,
                    room_id=selected.room_id,
                    start_at=selected.start_at,
                    end_at=selected.end_at,
                    required_participant_ids=required_ids,
                    optional_participant_ids=[],
                    create_video_conference=request.create_video_conference,
                ),
                )
                draft_view = CreateDraftView(draft=draft_response.draft)
                token = draft_response.confirmation_token
                expires = draft_response.expires_at
                operation = OperationType.CREATE
        except JavaToolError as exc:
            raise WorkflowError(exc.code, "预约草案创建暂不可用") from exc
        outcomes.append(draft_outcome)
        return (
            usage_state.model_copy(
                update={
                    "resolved_employees": resolved,
                    "availability_snapshot": snapshot,
                    "schedule_candidates": candidates,
                    "selected_candidate_id": selected.candidate_id,
                    "unsat_analysis": None,
                    "operation_type": operation,
                    "draft": draft_view,
                    "confirmation_token": token,
                    "draft_expires_at": expires,
                    "draft_tool_call_id": draft_call_id,
                    "draft_generation": generation,
                    "confirm_tool_call_id": None,
                    "confirm_idempotency_key": None,
                    "pending_request_no": None,
                    "business_result": None,
                    "status": RunStatus.WAITING_CONFIRMATION,
                    "next_route": Route.HITL,
                    "stop_reason": LoopStopReason.READY_FOR_CONFIRMATION.value,
                    **common_update,
                }
            ),
            "已生成并校验候选方案，等待用户确认草案",
            outcomes,
            model_calls,
        )


def _participants_from_java(data: dict[str, Any]) -> list[Participant]:
    raw = data.get("employees", [])
    if not isinstance(raw, list):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
    participants: list[Participant] = []
    for employee in raw:
        if not isinstance(employee, dict):
            raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
        employee_id = employee.get("employeeId")
        display_name = employee.get("displayName")
        if (
            not isinstance(employee_id, int)
            or not isinstance(display_name, str)
            or not display_name
        ):
            raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
        participants.append(Participant(name=display_name, employee_id=employee_id))
    return participants


def _requirement_prompt(state: AgentState) -> str:
    return (
        f"REQUEST_TIME={state.request_time.isoformat()}\n"
        "TIMEZONE=Asia/Shanghai\n"
        f"USER_MESSAGE={state.message}"
    )


def _apply_explicit_meeting_defaults(
    draft: RequirementDraft, source: str, *, request_time: datetime
) -> RequirementDraft:
    updates: dict[str, object] = {}
    if "架构评审" in source:
        if not draft.title:
            updates["title"] = "架构评审"
        if not draft.meeting_type:
            updates["meeting_type"] = "ARCHITECTURE_REVIEW"
    if (
        draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        and draft.target_meeting_id is None
        and not draft.target_meeting_reference
    ):
        for reference in ("刚才那个架构评审", "刚才那个会议", "刚才那个", "最近的会议"):
            if reference in source:
                updates["target_meeting_reference"] = reference
                break
    if draft.time_window is None:
        target_date = None
        if "明天" in source:
            target_date = (request_time + timedelta(days=1)).date()
        elif "下周三" in source:
            days_until = ((2 - request_time.weekday()) % 7) + 7
            target_date = (request_time + timedelta(days=days_until)).date()
        if target_date is not None:
            start_hour, end_hour = (13, 18) if "下午" in source else (9, 12)
            start = request_time.replace(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=start_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            updates["time_window"] = TimeWindow(
                start=start,
                end=start.replace(hour=end_hour),
            )
    return draft.model_copy(update=updates) if updates else draft


def _scheduling_system_prompt(*, state: AgentState, context: AgentContext) -> str:
    request = state.meeting_request
    if request is None:
        raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
    window = request.time_window
    canonical: dict[str, object] = {
        "organizerId": context.user_id,
        "intent": request.intent.value,
        "targetMeetingId": request.target_meeting_id,
        "targetMeetingReference": request.target_meeting_reference,
        "participantNames": [item.name for item in request.required_participants],
        "from": window.start.isoformat() if window is not None else None,
        "to": window.end.isoformat() if window is not None else None,
        "requestedMinimumCapacity": request.minimum_capacity or 1,
        "requiredFeatures": request.required_features,
        "excludedCandidateIds": state.excluded_candidate_ids,
    }
    return (
        "You are the Scheduling Agent. Use only the supplied READ functions. Never call DRAFT "
        "or WRITE operations, never provide userId/runId/roles, and never expose reasoning. "
        "After employee resolution, room minimumCapacity must be the maximum of "
        "requestedMinimumCapacity and the unique organizer plus resolved employee IDs.\n"
        "CANONICAL_CONTEXT="
        + json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    )


def _read_facts_ready(
    request: MeetingRequest,
    free_busy_data: dict[str, Any] | None,
    rooms_data: dict[str, Any] | None,
    recent_data: dict[str, Any] | None,
) -> bool:
    if request.intent is Intent.CREATE_MEETING:
        return free_busy_data is not None and rooms_data is not None
    if request.intent is Intent.MODIFY_MEETING:
        return recent_data is not None and free_busy_data is not None and rooms_data is not None
    if request.intent is Intent.CANCEL_MEETING:
        return request.target_meeting_id is not None or recent_data is not None
    return True


def _recent_meeting_id(data: dict[str, Any] | None) -> int | None:
    meeting = _recent_meeting(data, None)
    return meeting.id if meeting is not None else None


def _recent_meeting(
    data: dict[str, Any] | None, target_meeting_id: int | None
) -> MeetingView | None:
    if data is None:
        return None
    raw = data.get("meetings", [])
    if not isinstance(raw, list):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议响应格式无效")
    try:
        meetings = [
            meeting
            for item in raw
            if (meeting := MeetingView.model_validate(item)).status == "CONFIRMED"
        ]
    except ValueError as exc:
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议响应格式无效") from exc
    if target_meeting_id is not None:
        return next((item for item in meetings if item.id == target_meeting_id), None)
    return meetings[0] if meetings else None


def _snapshot_from_java(
    free_busy_data: dict[str, Any], rooms_data: dict[str, Any]
) -> AvailabilitySnapshot:
    raw_busy = free_busy_data.get("employees", [])
    raw_rooms = rooms_data.get("rooms", [])
    if not isinstance(raw_busy, list) or not isinstance(raw_rooms, list):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "可用性查询响应格式无效")
    try:
        employees = [
            EmployeeBusySlots(
                employee_id=item["employeeId"],
                busy_intervals=[
                    BusyInterval(start_at=slot["startAt"], end_at=slot["endAt"])
                    for slot in item.get("busySlots", [])
                ],
            )
            for item in raw_busy
            if isinstance(item, dict)
        ]
        if len(employees) != len(raw_busy):
            raise ValueError("busy employee item is invalid")
        rooms = [
            RoomAvailability(
                room_id=item["roomId"],
                room_name=item["roomName"],
                building=item["building"],
                capacity=item["capacity"],
                room_type=item["roomType"],
                features=item.get("features", []),
            )
            for item in raw_rooms
            if isinstance(item, dict)
        ]
        if len(rooms) != len(raw_rooms):
            raise ValueError("room item is invalid")
        return AvailabilitySnapshot(rooms=rooms, employee_busy_slots=employees)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("TOOL_RESPONSE_INVALID", "可用性查询响应格式无效") from exc


def _scheduling_problem(
    *, state: AgentState, request_required_ids: list[int], snapshot: AvailabilitySnapshot
) -> SchedulingProblem:
    request = state.meeting_request
    if request is None:
        raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
    restricted_snapshot = snapshot
    if state.edited_draft is not None and state.edited_draft.room_id is not None:
        restricted_snapshot = AvailabilitySnapshot(
            rooms=[room for room in snapshot.rooms if room.room_id == state.edited_draft.room_id],
            employee_busy_slots=snapshot.employee_busy_slots,
        )
    try:
        return SchedulingProblem(
            meeting_request=request,
            availability_snapshot=restricted_snapshot,
            organizer_id=state.user_id,
            required_participant_ids=request_required_ids,
            optional_participant_ids=[],
            policy_constraints=state.policy_result.constraints
            if state.policy_result is not None
            else [],
            user_preferences=state.user_preferences or SchedulingPreferences(),
        )
    except ValueError as exc:
        raise WorkflowError("SCHEDULE_INPUT_INVALID", "调度输入不满足结构化约束") from exc


def _model_output_with_count(
    *,
    provider: ModelProvider,
    runner: StructuredModelRunner,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    output_type: type[Any],
) -> tuple[Any, list[ModelCompletion]]:
    try:
        return runner.invoke_with_count(
            provider=provider,
            request=ModelRequest(
                agent_name=agent_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_name=output_type.__name__,
                schema=output_type.model_json_schema(by_alias=True),
            ),
            output_type=output_type,
        )
    except ModelOutputError as exc:
        raise WorkflowError("MODEL_OUTPUT_INVALID", "模型结构化输出校验失败") from exc
    except ModelProviderError as exc:
        raise WorkflowError("MODEL_UNAVAILABLE", "模型服务暂不可用") from exc


def _apply_completions(state: AgentState, completions: list[ModelCompletion]) -> AgentState:
    response_models = list(state.response_models)
    for completion in completions:
        if completion.model and completion.model not in response_models:
            response_models.append(completion.model)
    return state.model_copy(
        update={
            "response_models": response_models[:12],
            "input_tokens": state.input_tokens
            + sum(item.usage.input_tokens for item in completions),
            "output_tokens": state.output_tokens
            + sum(item.usage.output_tokens for item in completions),
            "cache_hit_tokens": state.cache_hit_tokens
            + sum(item.usage.cache_hit_tokens for item in completions),
            "cache_miss_tokens": state.cache_miss_tokens
            + sum(item.usage.cache_miss_tokens for item in completions),
        }
    )


@dataclass
class WorkflowRun:
    settings: Settings
    repository: MetadataRepository
    supervisor: SupervisorAgent
    requirement: RequirementAgent
    policy: PolicyAgent
    scheduling: SchedulingAgent
    context: AgentContext
    checkpoint_saver: BaseCheckpointSaver[Any]
    sink: EventSink = field(default_factory=EventSink)
    latest_state: AgentState | None = None
    paused: bool = False

    def stream(self, initial_state: AgentState) -> Iterator[tuple[str, dict[str, object]]]:
        self.paused = False
        graph = self._build_graph()
        self.latest_state = initial_state
        config = self._graph_config(initial_state)
        yield from self._stream_graph(graph, initial_state, config)

    def resume(
        self, state: AgentState, request: AgentResumeRequest
    ) -> Iterator[tuple[str, dict[str, object]]]:
        self.paused = False
        graph = self._build_graph()
        self.latest_state = state
        config = self._graph_config(state)
        command = HitlResumeCommand(action=request.action, edited_draft=request.edited_draft)
        yield from self._stream_graph(
            graph, Command(resume=command.model_dump(by_alias=True, mode="json")), config
        )

    def load_state(self, *, thread_id: str, run_id: str) -> AgentState | None:
        graph = self._build_graph()
        config = self._graph_config_values(thread_id=thread_id, run_id=run_id)
        try:
            snapshot = graph.get_state(config)
            if not snapshot.values:
                return None
            return AgentState.model_validate(snapshot.values)
        except WorkflowError:
            raise
        except Exception as exc:
            raise WorkflowError("CHECKPOINT_UNAVAILABLE", "检查点不可用") from exc

    def delete_checkpoint(self, *, thread_id: str, run_id: str) -> None:
        try:
            self.checkpoint_saver.delete_thread(checkpoint_thread_id(thread_id, run_id))
        except Exception as exc:
            raise WorkflowError("CHECKPOINT_UNAVAILABLE", "检查点不可用") from exc

    def restore_checkpoint_state(self, state: AgentState) -> None:
        """Rehydrate a durable state after a failed callback replan.

        This uses LangGraph's compiled graph ``update_state`` API and therefore
        the configured ``BaseCheckpointSaver`` rather than an application-side
        JSON cache.  It gives an at-least-once Java callback another chance to
        process the original WAITING_BUSINESS_RESULT state.
        """

        graph = self._build_graph()
        try:
            graph.update_state(
                self._graph_config(state),
                self._dump(state),
                as_node="confirm_booking",
            )
        except Exception as exc:
            raise WorkflowError("CHECKPOINT_UNAVAILABLE", "检查点不可用") from exc

    def _stream_graph(
        self,
        graph: CompiledStateGraph[AgentState, Any, AgentState, AgentState],
        input_value: AgentState | Command[Any],
        config: RunnableConfig,
    ) -> Iterator[tuple[str, dict[str, object]]]:
        try:
            # Checkpoint state is part of the HITL/HOT correctness boundary.
            # LangGraph otherwise defaults to asynchronous durability, which
            # can leave background saver tasks racing graph completion.
            for update in graph.stream(input_value, config=config, durability="sync"):
                if "__interrupt__" in update:
                    self.paused = True
                for node_name, node_state in update.items():
                    if node_name == "__interrupt__":
                        continue
                    self.latest_state = AgentState.model_validate(node_state)
                yield from self.sink.drain()
            if self.paused:
                snapshot = graph.get_state(config)
                if snapshot.values:
                    self.latest_state = AgentState.model_validate(snapshot.values)
        except WorkflowError:
            raise
        except RedisCheckpointError as exc:
            raise WorkflowError("CHECKPOINT_UNAVAILABLE", "检查点不可用") from exc
        except GraphRecursionError as exc:
            raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到图步骤上限") from exc
        except Exception as exc:
            # Keep the user-facing SSE summary safe, but preserve the Python
            # exception type/stack in service logs for operational diagnosis.
            # No prompt, token, or full AgentState is included here.
            logger.exception("LangGraph stream failed for run %s", self.context.run_id)
            raise WorkflowError("AGENT_GRAPH_FAILED", "智能调度工作流执行失败") from exc

    def _build_graph(self) -> CompiledStateGraph[AgentState, Any, AgentState, AgentState]:
        graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
        graph.add_node("supervisor_route", self._supervisor_node)
        graph.add_node("requirement_agent", self._requirement_node)
        graph.add_node("policy_agent", self._policy_node)
        graph.add_node("scheduling_agent", self._scheduling_node)
        graph.add_node("await_human_confirmation", self._await_human_confirmation_node)
        graph.add_node("resume_dispatch", self._resume_dispatch_node)
        graph.add_node("confirm_booking", self._confirm_booking_node)
        graph.add_node("compose_final", self._final_node)
        graph.add_conditional_edges(
            START,
            self._route_from_start,
            {
                "supervisor_route": "supervisor_route",
                "scheduling_agent": "scheduling_agent",
            },
        )
        graph.add_conditional_edges(
            "supervisor_route",
            self._route_after_supervisor,
            {
                "requirement_agent": "requirement_agent",
                "policy_agent": "policy_agent",
                "compose_final": "compose_final",
            },
        )
        graph.add_conditional_edges(
            "requirement_agent",
            self._route_after_requirement,
            {
                "policy_agent": "policy_agent",
                "scheduling_agent": "scheduling_agent",
                "compose_final": "compose_final",
            },
        )
        graph.add_conditional_edges(
            "policy_agent",
            self._route_after_policy,
            {"scheduling_agent": "scheduling_agent", "compose_final": "compose_final"},
        )
        graph.add_conditional_edges(
            "scheduling_agent",
            self._route_after_scheduling,
            {
                "await_human_confirmation": "await_human_confirmation",
                "compose_final": "compose_final",
            },
        )
        graph.add_edge("await_human_confirmation", "resume_dispatch")
        graph.add_conditional_edges(
            "resume_dispatch",
            self._route_after_resume_dispatch,
            {
                "confirm_booking": "confirm_booking",
                "requirement_agent": "requirement_agent",
                "compose_final": "compose_final",
            },
        )
        graph.add_conditional_edges(
            "confirm_booking",
            self._route_after_confirmation,
            {
                "scheduling_agent": "scheduling_agent",
                "compose_final": "compose_final",
                "end": END,
            },
        )
        graph.add_edge("compose_final", END)
        return graph.compile(checkpointer=self.checkpoint_saver)

    def _graph_config(self, state: AgentState) -> RunnableConfig:
        return self._graph_config_values(thread_id=state.thread_id, run_id=state.run_id)

    def _graph_config_values(self, *, thread_id: str, run_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": checkpoint_thread_id(thread_id, run_id)},
            # The state counter is the stable product guard.  Let LangGraph
            # receive one extra transition so it does not mask that error.
            "recursion_limit": self.settings.agent_max_graph_nodes + 1,
        }

    @staticmethod
    def _dump(state: AgentState) -> dict[str, Any]:
        return state.model_dump(mode="json")

    def _supervisor_node(self, state: AgentState) -> dict[str, Any]:
        return self._record_agent_step(
            state=state,
            agent_name="supervisor",
            node_name="supervisor_route",
            input_summary="Route the structured user request.",
            execute=self.supervisor.execute,
        )

    def _requirement_node(self, state: AgentState) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=1, tool_increment=0)
        sequence_no = state.step_count + 1
        started = time.perf_counter()
        try:
            updated, summary, model_calls = self.requirement.execute(state)
            if state.model_call_count + model_calls > self.settings.agent_max_model_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到模型调用上限")
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + model_calls,
                }
            )
            self._record_step(
                state=updated,
                sequence_no=sequence_no,
                agent_name="requirement",
                node_name="requirement_agent",
                summary=summary,
                duration_ms=int((time.perf_counter() - started) * 1000),
                input_summary="Extract, evaluate, and at most once repair a meeting request.",
            )
            self.latest_state = updated
            return self._dump(updated)
        except WorkflowError as exc:
            self._record_failed_step(state, "requirement", "requirement_agent", sequence_no, exc)
            raise

    def _policy_node(self, state: AgentState) -> dict[str, Any]:
        return self._record_agent_step(
            state=state,
            agent_name="policy",
            node_name="policy_agent",
            input_summary="Retrieve and validate policy references.",
            execute=self.policy.execute,
        )

    def _scheduling_node(self, state: AgentState) -> dict[str, Any]:
        # CREATE scheduling has three READ calls plus one DRAFT call; reserve
        # the upper bound before performing external effects.
        tool_increment = 4 if state.intent in {None, Intent.CREATE_MEETING} else 1
        self._ensure_limits(state, model_increment=1, tool_increment=tool_increment)
        started = time.perf_counter()
        sequence_no = state.step_count + 1
        initial_loop = _loop_event(
                state=state,
                phase="REPLAN" if state.replan_count else "PLAN",
                iteration=state.loop_iteration + 1,
                decision="读取受信任业务事实",
                model_budget=self.settings.agent_max_model_calls,
                tool_budget=self.settings.agent_max_tool_calls,
            )
        self._record_loop_event(state, initial_loop)
        try:
            updated, summary, outcomes, model_calls = self.scheduling.execute(
                state, self.context
            )
            actual_tool_count = len(outcomes)
            if (
                state.tool_call_count + actual_tool_count > self.settings.agent_max_tool_calls
                or state.model_call_count + model_calls > self.settings.agent_max_model_calls
            ):
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到工具调用上限")
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + model_calls,
                    "tool_call_count": state.tool_call_count + actual_tool_count,
                }
            )
            for outcome in outcomes:
                self._record_tool(state=updated, outcome=outcome)
            verified_loop = _loop_event(
                    state=updated,
                    phase="VERIFY",
                    iteration=updated.loop_iteration,
                    decision=(
                        "候选与草案已通过验证"
                        if updated.status is RunStatus.WAITING_CONFIRMATION
                        else "事实验证完成"
                    ),
                    model_budget=self.settings.agent_max_model_calls,
                    tool_budget=self.settings.agent_max_tool_calls,
                )
            self._record_loop_event(updated, verified_loop)
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._record_step(
                state=updated,
                sequence_no=sequence_no,
                agent_name="scheduling",
                node_name="scheduling_agent",
                summary=summary,
                duration_ms=duration_ms,
                input_summary=(
                    "Perform bounded Java READ, candidate solving, and optional draft creation."
                ),
            )
            if updated.schedule_candidates:
                self.sink.emit(
                    "plan.candidates",
                    {
                        "runId": updated.run_id,
                        "candidates": [
                            candidate.model_dump(by_alias=True, mode="json")
                            for candidate in updated.schedule_candidates
                        ],
                    },
                )
            if updated.status is RunStatus.WAITING_CONFIRMATION:
                if (
                    updated.confirmation_token is None
                    or updated.draft_expires_at is None
                    or updated.draft is None
                ):
                    raise WorkflowError("DRAFT_RESPONSE_INVALID", "预约草案响应不完整")
                # This is the one allowed transient confirmation-token event.
                # It never enters trace metadata, tool summaries or logs.
                self.sink.emit(
                    "hitl.required",
                    {
                        "runId": updated.run_id,
                        "status": RunStatus.WAITING_CONFIRMATION.value,
                        "confirmationToken": updated.confirmation_token,
                        "expiresAt": updated.draft_expires_at.isoformat(),
                        "actionType": (
                            updated.operation_type.value
                            if updated.operation_type is not None
                            else OperationType.CREATE.value
                        ),
                        "draft": _visible_draft(updated),
                    },
                )
            self.latest_state = updated
            return self._dump(updated)
        except WorkflowError as exc:
            self._record_failed_step(state, "scheduling", "scheduling_agent", sequence_no, exc)
            raise

    def _await_human_confirmation_node(self, state: AgentState) -> dict[str, Any]:
        # No external side effect before interrupt: LangGraph replays this node
        # when Command(resume=...) arrives after a process restart.
        response = interrupt({"kind": "booking_confirmation"})
        try:
            request = HitlResumeCommand.model_validate(response)
        except ValueError as exc:
            raise WorkflowError("RESUME_INPUT_INVALID", "恢复确认输入无效") from exc
        update: dict[str, object] = {
            "resume_action": request.action,
            "edited_draft": request.edited_draft,
        }
        if request.action is ResumeAction.ACCEPT and state.confirmation_token is not None:
            operation = _confirm_operation(state.operation_type)
            update["confirm_tool_call_id"] = state.confirm_tool_call_id or stable_tool_identity(
                state.run_id, operation, state.confirmation_token
            )
            update["confirm_idempotency_key"] = (
                state.confirm_idempotency_key
                or stable_idempotency_identity(
                    state.run_id, operation, state.confirmation_token
                )
            )
        updated = state.model_copy(update=update)
        self.latest_state = updated
        return self._dump(updated)

    def _resume_dispatch_node(self, state: AgentState) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=0, tool_increment=0)
        sequence_no = state.step_count + 1
        action = state.resume_action
        if action is ResumeAction.EDIT:
            updated = state.model_copy(
                update={
                    "availability_snapshot": None,
                    "schedule_candidates": [],
                    "selected_candidate_id": None,
                    "unsat_analysis": None,
                    "draft": None,
                    "confirmation_token": None,
                    "draft_expires_at": None,
                    "draft_tool_call_id": None,
                    "confirm_tool_call_id": None,
                    "confirm_idempotency_key": None,
                    "pending_request_no": None,
                    "business_result": None,
                    "loop_iteration": 0,
                    "executed_tool_fingerprints": [],
                    "status": RunStatus.RUNNING,
                    "next_route": Route.REQUIREMENT,
                }
            )
            summary = "已接收编辑请求，将重新提取需求并查询最新可用性"
        elif action is ResumeAction.REJECT:
            updated = state.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "answer_summary": "用户已拒绝预约草案。",
                    "next_route": Route.FINAL,
                }
            )
            summary = "用户拒绝预约草案，未调用写入工具"
        elif action is ResumeAction.ACCEPT:
            updated = state.model_copy(
                update={"status": RunStatus.RUNNING, "next_route": Route.FINAL}
            )
            summary = "用户已确认预约草案，准备执行受限确认"
        else:
            raise WorkflowError("RESUME_INPUT_INVALID", "恢复确认输入无效")
        updated = updated.model_copy(update={"step_count": sequence_no})
        self._record_step(
            state=updated,
            sequence_no=sequence_no,
            agent_name="deterministic",
            node_name="resume_dispatch",
            summary=summary,
            duration_ms=0,
            input_summary="Apply a validated HITL action.",
        )
        self.latest_state = updated
        return self._dump(updated)

    def _confirm_booking_node(self, state: AgentState) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=0, tool_increment=1)
        sequence_no = state.step_count + 1
        if (
            state.resume_action is not ResumeAction.ACCEPT
            or state.confirmation_token is None
            or state.confirm_tool_call_id is None
            or state.confirm_idempotency_key is None
        ):
            raise WorkflowError("CONFIRMATION_GUARD_FAILED", "确认操作未通过 HITL 校验")
        started = time.perf_counter()
        operation = _confirm_operation(state.operation_type)
        try:
            if operation == "confirm_reschedule":
                confirm = self.scheduling.tools.confirm_reschedule
            elif operation == "confirm_cancellation":
                confirm = self.scheduling.tools.confirm_cancellation
            else:
                confirm = self.scheduling.tools.confirm_booking
            outcome, confirmation = confirm(
                context=self.context,
                confirmation_token=state.confirmation_token,
                tool_call_id=state.confirm_tool_call_id,
                idempotency_key=state.confirm_idempotency_key,
            )
        except JavaToolError as exc:
            if exc.code == "TOOL_CONFLICT":
                self._record_failed_tool(
                    state=state,
                    tool_name=operation,
                    risk_level="WRITE",
                    tool_call_id=state.confirm_tool_call_id or "tool_unknown",
                    summary="预约确认被 Java 最终并发裁决拒绝",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                updated = _synchronous_conflict_replan_state(state=state, error=exc)
                updated = updated.model_copy(update={"step_count": sequence_no})
                self._record_step(
                    state=updated,
                    sequence_no=sequence_no,
                    agent_name="deterministic",
                    node_name="conflict_repair",
                    summary="同步确认冲突，保留硬约束并排除失败候选后重新规划",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    input_summary="Process server-derived synchronous conflict evidence.",
                )
                self.latest_state = updated
                return self._dump(updated)
            error = WorkflowError(exc.code, "预约确认暂不可用")
            self._record_failed_step(state, "deterministic", operation, sequence_no, error)
            raise error from exc
        if confirmation.status == "SUCCESS":
            assert confirmation.meeting_id is not None
            updated = state.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "answer_summary": "预约确认成功。",
                    "pending_request_no": None,
                    "step_count": sequence_no,
                    "tool_call_count": state.tool_call_count + 1,
                }
            )
            summary = "预约确认成功"
        else:
            assert confirmation.request_no is not None
            updated = state.model_copy(
                update={
                    "status": RunStatus.WAITING_BUSINESS_RESULT,
                    "pending_request_no": confirmation.request_no,
                    "step_count": sequence_no,
                    "tool_call_count": state.tool_call_count + 1,
                }
            )
            summary = "预约请求已进入异步排队"
        self._record_tool(state=updated, outcome=outcome)
        self._record_step(
            state=updated,
            sequence_no=sequence_no,
            agent_name="deterministic",
            node_name="confirm_booking",
            summary=summary,
            duration_ms=int((time.perf_counter() - started) * 1000),
            input_summary="Confirm an accepted booking draft with a stable idempotency key.",
        )
        if confirmation.status == "SUCCESS":
            assert confirmation.meeting_id is not None
            self.sink.emit(
                "booking.completed",
                {
                    "runId": updated.run_id,
                    "status": "SUCCESS",
                    "meetingId": confirmation.meeting_id,
                    "actionType": (state.operation_type or OperationType.CREATE).value,
                },
            )
        else:
            assert confirmation.request_no is not None
            self.sink.emit(
                "booking.pending",
                {
                    "runId": updated.run_id,
                    "status": RunStatus.WAITING_BUSINESS_RESULT.value,
                    "requestNo": confirmation.request_no,
                },
            )
        self.latest_state = updated
        return self._dump(updated)

    def _final_node(self, state: AgentState) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=0, tool_increment=0)
        sequence_no = state.step_count + 1
        if state.missing_fields:
            answer = "请补充：" + "、".join(state.missing_fields)
            run_status = RunStatus.WAITING_USER_INPUT
        elif state.policy_result is not None and state.intent is Intent.QUERY_POLICY:
            answer = state.policy_result.summary
            run_status = RunStatus.SUCCEEDED
        elif state.status is RunStatus.CANCELLED:
            answer = state.answer_summary or "用户已拒绝预约草案。"
            run_status = RunStatus.CANCELLED
        elif state.status is RunStatus.WAITING_USER_INPUT:
            answer = state.answer_summary or "请调整约束后重试。"
            run_status = RunStatus.WAITING_USER_INPUT
        elif state.unsat_analysis is not None:
            answer = state.unsat_analysis.summary
            run_status = RunStatus.SUCCEEDED
        else:
            answer = state.answer_summary or "已完成结构化处理。"
            run_status = RunStatus.SUCCEEDED
        updated = state.model_copy(
            update={
                "step_count": sequence_no,
                "answer_summary": answer,
                "status": run_status,
                "next_route": Route.FINAL,
            }
        )
        self._record_step(
            state=updated,
            sequence_no=sequence_no,
            agent_name="deterministic",
            node_name="compose_final",
            summary=answer,
            duration_ms=0,
            input_summary="Compose a safe terminal summary.",
        )
        self.latest_state = updated
        return self._dump(updated)

    def _record_agent_step(
        self,
        *,
        state: AgentState,
        agent_name: str,
        node_name: str,
        input_summary: str,
        execute: Callable[[AgentState], tuple[AgentState, str, int]],
    ) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=1, tool_increment=0)
        sequence_no = state.step_count + 1
        started = time.perf_counter()
        try:
            updated, summary, model_calls = execute(state)
            if state.model_call_count + model_calls > self.settings.agent_max_model_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到模型调用上限")
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + model_calls,
                }
            )
            self._record_step(
                state=updated,
                sequence_no=sequence_no,
                agent_name=agent_name,
                node_name=node_name,
                summary=summary,
                duration_ms=int((time.perf_counter() - started) * 1000),
                input_summary=input_summary,
            )
            self.latest_state = updated
            return self._dump(updated)
        except WorkflowError as exc:
            self._record_failed_step(state, agent_name, node_name, sequence_no, exc)
            raise

    def _record_step(
        self,
        *,
        state: AgentState,
        sequence_no: int,
        agent_name: str,
        node_name: str,
        summary: str,
        duration_ms: int,
        input_summary: str,
    ) -> None:
        step_id = _new_id("step")
        self.repository.record_step(
            step_id=step_id,
            run_id=state.run_id,
            sequence_no=sequence_no,
            agent_name=agent_name,
            node_name=node_name,
            status="SUCCEEDED",
            input_summary=input_summary,
            output_summary=summary,
            duration_ms=duration_ms,
        )
        self.sink.emit(
            "agent.step",
            {
                "runId": state.run_id,
                "stepId": step_id,
                "sequenceNo": sequence_no,
                "agentName": agent_name,
                "nodeName": node_name,
                "status": "SUCCEEDED",
                "summary": summary,
                "durationMs": duration_ms,
            },
        )

    def _record_tool(self, *, state: AgentState, outcome: ToolOutcome) -> None:
        self.repository.record_tool_call(
            tool_call_id=outcome.tool_call_id,
            run_id=state.run_id,
            tool_name=outcome.tool_name,
            risk_level=outcome.risk_level,
            sanitized_args=_sanitized_tool_args(outcome, state),
            result_summary=outcome.summary,
            status="SUCCEEDED",
            duration_ms=outcome.duration_ms,
        )
        self.sink.emit(
            "tool.call",
            {
                "runId": state.run_id,
                "toolCallId": outcome.tool_call_id,
                "toolName": outcome.tool_name,
                "riskLevel": outcome.risk_level,
                "status": "SUCCEEDED",
                "summary": outcome.summary,
                "durationMs": outcome.duration_ms,
            },
        )

    def _record_loop_event(self, state: AgentState, event: dict[str, object]) -> None:
        remaining = event["remainingBudget"]
        assert isinstance(remaining, dict)
        feedback = event["feedbackCodes"]
        assert isinstance(feedback, list)
        iteration = event["iteration"]
        replan_count = event["replanCount"]
        assert isinstance(iteration, int) and isinstance(replan_count, int)
        sequence = state.step_count * 10 + iteration + (
            5 if event["phase"] == "VERIFY" else 0
        )
        self.repository.record_loop_event(
            run_id=state.run_id,
            sequence_no=sequence,
            phase=str(event["phase"]),
            iteration=iteration,
            decision=str(event["decision"]),
            feedback_codes=[str(item) for item in feedback],
            replan_count=replan_count,
            remaining_model_calls=int(remaining["modelCalls"]),
            remaining_tool_calls=int(remaining["toolCalls"]),
            stop_reason=str(event["stopReason"]) if event["stopReason"] else None,
        )
        self.sink.emit("agent.loop", event)

    def _record_failed_tool(
        self,
        *,
        state: AgentState,
        tool_name: str,
        risk_level: str,
        tool_call_id: str,
        summary: str,
        duration_ms: int,
    ) -> None:
        self.repository.record_tool_call(
            tool_call_id=tool_call_id,
            run_id=state.run_id,
            tool_name=tool_name,
            risk_level=risk_level,
            sanitized_args={"riskGuard": "HITL_ACCEPTED"},
            result_summary=summary,
            status="FAILED",
            duration_ms=duration_ms,
        )
        self.sink.emit(
            "tool.call",
            {
                "runId": state.run_id,
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "riskLevel": risk_level,
                "status": "FAILED",
                "summary": summary,
                "durationMs": duration_ms,
            },
        )

    def _record_failed_step(
        self,
        state: AgentState,
        agent_name: str,
        node_name: str,
        sequence_no: int,
        error: WorkflowError,
    ) -> None:
        step_id = _new_id("step")
        self.repository.record_step(
            step_id=step_id,
            run_id=state.run_id,
            sequence_no=sequence_no,
            agent_name=agent_name,
            node_name=node_name,
            status="FAILED",
            input_summary="A controlled workflow error occurred.",
            output_summary=error.message,
            duration_ms=0,
            error_code=error.code,
        )
        self.sink.emit(
            "agent.step",
            {
                "runId": state.run_id,
                "stepId": step_id,
                "sequenceNo": sequence_no,
                "agentName": agent_name,
                "nodeName": node_name,
                "status": "FAILED",
                "summary": error.message,
                "durationMs": 0,
            },
        )

    def _ensure_limits(
        self, state: AgentState, *, model_increment: int, tool_increment: int
    ) -> None:
        if (
            state.step_count + 1 > self.settings.agent_max_graph_nodes
            or state.model_call_count + model_increment > self.settings.agent_max_model_calls
            or state.tool_call_count + tool_increment > self.settings.agent_max_tool_calls
        ):
            raise WorkflowError("BUDGET_EXHAUSTED", "已达到图步骤或模型/工具调用预算")

    @staticmethod
    def _route_from_start(state: AgentState) -> str:
        if (
            state.business_result is not None
            and state.business_result.status.value == "CONFLICT"
            and state.conflict_repair_feedback is not None
        ):
            return "scheduling_agent"
        return "supervisor_route"

    @staticmethod
    def _route_after_supervisor(state: AgentState) -> str:
        if state.next_route is Route.POLICY:
            return "policy_agent"
        if state.next_route is Route.REQUIREMENT:
            return "requirement_agent"
        return "compose_final"

    @staticmethod
    def _route_after_requirement(state: AgentState) -> str:
        if state.next_route is Route.POLICY:
            return "policy_agent"
        if state.next_route is Route.SCHEDULING:
            return "scheduling_agent"
        return "compose_final"

    @staticmethod
    def _route_after_policy(state: AgentState) -> str:
        return "scheduling_agent" if state.next_route is Route.SCHEDULING else "compose_final"

    @staticmethod
    def _route_after_scheduling(state: AgentState) -> str:
        return (
            "await_human_confirmation"
            if state.status is RunStatus.WAITING_CONFIRMATION
            else "compose_final"
        )

    @staticmethod
    def _route_after_resume_dispatch(state: AgentState) -> str:
        if state.resume_action is ResumeAction.ACCEPT:
            return "confirm_booking"
        if state.resume_action is ResumeAction.EDIT:
            return "requirement_agent"
        return "compose_final"

    @staticmethod
    def _route_after_confirmation(state: AgentState) -> str:
        if state.next_route is Route.SCHEDULING:
            return "scheduling_agent"
        return "end" if state.status is RunStatus.WAITING_BUSINESS_RESULT else "compose_final"


def _synchronous_conflict_replan_state(
    *, state: AgentState, error: JavaToolError
) -> AgentState:
    if state.replan_count >= 2:
        return state.model_copy(
            update={
                "status": RunStatus.WAITING_USER_INPUT,
                "answer_summary": "连续并发冲突已达到重规划上限，请调整时间或会议室。",
                "next_route": Route.FINAL,
                "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                "confirmation_token": None,
                "draft": None,
                "tool_call_count": state.tool_call_count + 1,
            }
        )
    failed_candidate = state.selected_candidate_id
    if failed_candidate is None:
        raise WorkflowError("CONFLICT_REPLAN_INVALID", "同步冲突缺少失败候选")
    request = state.meeting_request
    if request is None:
        raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
    excluded = list(dict.fromkeys([*state.excluded_candidate_ids, failed_candidate]))
    preserved = [
        f"durationMinutes={request.duration_minutes}",
        f"requiredParticipantCount={len(request.required_participants)}",
        f"minimumCapacity={request.minimum_capacity or 1}",
    ]
    if request.time_window is not None:
        preserved.append(
            "timeWindow="
            f"{request.time_window.start.isoformat()}/{request.time_window.end.isoformat()}"
        )
    preserved.extend(
        f"hard:{constraint.type}={constraint.value}"
        for constraint in request.hard_constraints
    )
    feedback = ConflictRepairFeedbackState(
        conflict_type=error.details.get("conflict.type", "BOOKING_CONFLICT"),
        failed_candidate_id=failed_candidate,
        preserved_constraints=preserved[:20],
        excluded_candidate_ids=excluded,
        replan_count=state.replan_count + 1,
        room_id=_positive_int(error.details.get("conflict.roomId")),
        slots=_slot_indexes(error.details.get("conflict.slots")),
        reason="Java 最终并发裁决冲突，刷新事实并生成不同候选。",
    )
    return state.model_copy(
        update={
            "availability_snapshot": None,
            "schedule_candidates": [],
            "selected_candidate_id": None,
            "unsat_analysis": None,
            "draft": None,
            "confirmation_token": None,
            "draft_expires_at": None,
            "draft_tool_call_id": None,
            "confirm_tool_call_id": None,
            "confirm_idempotency_key": None,
            "pending_request_no": None,
            "business_result": None,
            "resume_action": None,
            "answer_summary": None,
            "status": RunStatus.RUNNING,
            "next_route": Route.SCHEDULING,
            "replan_count": state.replan_count + 1,
            "excluded_candidate_ids": excluded,
            "conflict_repair_feedback": feedback,
            "loop_iteration": 0,
            "executed_tool_fingerprints": [],
            "tool_call_count": state.tool_call_count + 1,
        }
    )


def _positive_int(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _confirm_operation(operation_type: OperationType | None) -> str:
    return {
        OperationType.CREATE: "confirm_booking",
        OperationType.RESCHEDULE: "confirm_reschedule",
        OperationType.CANCEL: "confirm_cancellation",
        None: "confirm_booking",
    }[operation_type]


def _slot_indexes(value: str | None) -> list[int]:
    if value is None:
        return []
    slots = [int(item) for item in value.split(",") if item.isdigit()]
    return [slot for slot in slots if 0 <= slot <= 47][:48]


def _loop_event(
    *,
    state: AgentState,
    phase: str,
    iteration: int,
    decision: str,
    model_budget: int,
    tool_budget: int,
) -> dict[str, object]:
    feedback_codes = (
        state.requirement_feedback.codes if state.requirement_feedback is not None else []
    )
    if state.conflict_repair_feedback is not None:
        feedback_codes = [
            *feedback_codes,
            state.conflict_repair_feedback.conflict_type,
        ]
    return {
        "runId": state.run_id,
        "phase": phase,
        "iteration": iteration,
        "decision": decision,
        "replanCount": state.replan_count,
        "feedbackCodes": feedback_codes,
        "stopReason": state.stop_reason,
        "remainingBudget": {
            "modelCalls": max(0, model_budget - state.model_call_count),
            "toolCalls": max(0, tool_budget - state.tool_call_count),
        },
        "model": state.configured_model,
        "promptVersion": state.prompt_version,
        "schemaVersion": state.schema_version,
        "tokenUsage": {
            "inputTokens": state.input_tokens,
            "outputTokens": state.output_tokens,
            "cacheHitTokens": state.cache_hit_tokens,
            "cacheMissTokens": state.cache_miss_tokens,
        },
    }
def _sanitized_tool_args(outcome: ToolOutcome, state: AgentState) -> dict[str, object]:
    request = state.meeting_request
    if outcome.tool_name == "resolve_employees":
        return {"nameCount": len(request.required_participants) if request is not None else 0}
    if outcome.tool_name == "get_employee_free_busy":
        return {"employeeCount": len(state.resolved_employees)}
    if outcome.tool_name == "search_available_rooms":
        return {
            "minimumCapacity": request.minimum_capacity if request is not None else None,
            "featureCount": len(request.required_features) if request is not None else 0,
        }
    if outcome.tool_name in {
        "create_booking_draft",
        "create_reschedule_draft",
        "create_cancellation_preview",
    }:
        return {"candidateId": state.selected_candidate_id, "riskGuard": "SOLVER_VALIDATED"}
    if outcome.tool_name in {"confirm_booking", "confirm_reschedule", "confirm_cancellation"}:
        # Never place confirmation token or idempotency key into Trace.
        return {"riskGuard": "HITL_ACCEPTED"}
    return {}


def _visible_draft(state: AgentState) -> dict[str, object]:
    draft = state.draft
    if draft is None:
        return {}
    if isinstance(draft, CreateDraftView):
        return draft.draft.model_dump(by_alias=True, mode="json")
    if isinstance(draft, RescheduleDraftView):
        return {
            "originalMeeting": draft.original_meeting.model_dump(by_alias=True, mode="json"),
            "proposedMeeting": draft.proposed_meeting.model_dump(by_alias=True, mode="json"),
        }
    assert isinstance(draft, CancellationDraftView)
    return {"meeting": draft.meeting.model_dump(by_alias=True, mode="json")}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def build_workflow_run(
    *,
    settings: Settings,
    repository: MetadataRepository,
    provider: ModelProvider,
    retriever: PolicyRetriever,
    tools: JavaReadToolClient,
    context: AgentContext,
    checkpoint_saver: BaseCheckpointSaver[Any],
) -> WorkflowRun:
    runner = StructuredModelRunner()
    return WorkflowRun(
        settings=settings,
        repository=repository,
        supervisor=SupervisorAgent(provider=provider, runner=runner),
        requirement=RequirementAgent(provider=provider, runner=runner),
        policy=PolicyAgent(provider=provider, runner=runner, retriever=retriever),
        scheduling=SchedulingAgent(
            provider=provider,
            runner=runner,
            tools=tools,
            max_model_calls=settings.agent_max_model_calls,
            max_tool_calls=settings.agent_max_tool_calls,
        ),
        context=context,
        checkpoint_saver=checkpoint_saver,
    )
