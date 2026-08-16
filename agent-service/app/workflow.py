"""Day 5 LangGraph orchestration for exactly four specialised runtime Agents.

``SupervisorAgent``, ``RequirementAgent``, ``PolicyAgent`` and
``SchedulingAgent`` remain the only runtime Agents.  Solver, HITL and booking
operations below are deterministic graph nodes; model output never receives a
general write Tool surface.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

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
    RequirementFeedback,
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
    ModelToolCall,
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
    ClarificationResponse,
    ConflictRepairFeedbackState,
    Constraint,
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
    PostMeetingDraft,
    PostMeetingDraftRequest,
    RequirementDraft,
    RequirementExtraction,
    RequirementFeedbackState,
    RequirementItem,
    RequirementSlotStatus,
    RescheduleDraftView,
    ResumeAction,
    RoomAvailability,
    Route,
    RunStatus,
    SchedulingPreferences,
    SchedulingProblem,
    SupervisorDecision,
    TimeWindow,
    UnsatAnalysis,
)
from app.security import AgentContext
from app.tools.java import (
    CreateBookingDraftInput,
    FreeBusyInput,
    JavaReadToolClient,
    JavaToolError,
    RecentMeetingInput,
    RescheduleDraftInput,
    SearchRoomsInput,
    ToolOutcome,
    stable_idempotency_identity,
    stable_tool_identity,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "meeting-agent-prompts-v11"
SCHEMA_VERSION = "meeting-agent-state-v7"
POST_MEETING_PROMPT_VERSION: Literal["post-meeting-analysis-v1"] = "post-meeting-analysis-v1"
POST_MEETING_SCHEMA_VERSION: Literal["post-meeting-draft-v1"] = "post-meeting-draft-v1"

SUPERVISOR_PROMPT = """You are the Supervisor Agent for an enterprise meeting scheduler.
Only classify the current objective. Initial routes are POLICY, REQUIREMENT, or CLARIFICATION.
Never route directly to SCHEDULING, HITL, WAIT_BUSINESS_RESULT, FINAL, or FAIL. POLICY is only a
pure rule/restriction/permission question without a mutation request. REQUIREMENT covers create,
find time, room recommendation, modify, cancel, and explicit preference updates. evidence must be
one continuous verbatim substring of USER_MESSAGE. Return only the schema JSON; no reasoning."""

REQUIREMENT_PROMPT = """You are the Requirement Agent. Extract only source-supported facts into
RequirementDraft. Missing facts remain null/empty. Never invent names from a headcount. Copy named
participants exactly. timeWindow is the allowed candidate-search window, while durationMinutes is
the length of one meeting. When the user supplies both, preserve them independently. Derive duration
from a fixed start/end interval only when no separate duration was supplied and the text does not
describe an allowed range with words such as 之间、以内、范围内 or 时段内.
"给出候选方案/不要替我确认" describes the mandatory HITL behavior; when the user asks to arrange
a meeting with participants and duration, it remains CREATE_MEETING rather than RECOMMEND_ROOM.
Supported features: 白板=WHITEBOARD, 大屏/投屏=LARGE_SCREEN, 视频会议=VIDEO_CONFERENCE,
投影仪=PROJECTOR; English whiteboard/large screen/video conference/projector use the same
canonical values. “只查时间/一起空出” is FIND_COMMON_TIME; “只推荐/不要预约” with a room
request is RECOMMEND_ROOM. “我的小组/同组人员” must be participantScope=MY_DEPARTMENT and must not
contain invented member names. title and meetingType may be null because deterministic code owns
safe defaults. On a continuation turn, extract only facts present in the current USER_MESSAGE; do
not copy the previous roster. Expressions such as 去掉、不参加、请假不会来 are participant removal
instructions that deterministic code applies to the verified previous roster.
For MODIFY_MEETING, targetMeetingReference identifies the existing meeting (for example the old
date/time/title before 改到), while pendingStartAt/timeWindow describe the destination. Never put
the old target selector into the destination. “27号同一时间” means the destination date is the
27th and its clock is inherited from the explicit old target clock; set pendingStartAt accordingly.
“异常重排/资源失效/会议室不可用” is MODIFY_MEETING. Preserve an explicit 会议 ID/meetingId as
targetMeetingId. Unless the user explicitly changes a constraint, inherit the original time,
duration, required/optional participants and room features; the failed original room is excluded.
The deterministic runtime, not you, resolves a first-person participant such as “我和李四” from
the authenticated session. Do not invent a name or identity for “我”.
Every populated user-derived field needs fieldEvidence whose source is a continuous verbatim
substring of USER_MESSAGE. Do not call tools, create drafts, confirm, or expose reasoning."""

REQUIREMENT_REPAIR_PROMPT = """Repair RequirementDraft using only USER_MESSAGE,
SERVER_REQUEST_TIME, and EVALUATOR_FEEDBACK. Correct only rejected fields. Unsupported facts must
be null/empty. Return only the corrected schema JSON; no reasoning."""

POST_MEETING_ANALYSIS_PROMPT = """You are the existing Requirement Agent operating in the
isolated POST_MEETING_ANALYSIS mode. Convert only the authenticated meeting snapshot and submitted
transcript into a PostMeetingDraft. Summarize the meeting background, discussion, and conclusion;
extract at most 20 explicit decisions and at most 50 concrete action items. Do not invent facts,
decisions, deadlines, or employee IDs. assigneeEmployeeId may only be copied from the participant
allowlist in POST_MEETING_INPUT; use null whenever the transcript does not identify exactly one
allowlisted participant. dueAt must be null unless the transcript provides a concrete deadline,
and any populated timestamp must use the +08:00 offset. This mode must not plan scheduling, call
tools, or claim that the draft has been accepted or written. Return only the schema JSON; no
reasoning."""

CLARIFICATION_PROMPT = """You are the existing Supervisor Agent. Turn the supplied verified
clarification contract into concise, friendly Chinese for a non-technical user. Explain what is
missing or inconsistent, then ask for exactly the requested input or present the supplied choices.
Use only VERIFIED_FACTS, EXPLANATIONS, REQUESTED_INPUTS and FALLBACK_MESSAGE. Never mention internal
codes, validators, schemas, prompts or traces. Never invent a person, time, room, conflict or
business result. Never claim a meeting was created, confirmed, changed or cancelled. Return schema
JSON only."""


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
        clarification = None
        if route is Route.CLARIFICATION:
            clarification, clarification_completions = _compose_clarification(
                provider=self.provider,
                issue_codes=["OBJECTIVE_NOT_UNDERSTOOD"],
                request=None,
            )
            completions.extend(clarification_completions)
        updated = _apply_completions(state, completions)
        return (
            updated.model_copy(
                update={
                    "next_route": route,
                    "intent": intent,
                    "answer_summary": clarification,
                }
            ),
            decision.summary,
            len(completions),
        )


@dataclass(frozen=True)
class RequirementAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    tools: JavaReadToolClient | None = None
    context: AgentContext | None = None

    evaluator: RequirementEvaluator = field(default_factory=RequirementEvaluator)
    fidelity: SourceFidelityEvaluator = field(default_factory=SourceFidelityEvaluator)
    normalizer: RequirementNormalizer = field(default_factory=RequirementNormalizer)

    def analyze_post_meeting(
        self, request: PostMeetingDraftRequest
    ) -> tuple[PostMeetingDraft, list[ModelCompletion]]:
        """Run the same Requirement Agent in an isolated structured-analysis mode."""

        return self.runner.invoke_with_count(
            provider=self.provider,
            request=ModelRequest(
                agent_name="requirement",
                system_prompt=POST_MEETING_ANALYSIS_PROMPT,
                user_prompt=(
                    "POST_MEETING_INPUT="
                    + json.dumps(
                        request.model_dump(by_alias=True, mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
                schema_name=PostMeetingDraft.__name__,
                schema=PostMeetingDraft.model_json_schema(by_alias=True),
            ),
            output_type=PostMeetingDraft,
        )

    def execute(self, state: AgentState) -> tuple[AgentState, str, int, list[ToolOutcome]]:
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
        draft = _apply_current_user_participation(draft, state.message)
        if state.intent is not None and (
            not state.continuation_turn or not _source_changes_intent(state.message)
        ):
            draft = draft.model_copy(update={"intent": state.intent})
        if (
            state.continuation_turn
            and state.requirement_draft is not None
            and draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        ):
            draft = draft.model_copy(
                update={
                    "target_meeting_id": (
                        draft.target_meeting_id
                        or state.requirement_draft.target_meeting_id
                    ),
                    "target_meeting_reference": (
                        draft.target_meeting_reference
                        or state.requirement_draft.target_meeting_reference
                    ),
                }
            )
        feedback = _continuation_fidelity_feedback(
            self.fidelity.evaluate(draft, state.message), state=state
        )
        if (
            feedback is not None
            and feedback.codes == ["INTENT_SOURCE_MISMATCH"]
            and state.intent is not None
        ):
            # The Supervisor's high-confidence verb anchors already decide
            # the mutation boundary.  “Give candidates / do not confirm” is
            # HITL guidance, not a room-recommendation intent, so avoid an
            # unnecessary model repair that could rewrite otherwise faithful
            # evidence strings.
            draft = draft.model_copy(update={"intent": state.intent})
            feedback = _continuation_fidelity_feedback(
                self.fidelity.evaluate(draft, state.message), state=state
            )
        if (
            feedback is None
            and not state.continuation_turn
            and not (draft.pending_start_at is not None and draft.duration_minutes is None)
        ):
            initial_request, _ = self.normalizer.normalize(draft, source=state.message)
            feedback = self.evaluator.evaluate(initial_request, request_time=state.request_time)
        if feedback is not None and feedback.repairable:
            extraction, repair_completions = _model_output_with_count(
                provider=self.provider,
                runner=self.runner,
                agent_name="requirement",
                system_prompt=REQUIREMENT_REPAIR_PROMPT,
                user_prompt=(
                    f"{prompt}\nEVALUATOR_FEEDBACK={feedback.model_dump_json(by_alias=True)}"
                ),
                output_type=RequirementExtraction,
            )
            completions.extend(repair_completions)
            draft = extraction.requirement_draft
            draft = _apply_explicit_meeting_defaults(
                draft, state.message, request_time=state.request_time
            )
            draft = _apply_current_user_participation(draft, state.message)
            if state.intent is not None and (
                not state.continuation_turn or not _source_changes_intent(state.message)
            ):
                draft = draft.model_copy(update={"intent": state.intent})
            if (
                state.continuation_turn
                and state.requirement_draft is not None
                and draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
            ):
                draft = draft.model_copy(
                    update={
                        "target_meeting_id": (
                            draft.target_meeting_id
                            or state.requirement_draft.target_meeting_id
                        ),
                        "target_meeting_reference": (
                            draft.target_meeting_reference
                            or state.requirement_draft.target_meeting_reference
                        ),
                    }
                )
            feedback = _continuation_fidelity_feedback(
                self.fidelity.evaluate(draft, state.message), state=state
            )
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
                feedback = _continuation_fidelity_feedback(
                    self.fidelity.evaluate(draft, state.message), state=state
                )
        previous_draft = state.requirement_draft if state.continuation_turn else None
        draft = _resolve_ambiguous_pending_start(previous_draft, draft, state.message)
        draft = _apply_participant_delta(previous_draft, draft, state.message)
        draft = _apply_constraint_delta(previous_draft, draft, state.message)
        draft = _apply_unsat_recommended_time(
            previous_draft,
            draft,
            source=state.message,
            analysis=state.unsat_analysis,
        )
        merged = _merge_requirement_drafts(previous_draft, draft, source=state.message)
        if (
            merged.time_window is None
            and merged.pending_start_at is not None
            and not merged.pending_start_ambiguous
            and merged.duration_minutes is not None
        ):
            merged = merged.model_copy(
                update={
                    "time_window": TimeWindow(
                        start=merged.pending_start_at,
                        end=merged.pending_start_at + timedelta(minutes=merged.duration_minutes),
                    )
                }
            )
        outcomes: list[ToolOutcome] = []
        resolved_employees = list(state.resolved_employees)
        if previous_draft is not None and (
            merged.required_participant_names != previous_draft.required_participant_names
        ):
            final_names = set(merged.required_participant_names)
            resolved_employees = [item for item in resolved_employees if item.name in final_names]
        if (
            merged.participant_scope == "MY_DEPARTMENT"
            and not merged.required_participant_names
            and not merged.participant_list_modified
        ):
            if self.tools is None or self.context is None:
                feedback = feedback or _requirement_feedback(
                    "PARTICIPANT_SCOPE_UNRESOLVED", "无法读取当前用户所属小组。"
                )
            else:
                try:
                    outcome = self.tools.resolve_participant_scope(
                        context=self.context,
                        tool_call_id=stable_tool_identity(
                            state.run_id,
                            "resolve_participant_scope",
                            f"requirement-revision-{state.requirement_revision + 1}",
                        ),
                    )
                    members = _scope_members(outcome)
                    if not members:
                        feedback = _requirement_feedback(
                            "PARTICIPANT_SCOPE_UNRESOLVED", "当前小组没有可用于排期的在职成员。"
                        )
                    else:
                        outcomes.append(outcome)
                        resolved_employees = members
                        scope_includes_current_user = any(
                            item.employee_id == self.context.user_id for item in members
                        )
                        merged = merged.model_copy(
                            update={
                                "required_participant_names": [item.name for item in members],
                                "includes_current_user": (
                                    merged.includes_current_user
                                    and not scope_includes_current_user
                                ),
                                "minimum_capacity": max(
                                    merged.minimum_capacity or 1,
                                    len({item.employee_id for item in members}),
                                ),
                            }
                        )
                except JavaToolError:
                    feedback = _requirement_feedback(
                        "PARTICIPANT_SCOPE_UNRESOLVED", "无法从通讯录确定当前小组成员。"
                    )

        request, report = self.normalizer.normalize(merged, source=state.message)
        if (
            merged.duration_minutes is None
            and "durationMinutes" in report.derived_fields
            and _source_describes_fixed_interval(state.message)
        ):
            merged = merged.model_copy(update={"duration_minutes": request.duration_minutes})
        semantic = self.evaluator.evaluate(request, request_time=state.request_time)
        if (
            merged.pending_start_at is not None
            and not merged.pending_start_ambiguous
            and merged.duration_minutes is None
            and semantic is not None
            and semantic.codes == ["TIME_WINDOW_REQUIRED"]
        ):
            semantic = None
        if semantic is not None:
            feedback = semantic
        if merged.time_window is None and _source_has_ambiguous_single_time(state.message):
            feedback = _requirement_feedback(
                "TIME_MERIDIEM_AMBIGUOUS", "单独的几点存在上午和下午两种解释。"
            )
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
        missing_fields = list(semantic_missing)
        if request.intent in {
            Intent.CREATE_MEETING,
            Intent.FIND_COMMON_TIME,
            Intent.RECOMMEND_ROOM,
        }:
            if merged.time_window is None and (
                merged.pending_start_at is None or merged.pending_start_ambiguous
            ):
                missing_fields.append("timeWindow")
            if merged.duration_minutes is None:
                missing_fields.append("durationMinutes")
            if (
                request.intent in {Intent.CREATE_MEETING, Intent.FIND_COMMON_TIME}
                and not merged.required_participant_names
                and merged.participant_scope != "ORGANIZER_ONLY"
            ):
                missing_fields.append("requiredParticipants")
        if request.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING} and (
            request.target_meeting_id is None and not request.target_meeting_reference
        ):
            missing_fields.append("targetMeeting")
        missing_fields = list(dict.fromkeys(missing_fields))
        optional_closed = state.optional_requirements_closed or _closes_optional_requirements(
            state.message
        )
        items = _requirement_items(
            draft=merged,
            request=request,
            missing_fields=missing_fields,
            source=state.message,
            previous_items=state.requirement_items,
            optional_closed=optional_closed,
        )
        next_route = (
            Route.CLARIFICATION
            if missing_fields
            else Route.POLICY
            if merged.needs_policy
            else Route.SCHEDULING
        )
        clarification = None
        if missing_fields:
            clarification = _format_requirement_clarification(items)
        return (
            _apply_completions(state, completions).model_copy(
                update={
                    "intent": request.intent,
                    "meeting_request": request,
                    "requirement_draft": merged,
                    "requirement_items": items,
                    "requirement_revision": state.requirement_revision + 1,
                    "continuation_turn": False,
                    "optional_requirements_closed": optional_closed,
                    "resolved_employees": resolved_employees,
                    "missing_fields": missing_fields,
                    "requirement_feedback": feedback_state,
                    "normalization_report": report,
                    "next_route": next_route,
                    "answer_summary": clarification,
                }
            ),
            merged.summary if feedback is None else feedback.summary,
            len(completions),
            outcomes,
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
            return (
                state.model_copy(update={"policy_result": result, "citations": []}),
                result.summary,
                0,
            )

        candidate_evidence = [
            {
                "chunkId": chunk.chunk_id,
                "title": chunk.title,
                "headingPath": list(chunk.heading_path),
                "page": chunk.page,
                "content": chunk.content,
            }
            for chunk in candidates[:5]
        ]
        selection, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="policy",
            system_prompt=(
                "You are the Policy Agent. Answer only from the supplied RETRIEVED_EVIDENCE "
                "content. Select only chunk IDs whose content directly supports the answer. "
                "If none of the supplied chunks answers the question, return selectedChunkIds "
                "as [] and explicitly say that no verifiable policy evidence was found. Never "
                "infer a rule from a title, invent a citation, or make a booking decision."
            ),
            user_prompt=(
                f"QUESTION={state.message}\nRETRIEVED_EVIDENCE="
                f"{json.dumps(candidate_evidence, ensure_ascii=False, separators=(',', ':'))}"
            ),
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
        if not citations:
            selection = selection.model_copy(
                update={
                    "answer_summary": "未找到可验证的会议制度证据。",
                    "confidence": 0.0,
                    "constraints": [],
                }
            )
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
        deterministic_conflict_replan = state.conflict_repair_feedback is not None
        # The failed confirm already gives us a trusted, structured reason to
        # replan. Re-read the canonical Java facts and run the deterministic
        # solver directly so a late concurrency conflict cannot fail merely
        # because the preceding multi-turn conversation consumed its LLM
        # budget.
        max_iterations = 0 if deterministic_conflict_replan else 4
        if deterministic_conflict_replan:
            loop_iteration += 1
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
                required_tools = _missing_read_tools(
                    request=request,
                    resolved=resolved,
                    free_busy_data=free_busy_data,
                    rooms_data=rooms_data,
                    recent_data=recent_data,
                )
                messages.append(
                    ToolLoopMessage(
                        role="user",
                        content=(
                            'VERIFY_FEEDBACK={"codes":["REQUIRED_FACTS_MISSING"],'
                            '"instruction":"Call the listed READ tools now.",'
                            f'"requiredTools":{json.dumps(required_tools)}}}'
                        ),
                    )
                )
                continue
            mutation_intent = request.intent in {
                Intent.MODIFY_MEETING,
                Intent.CANCEL_MEETING,
            }
            ordered_calls = sorted(
                tool_response.tool_calls,
                key=lambda call: (
                    0 if mutation_intent and call.name == "get_recent_meeting" else 1
                ),
            )
            for call in ordered_calls:
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
                    if not isinstance(unresolved, list) or any(
                        not isinstance(item, str) for item in unresolved
                    ):
                        raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
                    if unresolved or len(resolved) < len(names):
                        resolved_names = {item.name for item in resolved}
                        unresolved_names = list(
                            dict.fromkeys(
                                [
                                    *unresolved,
                                    *[name for name in names if name not in resolved_names],
                                ]
                            )
                        )
                        answer, clarification_completions = _compose_clarification(
                            provider=self.provider,
                            issue_codes=["EMPLOYEE_UNRESOLVED"],
                            request=request,
                            extra_facts=["无法匹配的姓名为" + "、".join(unresolved_names)],
                        )
                        tool_usage.extend(clarification_completions)
                        model_calls += len(clarification_completions)
                        return (
                            _apply_completions(state, tool_usage).model_copy(
                                update={
                                    "resolved_employees": resolved,
                                    "missing_fields": ["requiredParticipants"],
                                    "answer_summary": answer,
                                    "status": RunStatus.WAITING_USER_INPUT,
                                    "next_route": Route.FINAL,
                                    "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                                    "loop_iteration": loop_iteration,
                                    "executed_tool_fingerprints": sorted(fingerprints),
                                }
                            ),
                            answer,
                            outcomes,
                            model_calls,
                        )
                elif call.name == "get_employee_free_busy":
                    free_busy_data = gated_result.outcome.data
                elif call.name == "search_available_rooms":
                    rooms_data = gated_result.outcome.data
                elif call.name == "get_recent_meeting":
                    recent_data = gated_result.outcome.data
                    matches, visible_meetings = _resolve_target_meeting(
                        recent_data,
                        request=request,
                        message=state.message,
                        request_time=state.request_time,
                    )
                    if len(matches) != 1:
                        answer = _target_meeting_clarification(
                            matches=matches,
                            visible_meetings=visible_meetings,
                        )
                        return (
                            _apply_completions(state, tool_usage).model_copy(
                                update={
                                    "missing_fields": ["uniqueTargetMeeting"],
                                    "answer_summary": answer,
                                    "status": RunStatus.WAITING_USER_INPUT,
                                    "next_route": Route.FINAL,
                                    "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                                    "loop_iteration": loop_iteration,
                                    "executed_tool_fingerprints": sorted(fingerprints),
                                }
                            ),
                            answer,
                            outcomes,
                            model_calls,
                        )
                    recent = matches[0]
                    state, request = _hydrate_mutation_target(
                        state=state,
                        request=request,
                        meeting=recent,
                        recent_data=recent_data,
                    )
                    resolved = [
                        Participant(name=item.display_name, employee_id=item.employee_id)
                        for item in recent.participants
                        if item.participant_type == "REQUIRED"
                    ]
                    messages[0] = ToolLoopMessage(
                        role="system",
                        content=_scheduling_system_prompt(state=state, context=context),
                    )
            if _read_facts_ready(request, free_busy_data, rooms_data, recent_data):
                break
        # A model may stop after only part of the canonical READ plan even
        # after bounded verifier feedback. Complete the remaining free-busy
        # and room reads deterministically from the already validated request
        # instead of asking the user to type “continue”. These calls still go
        # through the same Tool gate, context checks, idempotency and Trace.
        for fallback_index in range(4):
            if _read_facts_ready(request, free_busy_data, rooms_data, recent_data):
                break
            missing = _missing_read_tools(
                request=request,
                resolved=resolved,
                free_busy_data=free_busy_data,
                rooms_data=rooms_data,
                recent_data=recent_data,
            )
            tool_name = next(
                (
                    item
                    for item in missing
                    if item
                    in {
                        "get_recent_meeting",
                        "get_employee_free_busy",
                        "search_available_rooms",
                    }
                ),
                None,
            )
            if tool_name is None:
                break
            if state.tool_call_count + len(outcomes) + 1 > self.max_tool_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "调度工具调用预算已耗尽")
            call = _canonical_fact_read_call(
                name=tool_name,
                request=request,
                resolved=resolved,
                context=context,
                index=fallback_index,
            )
            try:
                gated_result = gate.execute(
                    call=call,
                    state=state,
                    context=context,
                    resolved_employees=resolved,
                    fingerprints=fingerprints,
                )
            except ToolGateError as exc:
                raise WorkflowError(exc.code, "确定性事实补全未通过安全校验") from exc
            outcomes.append(gated_result.outcome)
            fingerprints.add(gated_result.fingerprint)
            if tool_name == "get_recent_meeting":
                recent_data = gated_result.outcome.data
                matches, visible_meetings = _resolve_target_meeting(
                    recent_data,
                    request=request,
                    message=state.message,
                    request_time=state.request_time,
                )
                if len(matches) != 1:
                    answer = _target_meeting_clarification(
                        matches=matches,
                        visible_meetings=visible_meetings,
                    )
                    return (
                        _apply_completions(state, tool_usage).model_copy(
                            update={
                                "missing_fields": ["uniqueTargetMeeting"],
                                "answer_summary": answer,
                                "status": RunStatus.WAITING_USER_INPUT,
                                "next_route": Route.FINAL,
                                "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                                "loop_iteration": loop_iteration,
                                "executed_tool_fingerprints": sorted(fingerprints),
                            }
                        ),
                        answer,
                        outcomes,
                        model_calls,
                    )
                state, request = _hydrate_mutation_target(
                    state=state,
                    request=request,
                    meeting=matches[0],
                    recent_data=recent_data,
                )
                resolved = [
                    Participant(name=item.display_name, employee_id=item.employee_id)
                    for item in matches[0].participants
                    if item.participant_type == "REQUIRED"
                ]
            elif tool_name == "get_employee_free_busy":
                free_busy_data = gated_result.outcome.data
            else:
                rooms_data = gated_result.outcome.data
        if not _read_facts_ready(request, free_busy_data, rooms_data, recent_data):
            answer = (
                "会议需求已保存，但本轮没有完成忙闲和会议室查询。请回复“继续查询”，无需重述需求。"
            )
            return (
                _apply_completions(state, tool_usage).model_copy(
                    update={
                        "resolved_employees": resolved,
                        "executed_tool_fingerprints": sorted(fingerprints),
                        "loop_iteration": loop_iteration,
                        "status": RunStatus.WAITING_USER_INPUT,
                        "missing_fields": [],
                        "answer_summary": answer,
                        "next_route": Route.FINAL,
                        "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                    }
                ),
                answer,
                outcomes,
                model_calls,
            )

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
            answer = "已生成取消预览，等待用户确认。"
            return (
                usage_state.model_copy(
                    update={
                        **common_update,
                        "operation_type": OperationType.CANCEL,
                        "draft": CancellationDraftView(meeting=cancellation.meeting),
                        "confirmation_token": cancellation.confirmation_token,
                        "draft_expires_at": cancellation.expires_at,
                        "draft_generation": generation,
                        "answer_summary": answer,
                        "status": RunStatus.WAITING_CONFIRMATION,
                        "next_route": Route.HITL,
                        "stop_reason": LoopStopReason.READY_FOR_CONFIRMATION.value,
                    }
                ),
                answer,
                outcomes,
                model_calls,
            )
        assert free_busy_data is not None and rooms_data is not None
        snapshot = _snapshot_from_java(free_busy_data, rooms_data)
        if _is_exception_replanning_context(state):
            target_meeting = _recent_meeting(recent_data, request.target_meeting_id)
            if target_meeting is not None:
                snapshot = snapshot.model_copy(
                    update={
                        "rooms": [
                            room
                            for room in snapshot.rooms
                            if room.room_id != target_meeting.room_id
                        ]
                    }
                )
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
            detailed_unsat = _enrich_unsat_analysis(
                result.unsat,
                resolved=resolved,
                organizer_id=context.user_id,
            )
            return (
                usage_state.model_copy(
                    update={
                        "resolved_employees": resolved,
                        "availability_snapshot": snapshot,
                        "schedule_candidates": [],
                        "selected_candidate_id": None,
                        "unsat_analysis": detailed_unsat,
                        "answer_summary": detailed_unsat.summary,
                        "status": RunStatus.WAITING_USER_INPUT,
                        "next_route": Route.FINAL,
                        "stop_reason": LoopStopReason.NEED_CLARIFICATION.value,
                        **common_update,
                    }
                ),
                detailed_unsat.summary,
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
        if request.intent in {Intent.FIND_COMMON_TIME, Intent.RECOMMEND_ROOM}:
            return (
                usage_state.model_copy(
                    update={
                        "resolved_employees": resolved,
                        "availability_snapshot": snapshot,
                        "schedule_candidates": candidates,
                        "selected_candidate_id": candidates[0].candidate_id,
                        "unsat_analysis": None,
                        "answer_summary": "已找到满足当前条件的候选方案。",
                        "status": RunStatus.SUCCEEDED,
                        "next_route": Route.FINAL,
                        "stop_reason": LoopStopReason.COMPLETED.value,
                        **common_update,
                    }
                ),
                "已完成只读查询并验证候选方案",
                outcomes,
                model_calls,
            )
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
                    ),
                )
                draft_view = CreateDraftView(draft=draft_response.draft)
                token = draft_response.confirmation_token
                expires = draft_response.expires_at
                operation = OperationType.CREATE
        except JavaToolError as exc:
            raise WorkflowError(exc.code, "预约草案创建暂不可用") from exc
        outcomes.append(draft_outcome)
        answer = (
            "当前会议室已被占用，请切换其他的编排选项。"
            "已重新读取最新占用情况并生成其他可用方案。"
            if deterministic_conflict_replan
            else "已生成满足当前条件的候选方案，请确认草案或切换其他编排选项。"
        )
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
                    "answer_summary": answer,
                    "status": RunStatus.WAITING_CONFIRMATION,
                    "next_route": Route.HITL,
                    "stop_reason": LoopStopReason.READY_FOR_CONFIRMATION.value,
                    **common_update,
                }
            ),
            answer,
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


_CLARIFICATION_GUIDANCE: dict[str, tuple[str, str]] = {
    "OBJECTIVE_NOT_UNDERSTOOD": (
        "我还不能确定你希望查询规则、查找时间，还是创建、修改或取消会议。",
        "请直接说明要完成的会议操作。",
    ),
    "TIME_WINDOW_REQUIRED": (
        "还没有可用于排期的日期和时间范围。",
        "请告诉我希望安排在哪一天、哪个时间段。",
    ),
    "TIME_WINDOW_IN_PAST": (
        "给出的时间范围已经过去，无法继续排期。",
        "请提供一个未来的日期和时间范围。",
    ),
    "TIME_NOT_ON_30_MINUTE_SLOT": (
        "会议时间需要落在半小时的时间点上。",
        "请把开始和结束时间调整到整点或半点。",
    ),
    "WINDOW_SHORTER_THAN_DURATION": (
        "可选时间范围短于会议需要的时长。",
        "请延长可选时间范围，或缩短会议时长。",
    ),
    "DURATION_INTERVAL_MISMATCH": (
        "固定起止时间和会议时长不一致。",
        "请确认以起止时间为准，还是以会议时长为准。",
    ),
    "TARGET_REFERENCE_MISSING": (
        "还不能唯一确定要修改或取消哪场会议。",
        "请提供会议编号，或说明会议标题和时间。",
    ),
    "TARGET_MEETING_REQUIRED": (
        "还不能唯一确定要修改或取消哪场会议。",
        "请提供会议编号，或说明会议标题和时间。",
    ),
    "uniqueTargetMeeting": (
        "找到了不止一场可能匹配的会议。",
        "请提供会议编号，或补充会议标题和时间。",
    ),
    "CAPACITY_BELOW_PARTICIPANTS": (
        "会议室容量要求小于必须参加的人数。",
        "请提高最低容量，或确认哪些人不是必需参会者。",
    ),
    "HARD_SOFT_CONSTRAINT_CONFLICT": (
        "同一个条件同时被设为必须满足和尽量满足。",
        "请确认这个条件是硬性要求还是偏好。",
    ),
    "EXPLICIT_PARTICIPANT_OMITTED": (
        "参会者信息没有被可靠识别完整。",
        "请重新列出必须参加的人员姓名。",
    ),
    "PARTICIPANT_NOT_IN_SOURCE": (
        "参会者信息无法从原请求中可靠确认。",
        "请重新列出必须参加的人员姓名。",
    ),
    "HEADCOUNT_AS_PARTICIPANT": (
        "人数被误识别成了人员姓名。",
        "请分别说明必须参加的人员姓名和预计总人数。",
    ),
    "CAPACITY_SOURCE_MISMATCH": (
        "预计人数没有被可靠识别。",
        "请重新确认预计总人数。",
    ),
    "FEATURE_NOT_IN_SOURCE": (
        "所需设备无法从原请求中可靠确认。",
        "请重新说明必须具备的会议室设备。",
    ),
    "EXPLICIT_TIME_CHANGED": (
        "时间范围没有被可靠保留下来。",
        "请重新确认允许安排会议的开始和结束时间。",
    ),
    "INTENT_SOURCE_MISMATCH": (
        "会议操作没有被可靠识别。",
        "请确认要创建、修改还是取消会议。",
    ),
    "EVIDENCE_NOT_IN_SOURCE": (
        "有一项信息无法从原请求中可靠确认。",
        "请用一句话重新说明时间、时长、参会者和必要设备。",
    ),
    "EMPLOYEE_UNRESOLVED": (
        "有一位或多位参会者无法在组织通讯录中唯一匹配。",
        "请核对姓名；如有同名人员，请补充部门信息。",
    ),
}

_CLARIFICATION_FIELD_GUIDANCE: tuple[tuple[str, tuple[str, str]], ...] = (
    (
        "participant",
        (
            "还不知道哪些人必须参加这场会议。",
            "请告诉我必需参会者姓名；如果只有你参加，也请直接说明。",
        ),
    ),
    ("duration", ("还不知道会议需要持续多久。", "请提供会议时长，例如30分钟或60分钟。")),
    ("time", ("还没有可用于排期的时间信息。", "请提供日期和允许安排的时间范围。")),
    (
        "target",
        ("还不能唯一确定要操作哪场会议。", "请提供会议编号，或说明会议标题和时间。"),
    ),
)


def _compose_clarification(
    *,
    provider: ModelProvider,
    issue_codes: list[str],
    request: MeetingRequest | None,
    extra_facts: list[str] | None = None,
) -> tuple[str, list[ModelCompletion]]:
    """Let Supervisor phrase verified issues; fail closed to a deterministic template."""

    contract = _clarification_contract(
        issue_codes=issue_codes,
        request=request,
        extra_facts=extra_facts,
    )
    fallback = str(contract["fallbackMessage"])
    model_request = ModelRequest(
        agent_name="supervisor",
        system_prompt=CLARIFICATION_PROMPT,
        user_prompt="CLARIFICATION_CONTRACT="
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        schema_name=ClarificationResponse.__name__,
        schema=ClarificationResponse.model_json_schema(by_alias=True),
    )
    try:
        completion_value = provider.complete(model_request)
    except ModelProviderError:
        return fallback, []
    completion = (
        completion_value
        if isinstance(completion_value, ModelCompletion)
        else ModelCompletion(content=completion_value)
    )
    completions = [completion]
    if completion.content is None:
        return fallback, completions
    try:
        response = ClarificationResponse.model_validate_json(completion.content)
    except ValueError:
        return fallback, completions
    if not _clarification_message_supported(response.message, contract):
        return fallback, completions
    return response.message, completions


def _clarification_contract(
    *,
    issue_codes: list[str],
    request: MeetingRequest | None,
    extra_facts: list[str] | None = None,
) -> dict[str, object]:
    explanations: list[str] = []
    requested_inputs: list[str] = []
    for code in issue_codes:
        guidance = _CLARIFICATION_GUIDANCE.get(code)
        if guidance is None:
            lowered = code.lower()
            guidance = next(
                (value for marker, value in _CLARIFICATION_FIELD_GUIDANCE if marker in lowered),
                (
                    "这项会议需求还不能被可靠确认。",
                    "请换一种说法补充相关信息。",
                ),
            )
        explanation, requested = guidance
        if explanation not in explanations:
            explanations.append(explanation)
        if requested not in requested_inputs:
            requested_inputs.append(requested)
    if not explanations:
        explanations.append("这项会议需求还不能被可靠确认。")
    if not requested_inputs:
        requested_inputs.append("请换一种说法补充相关信息。")
    facts = _verified_clarification_facts(request)
    facts.extend(item for item in (extra_facts or []) if item not in facts)
    fallback = "我还需要确认一点：" + "；".join(explanations[:3])
    fallback += " " + "；".join(requested_inputs[:3])
    return {
        "verifiedFacts": facts[:6],
        "explanations": explanations[:3],
        "requestedInputs": requested_inputs[:3],
        "fallbackMessage": fallback[:500],
    }


def _verified_clarification_facts(request: MeetingRequest | None) -> list[str]:
    if request is None:
        return []
    facts = [f"会议时长为{request.duration_minutes}分钟"]
    if request.time_window is not None:
        facts.append(
            "允许安排的时间范围为"
            f"{request.time_window.start.isoformat()}至{request.time_window.end.isoformat()}"
        )
    names = [
        *(["当前登录用户（我）"] if request.includes_current_user else []),
        *(item.name for item in request.required_participants),
    ]
    if names:
        facts.append("必需参会者为" + "、".join(names))
    if request.required_features:
        labels = {
            "WHITEBOARD": "白板",
            "LARGE_SCREEN": "大屏",
            "VIDEO_CONFERENCE": "视频会议设备",
            "PROJECTOR": "投影仪",
        }
        facts.append(
            "必需设备为" + "、".join(labels.get(item, item) for item in request.required_features)
        )
    return facts[:6]


def _clarification_message_supported(message: str, contract: dict[str, object]) -> bool:
    """Reject numeric/time details that were not present in the verified contract."""

    contract_text = json.dumps(contract, ensure_ascii=False)
    numeric_tokens = re.findall(r"\d+(?::\d+)?", message)
    if any(token not in contract_text for token in numeric_tokens):
        return False
    return len(message) <= 500


def _apply_explicit_meeting_defaults(
    draft: RequirementDraft, source: str, *, request_time: datetime
) -> RequirementDraft:
    updates: dict[str, object] = {}
    if _is_exception_replanning(source):
        updates["intent"] = Intent.MODIFY_MEETING
        if not draft.target_meeting_reference:
            updates["target_meeting_reference"] = source[:240]
    explicit_meeting_id = _explicit_meeting_id(source)
    if explicit_meeting_id is not None and (
        draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        or _is_exception_replanning(source)
    ):
        updates["target_meeting_id"] = explicit_meeting_id
    normalized_source = _normalize_chinese_clock_tokens(source)
    time_source = normalized_source
    if draft.intent is Intent.MODIFY_MEETING:
        mutation = re.search(r"改到|调整到|移到|改为", source)
        if mutation is not None:
            selector_source = source[: mutation.start()].strip()
            destination_source = source[mutation.end() :].strip()
            if selector_source:
                updates["target_meeting_reference"] = selector_source[-240:]
            normalized_selector = _normalize_chinese_clock_tokens(selector_source)
            normalized_destination = _normalize_chinese_clock_tokens(destination_source)
            if any(marker in destination_source for marker in ("同一时间", "原时间", "同样时间")):
                selector_clock = re.search(
                    r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
                    normalized_selector,
                )
                if selector_clock is not None:
                    normalized_destination = normalized_destination + " " + selector_clock.group(0)
            time_source = normalized_destination
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
    if any(
        value in source
        for value in ("我的小组", "同组人员", "小组会议", "组内人员", "组内的人")
    ):
        updates["participant_scope"] = "MY_DEPARTMENT"
    elif (
        any(value in source for value in ("只有我", "我自己参加", "就我一个人", "我必须参加"))
        and not draft.required_participant_names
    ):
        updates["participant_scope"] = "ORGANIZER_ONLY"

    duration_match = re.search(r"(30|60|90|120|150|180|210|240)\s*分钟", source)
    english_duration = re.search(r"(30|60|90|120|150|180|210|240)[ -]minute", source, re.IGNORECASE)
    if duration_match is not None:
        updates["duration_minutes"] = int(duration_match.group(1))
    elif english_duration is not None:
        updates["duration_minutes"] = int(english_duration.group(1))
    elif "一个半小时" in source:
        updates["duration_minutes"] = 90
    elif "半小时" in source:
        updates["duration_minutes"] = 30
    elif "一小时" in source or "一个小时" in source:
        updates["duration_minutes"] = 60
    elif "两小时" in source or "两个小时" in source:
        updates["duration_minutes"] = 120

    headcount = re.search(r"(\d{1,4})\s*(?:个)?人", source)
    english_headcount = re.search(r"(\d{1,4})\s*people", source, re.IGNORECASE)
    if headcount is not None:
        updates["minimum_capacity"] = int(headcount.group(1))
    elif english_headcount is not None:
        updates["minimum_capacity"] = int(english_headcount.group(1))

    feature_aliases = {
        "白板": "WHITEBOARD",
        "大屏": "LARGE_SCREEN",
        "投屏": "LARGE_SCREEN",
        "视频会议": "VIDEO_CONFERENCE",
        "投影仪": "PROJECTOR",
        "whiteboard": "WHITEBOARD",
        "large screen": "LARGE_SCREEN",
        "video conference": "VIDEO_CONFERENCE",
        "projector": "PROJECTOR",
    }
    normalized_lower = source.lower()
    explicitly_closed = any(
        value in source for value in ("无设备要求", "不需要额外设备", "没有设备要求")
    )
    explicit_features = [
        canonical
        for alias, canonical in feature_aliases.items()
        if alias.lower() in normalized_lower
    ]
    features = (
        []
        if explicitly_closed
        else list(
            dict.fromkeys(
                [
                    *(feature_aliases.get(item.lower(), item) for item in draft.required_features),
                    *explicit_features,
                ]
            )
        )
    )
    if "投屏" in source and "投影仪" not in source:
        # DeepSeek sometimes expands “投屏” to both PROJECTOR and
        # LARGE_SCREEN.  The frozen product vocabulary maps it to the latter;
        # keep an explicitly requested 投影仪 distinct.
        features = [item for item in features if item != "PROJECTOR"]
        if "LARGE_SCREEN" not in features:
            features.append("LARGE_SCREEN")
    if features != draft.required_features:
        updates["required_features"] = features

    preferred = re.search(
        r"(?:最好|尽量|优先)(?:是|在)?(?:下午|晚上|上午|早上|中午)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
        normalized_source,
    )
    if preferred is not None:
        hour = int(preferred.group(1))
        minute = int(preferred.group(2) or 0)
        if any(value in preferred.group(0) for value in ("下午", "晚上")) and hour < 12:
            hour += 12
        soft = [item for item in draft.soft_constraints if item.type != "PREFER_START_AT"]
        soft.append(Constraint(type="PREFER_START_AT", value=f"{hour:02d}:{minute:02d}", weight=20))
        updates["soft_constraints"] = soft

    target_date = _deterministic_target_date(time_source, request_time)
    daypart = _daypart_window(time_source)
    has_explicit_time_context = target_date is not None or daypart is not None
    explicit_range = re.search(
        r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)\s*"
        r"(?:到|至|-)\s*(\d{1,2})(?::(\d{2})|点)",
        time_source,
    )
    explicit_single = re.search(
        r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
        time_source,
    )
    has_preferred_only = preferred is not None and not any(
        marker in source for marker in ("必须", "固定", "就定", "只能")
    )
    if has_preferred_only and not has_explicit_time_context:
        # A soft start preference must not become a new hard time window.  In
        # continuation turns this also removes provider-invented time fields so
        # the merge below retains the already confirmed/defaulted date window.
        updates["time_window"] = None
        updates["pending_start_at"] = None
        updates["pending_start_ambiguous"] = False
    if target_date is None and (
        daypart is not None or explicit_range is not None or explicit_single is not None
    ):
        target_date = request_time.date()
    try:
        if (
            target_date is not None
            and explicit_single is not None
            and _source_has_ambiguous_single_time(time_source)
            and not has_preferred_only
        ):
            start = _at_local_date(
                request_time,
                target_date,
                int(explicit_single.group(1)),
                int(explicit_single.group(2) or 0),
            )
            updates["pending_start_at"] = start
            updates["pending_start_ambiguous"] = True
            updates["time_window"] = None
        elif (
            target_date is not None
            and explicit_range is not None
            and not _source_has_ambiguous_single_time(time_source)
        ):
            start_hour = int(explicit_range.group(1))
            start_minute = int(explicit_range.group(2) or 0)
            end_hour = int(explicit_range.group(3))
            end_minute = int(explicit_range.group(4) or 0)
            marker = explicit_range.group(0)
            if any(value in marker for value in ("下午", "晚上")):
                if start_hour < 12:
                    start_hour += 12
                if end_hour < 12:
                    end_hour += 12
            start = _at_local_date(request_time, target_date, start_hour, start_minute)
            end = _at_local_date(request_time, target_date, end_hour, end_minute)
            if end <= start and "晚上" in marker:
                end += timedelta(days=1)
            updates["time_window"] = TimeWindow(start=start, end=end)
        elif (
            target_date is not None
            and explicit_single is not None
            and not has_preferred_only
            and not _source_has_ambiguous_single_time(time_source)
        ):
            hour = int(explicit_single.group(1))
            minute = int(explicit_single.group(2) or 0)
            marker = explicit_single.group(0)
            if any(value in marker for value in ("下午", "晚上")) and hour < 12:
                hour += 12
            start = _at_local_date(request_time, target_date, hour, minute)
            if draft.duration_minutes is None:
                updates["pending_start_at"] = start
                updates["pending_start_ambiguous"] = False
                updates["time_window"] = None
            else:
                updates["time_window"] = TimeWindow(
                    start=start, end=start + timedelta(minutes=draft.duration_minutes)
                )
                updates["pending_start_at"] = None
                updates["pending_start_ambiguous"] = False
        elif target_date is not None and daypart is not None:
            start_hour, end_hour, crosses_midnight = daypart
            start = _at_local_date(request_time, target_date, start_hour, 0)
            end = _at_local_date(request_time, target_date, end_hour, 0)
            if crosses_midnight:
                end += timedelta(days=1)
            updates["time_window"] = TimeWindow(start=start, end=end)
    except ValueError:
        updates.pop("time_window", None)
    return draft.model_copy(update=updates) if updates else draft


def _apply_current_user_participation(
    draft: RequirementDraft, source: str
) -> RequirementDraft:
    """Resolve first-person attendance from authenticated context, not model-supplied identity."""

    if draft.intent not in {
        Intent.CREATE_MEETING,
        Intent.FIND_COMMON_TIME,
        Intent.RECOMMEND_ROOM,
    }:
        return draft
    normalized = re.sub(r"\s+", "", source)
    attends = bool(
        re.search(r"我(?:和|跟|与)|(?:和|跟|与)我", normalized)
        or re.search(r"(?:包括我|我(?:本人|也)?(?:必须|需要|要)?参加)", normalized)
        or re.search(r"(?:^|[，,、])我(?:[，,、]|$)", normalized)
    )
    return draft.model_copy(update={"includes_current_user": attends})


def _normalize_chinese_clock_tokens(source: str) -> str:
    values = {
        "一": "1",
        "两": "2",
        "二": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
        "十": "10",
        "十一": "11",
        "十二": "12",
    }
    return re.sub(
        r"(十二|十一|十|[一二两三四五六七八九])(?=点)",
        lambda match: values[match.group(1)],
        source,
    )


def _deterministic_target_date(source: str, request_time: datetime) -> Any:
    compact = re.sub(r"\s+", "", source)
    try:
        iso_date = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", compact)
        if iso_date is not None:
            return request_time.date().replace(
                year=int(iso_date.group(1)),
                month=int(iso_date.group(2)),
                day=int(iso_date.group(3)),
            )
        if "今天" in compact or "今日" in compact:
            return request_time.date()
        if "明天" in compact:
            return (request_time + timedelta(days=1)).date()
        if "后天" in compact:
            return (request_time + timedelta(days=2)).date()
        absolute = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", compact)
        if absolute is not None:
            return request_time.date().replace(
                year=int(absolute.group(1)),
                month=int(absolute.group(2)),
                day=int(absolute.group(3)),
            )
        month_day = re.search(r"(\d{1,2})月(\d{1,2})[日号]", compact)
        if month_day is not None:
            return request_time.date().replace(
                month=int(month_day.group(1)), day=int(month_day.group(2))
            )
        day_only = re.search(r"(?<!月)(?<!\d)(\d{1,2})号", compact)
        if day_only is not None:
            return request_time.date().replace(day=int(day_only.group(1)))
    except ValueError:
        return None
    weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    weekday = re.search(
        r"(下周|下星期|本周|这周|本星期|这星期|周|星期)([一二三四五六日天])",
        compact,
    )
    if weekday is not None:
        target = weekdays[weekday.group(2)]
        week_start = request_time.date() - timedelta(days=request_time.weekday())
        if weekday.group(1) in {"下周", "下星期"}:
            week_start += timedelta(days=7)
        return week_start + timedelta(days=target)
    return None


def _daypart_window(source: str) -> tuple[int, int, bool] | None:
    normalized = source.lower()
    if "晚上" in source or "evening" in normalized:
        return 18, 6, True
    if "下午" in source or "afternoon" in normalized:
        return 12, 18, False
    if "中午" in source or "noon" in normalized:
        return 11, 14, False
    if "上午" in source or "早上" in source or "morning" in normalized:
        return 6, 12, False
    return None


def _at_local_date(request_time: datetime, target_date: Any, hour: int, minute: int) -> datetime:
    return request_time.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def _resolve_ambiguous_pending_start(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    source: str,
) -> RequirementDraft:
    if (
        previous is None
        or previous.pending_start_at is None
        or not previous.pending_start_ambiguous
    ):
        return current
    daypart = _daypart_window(source)
    if daypart is None:
        return current
    start = previous.pending_start_at
    hour = start.hour
    if any(marker in source for marker in ("下午", "晚上", "中午")) and hour < 12:
        hour += 12
    resolved_start = start.replace(hour=hour)
    duration = current.duration_minutes or previous.duration_minutes
    if duration is None:
        return current.model_copy(
            update={
                "time_window": None,
                "pending_start_at": resolved_start,
                "pending_start_ambiguous": False,
            }
        )
    return current.model_copy(
        update={
            "time_window": TimeWindow(
                start=resolved_start,
                end=resolved_start + timedelta(minutes=duration),
            ),
            "pending_start_at": None,
            "pending_start_ambiguous": False,
        }
    )


def _apply_participant_delta(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    source: str,
) -> RequirementDraft:
    """Apply explicit roster mutations to the last verified participant list.

    The model extracts only the current utterance, so a removal can legitimately
    contain no replacement list.  Matching removals against the previous names
    keeps the operation deterministic and prevents a negative instruction from
    being misread as a brand-new participant list.
    """

    if previous is None or not previous.required_participant_names:
        return current
    previous_names = list(dict.fromkeys(previous.required_participant_names))
    mentioned_previous = [name for name in previous_names if name in source]
    remove_requested = bool(
        mentioned_previous
        and any(
            marker in source
            for marker in (
                "去掉",
                "删除",
                "移除",
                "排除",
                "不参加",
                "不会来",
                "不来",
                "请假",
            )
        )
    )
    add_requested = any(
        marker in source for marker in ("加上", "增加", "添加", "邀请", "再叫上", "再加")
    )
    replace_requested = any(
        marker in source for marker in ("改成", "换成", "只有", "就这些人", "参会人是")
    )

    next_names: list[str] | None = None
    if remove_requested:
        removed = set(mentioned_previous)
        next_names = [name for name in previous_names if name not in removed]
        if add_requested:
            deterministic_additions = re.findall(
                r"(?:加上|增加|添加|邀请|再叫上|再加)\s*"
                r"([\u4e00-\u9fff]{2,4}?)(?=必须|参加|、|，|和|与|$)",
                source,
            )
            additions = [
                name
                for name in [*current.required_participant_names, *deterministic_additions]
                if name not in removed
            ]
            next_names = list(dict.fromkeys([*next_names, *additions]))
    elif add_requested and current.required_participant_names:
        next_names = list(dict.fromkeys([*previous_names, *current.required_participant_names]))
    elif replace_requested and current.required_participant_names:
        next_names = list(dict.fromkeys(current.required_participant_names))

    if next_names is None:
        return current
    previous_capacity = previous.minimum_capacity
    explicit_capacity = current.minimum_capacity
    preserved_capacity = (
        previous_capacity
        if previous_capacity is not None and previous_capacity > len(previous_names)
        else None
    )
    next_capacity = max(
        len(next_names),
        explicit_capacity or 0,
        preserved_capacity or 0,
        1,
    )
    return current.model_copy(
        update={
            "required_participant_names": next_names,
            # A user-corrected directory roster is now an explicit list.  Do
            # not resolve MY_DEPARTMENT again and silently re-add removals.
            "participant_scope": previous.participant_scope,
            "participant_list_modified": True,
            "minimum_capacity": next_capacity,
        }
    )


def _apply_constraint_delta(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    source: str,
) -> RequirementDraft:
    """Apply explicit relaxation deltas to the last verified requirement.

    Exception replanning starts from inherited Java facts.  A continuation may
    relax one device or extend the original candidate window, but it must not
    accidentally replace the inherited meeting duration just because the
    relaxation itself contains a number of minutes.
    """

    if previous is None:
        return current
    updates: dict[str, object] = {}
    features = list(previous.required_features)
    removed_features = {
        canonical
        for canonical, aliases in SourceFidelityEvaluator._FEATURE_ALIASES.items()
        if any(_feature_removal_requested(source, alias) for alias in aliases)
    }
    if removed_features:
        features = [item for item in features if item not in removed_features]
        additions = [item for item in current.required_features if item not in removed_features]
        updates["required_features"] = list(dict.fromkeys([*features, *additions]))

    delay = re.search(r"(?:允许)?(?:顺延|延后|推迟)\s*(30|60|90|120)\s*分钟", source)
    if delay is not None and previous.time_window is not None:
        delay_minutes = int(delay.group(1))
        updates.update(
            {
                "duration_minutes": previous.duration_minutes,
                "time_window": TimeWindow(
                    start=previous.time_window.start,
                    end=previous.time_window.end + timedelta(minutes=delay_minutes),
                ),
                "pending_start_at": None,
                "pending_start_ambiguous": False,
            }
        )
    return current.model_copy(update=updates) if updates else current


def _source_changes_intent(source: str) -> bool:
    if re.search(r"(?:取消|撤销|新建会议|预约会议|找个空的会议室)", source):
        return True
    explicit_target = bool(
        _explicit_meeting_id(source)
        or re.search(r"(?:把|将).{0,40}(?:会议|评审|周会|例会|那场|那个)", source)
    )
    return explicit_target and bool(re.search(r"(?:改期|改到|调整到|重新安排)", source))


def _continuation_fidelity_feedback(
    feedback: RequirementFeedback | None, *, state: AgentState
) -> RequirementFeedback | None:
    """Keep a draft's trusted intent when a continuation only edits that draft.

    Phrases such as “那改到明天” and “调整参会人” mutate the pending request;
    they do not turn a CREATE run into a reschedule of an existing meeting.
    Other fidelity failures remain blocking.
    """

    if (
        feedback is None
        or not state.continuation_turn
        or state.intent is None
        or _source_changes_intent(state.message)
        or "INTENT_SOURCE_MISMATCH" not in feedback.codes
    ):
        return feedback
    remaining = [code for code in feedback.codes if code != "INTENT_SOURCE_MISMATCH"]
    if not remaining:
        return None
    return feedback.model_copy(
        update={
            "codes": remaining,
            "summary": "源文本忠实度校验未通过：" + "、".join(remaining),
        }
    )


def _merge_requirement_drafts(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    *,
    source: str,
) -> RequirementDraft:
    if previous is None:
        return current
    explicit_names = bool(current.required_participant_names)
    participant_mutated = current.participant_list_modified
    scope_changed = current.participant_scope is not None
    if current.time_window is not None:
        time_window = current.time_window
        pending_start_at = None
        pending_start_ambiguous = False
    elif current.pending_start_at is not None:
        time_window = None
        pending_start_at = current.pending_start_at
        pending_start_ambiguous = current.pending_start_ambiguous
    else:
        time_window = previous.time_window
        pending_start_at = previous.pending_start_at
        pending_start_ambiguous = previous.pending_start_ambiguous
    soft_constraints = [*previous.soft_constraints]
    for soft_item in current.soft_constraints:
        soft_constraints = [
            existing for existing in soft_constraints if existing.type != soft_item.type
        ]
        value = soft_item.value
        if (
            soft_item.type == "PREFER_START_AT"
            and value.startswith("0")
            and time_window is not None
            and time_window.start.hour >= 12
        ):
            hour, minute = value.split(":", maxsplit=1)
            value = f"{int(hour) + 12:02d}:{minute}"
        soft_constraints.append(soft_item.model_copy(update={"value": value}))
    hard_constraints = list(previous.hard_constraints)
    for hard_item in current.hard_constraints:
        hard_constraints = [
            existing for existing in hard_constraints if existing.type != hard_item.type
        ]
        hard_constraints.append(hard_item)
    evidence = [*previous.field_evidence]
    for evidence_item in current.field_evidence:
        if evidence_item not in evidence:
            evidence.append(evidence_item)
    return previous.model_copy(
        update={
            "intent": (current.intent if _source_changes_intent(source) else previous.intent),
            "title": current.title or previous.title,
            "meeting_type": current.meeting_type or previous.meeting_type,
            "duration_minutes": (
                current.duration_minutes
                if _source_changes_meeting_duration(source)
                else previous.duration_minutes
            ),
            "time_window": time_window,
            "pending_start_at": pending_start_at,
            "pending_start_ambiguous": pending_start_ambiguous,
            "required_participant_names": (
                current.required_participant_names
                if explicit_names or scope_changed or participant_mutated
                else previous.required_participant_names
            ),
            "includes_current_user": (
                current.includes_current_user or previous.includes_current_user
            ),
            "participant_scope": (
                current.participant_scope or previous.participant_scope
                if participant_mutated
                else None
                if explicit_names
                else current.participant_scope or previous.participant_scope
            ),
            "participant_list_modified": (
                participant_mutated or previous.participant_list_modified
            ),
            "optional_groups": list(
                dict.fromkeys([*previous.optional_groups, *current.optional_groups])
            ),
            "required_features": (
                current.required_features
                if _source_changes_feature_constraints(source)
                else list(dict.fromkeys([*previous.required_features, *current.required_features]))
            ),
            "minimum_capacity": current.minimum_capacity or previous.minimum_capacity,
            "preferred_buildings": list(
                dict.fromkeys([*previous.preferred_buildings, *current.preferred_buildings])
            ),
            "hard_constraints": hard_constraints,
            "soft_constraints": soft_constraints,
            "target_meeting_id": current.target_meeting_id or previous.target_meeting_id,
            "target_meeting_reference": (
                current.target_meeting_reference or previous.target_meeting_reference
            ),
            "field_evidence": evidence[-40:],
            "needs_policy": current.needs_policy or previous.needs_policy,
            "summary": current.summary,
        }
    )


def _apply_unsat_recommended_time(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    *,
    source: str,
    analysis: UnsatAnalysis | None,
) -> RequirementDraft:
    """Apply a previously offered time only after the user explicitly accepts it."""

    if previous is None or analysis is None:
        return current
    if not any(
        marker in source
        for marker in (
            "按你推荐",
            "按推荐",
            "用推荐时间",
            "最近可行时间",
            "推荐的时间",
        )
    ):
        return current
    blocker_ends = [item.end_at for item in analysis.blocking_intervals]
    if not blocker_ends or previous.duration_minutes is None:
        return current
    recommended_start = max(blocker_ends)
    local_day_end = recommended_start.replace(hour=18, minute=0, second=0, microsecond=0)
    if recommended_start + timedelta(minutes=previous.duration_minutes) > local_day_end:
        return current
    return current.model_copy(
        update={
            "time_window": None,
            "pending_start_at": recommended_start,
            "pending_start_ambiguous": False,
            "summary": (
                f"用户接受了上一轮建议的最近可重新校验时间 {recommended_start:%Y-%m-%d %H:%M}。"
            ),
        }
    )


def _scope_members(outcome: ToolOutcome) -> list[Participant]:
    raw = outcome.data.get("members")
    if not isinstance(raw, list):
        return []
    members: list[Participant] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        employee_id = item.get("employeeId")
        display_name = item.get("displayName")
        status = item.get("status")
        if isinstance(employee_id, int) and isinstance(display_name, str) and status == "ACTIVE":
            members.append(Participant(name=display_name, employee_id=employee_id))
    return members


def _requirement_feedback(code: str, summary: str) -> RequirementFeedback:
    return RequirementFeedback(codes=[code], summary=summary, repairable=False)


def _source_has_ambiguous_single_time(source: str) -> bool:
    match = re.search(
        r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
        source,
    )
    if match is None or "点" not in match.group(0) or int(match.group(1)) > 12:
        return False
    return not any(marker in match.group(0) for marker in ("上午", "早上", "中午", "下午", "晚上"))


def _source_describes_fixed_interval(source: str) -> bool:
    match = SourceFidelityEvaluator._TIME_RANGE.search(source)
    return match is not None and not SourceFidelityEvaluator.is_search_window(source, match)


def _closes_optional_requirements(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "没别的要求",
            "没有其他要求",
            "其他没有",
            "没其他要求",
            "无其他要求",
            "没有设备要求",
            "无设备要求",
            "不需要额外设备",
        )
    )


def _previous_requirement_item(
    items: list[RequirementItem], field_name: str
) -> RequirementItem | None:
    return next((item for item in items if item.field == field_name), None)


def _has_requirement_issue(missing_fields: list[str], field_name: str) -> bool:
    prefixes = {
        "timeWindow": ("TIME_", "WINDOW_"),
        "durationMinutes": ("DURATION_",),
        "requiredParticipants": ("PARTICIPANT_", "REQUIRED_PARTICIPANT_"),
    }.get(field_name, ())
    return field_name in missing_fields or any(code.startswith(prefixes) for code in missing_fields)


def _requirement_items(
    *,
    draft: RequirementDraft,
    request: MeetingRequest,
    missing_fields: list[str],
    source: str,
    previous_items: list[RequirementItem],
    optional_closed: bool,
) -> list[RequirementItem]:
    items: list[RequirementItem] = []
    previous_time = _previous_requirement_item(previous_items, "timeWindow")
    time_issue = _has_requirement_issue(missing_fields, "timeWindow")
    if draft.time_window is None and draft.pending_start_at is not None:
        rule = _time_rule_id(source)
        items.append(
            RequirementItem(
                field="timeWindow",
                status=(
                    RequirementSlotStatus.AMBIGUOUS
                    if draft.pending_start_ambiguous
                    else RequirementSlotStatus.DEFAULTED
                    if rule is not None
                    else RequirementSlotStatus.EXPLICIT
                ),
                summary=(
                    f"{draft.pending_start_at.strftime('%Y-%m-%d')} 的"
                    f" {draft.pending_start_at.strftime('%H:%M')}，请确认上午或下午"
                    if draft.pending_start_ambiguous
                    else f"{draft.pending_start_at.strftime('%Y-%m-%d %H:%M')} 开始"
                ),
                source=source,
                rule_id=rule,
                blocking=draft.pending_start_ambiguous,
            )
        )
    elif draft.time_window is None:
        ambiguous_time = _source_has_ambiguous_single_time(source)
        items.append(
            RequirementItem(
                field="timeWindow",
                status=(
                    RequirementSlotStatus.AMBIGUOUS
                    if ambiguous_time
                    else RequirementSlotStatus.MISSING
                ),
                summary=(
                    "请说明是上午还是下午，并确认允许安排的时间段"
                    if ambiguous_time
                    else "待补充日期和允许安排的时间段"
                ),
                blocking=True,
            )
        )
    else:
        soft_preference_only = _source_has_soft_start_preference_only(source)
        rule = None if soft_preference_only else _time_rule_id(source)
        status = (
            RequirementSlotStatus.CONFLICT
            if time_issue
            else RequirementSlotStatus.EXPLICIT
            if _source_changes_time_constraints(source)
            else previous_time.status
            if soft_preference_only and previous_time is not None
            else RequirementSlotStatus.DEFAULTED
            if rule is not None
            else previous_time.status
            if previous_time is not None
            else RequirementSlotStatus.EXPLICIT
        )
        items.append(
            RequirementItem(
                field="timeWindow",
                status=status,
                summary=(
                    f"{draft.time_window.start.strftime('%Y-%m-%d %H:%M')} 至 "
                    f"{draft.time_window.end.strftime('%Y-%m-%d %H:%M')}"
                ),
                source=(
                    source
                    if rule is not None
                    else previous_time.source
                    if previous_time
                    else source
                ),
                rule_id=rule or (previous_time.rule_id if previous_time else None),
                blocking=time_issue,
            )
        )
    previous_duration = _previous_requirement_item(previous_items, "durationMinutes")
    duration_issue = _has_requirement_issue(missing_fields, "durationMinutes")
    if draft.duration_minutes is None:
        items.append(
            RequirementItem(
                field="durationMinutes",
                status=RequirementSlotStatus.MISSING,
                summary="待补充会议时长",
                blocking=True,
            )
        )
    else:
        duration_is_current = _source_changes_meeting_duration(source)
        items.append(
            RequirementItem(
                field="durationMinutes",
                status=(
                    RequirementSlotStatus.CONFLICT
                    if duration_issue
                    else RequirementSlotStatus.EXPLICIT
                    if duration_is_current
                    else previous_duration.status
                    if previous_duration
                    else RequirementSlotStatus.EXPLICIT
                ),
                summary=f"{draft.duration_minutes}分钟",
                source=(
                    source
                    if re.search(r"(?:分钟|小时)", source)
                    else previous_duration.source
                    if previous_duration
                    else source
                ),
                blocking=duration_issue,
            )
        )
    previous_participants = _previous_requirement_item(previous_items, "requiredParticipants")
    if (
        not draft.required_participant_names
        and draft.participant_scope != "ORGANIZER_ONLY"
        and not draft.includes_current_user
    ):
        items.append(
            RequirementItem(
                field="requiredParticipants",
                status=RequirementSlotStatus.MISSING,
                summary="待补充必需参会人员或人员范围",
                blocking=True,
            )
        )
    elif draft.participant_scope == "ORGANIZER_ONLY" or (
        draft.includes_current_user and not draft.required_participant_names
    ):
        items.append(
            RequirementItem(
                field="requiredParticipants",
                status=RequirementSlotStatus.EXPLICIT,
                summary="仅当前登录用户（我）",
                source=source,
            )
        )
    else:
        directory = (
            draft.participant_scope == "MY_DEPARTMENT" and not draft.participant_list_modified
        )
        participant_changed = _source_has_participant_mutation(source)
        labels = [
            *(["当前登录用户（我）"] if draft.includes_current_user else []),
            *draft.required_participant_names,
        ]
        names = "、".join(labels)
        items.append(
            RequirementItem(
                field="requiredParticipants",
                status=(
                    RequirementSlotStatus.DIRECTORY_RESOLVED
                    if directory
                    else RequirementSlotStatus.EXPLICIT
                    if participant_changed
                    else previous_participants.status
                    if previous_participants is not None
                    else RequirementSlotStatus.EXPLICIT
                ),
                summary=f"{len(labels)}人：{names}",
                source=(
                    "我的小组/同组人员"
                    if directory
                    else source
                    if participant_changed
                    else previous_participants.source
                    if previous_participants is not None
                    else source
                ),
                rule_id="CURRENT_USER_DEPARTMENT" if directory else None,
            )
        )
    features = "、".join(draft.required_features)
    feature_constraints_changed = _source_changes_feature_constraints(source)
    items.append(
        RequirementItem(
            field="optionalRequirements",
            status=(
                RequirementSlotStatus.CLOSED
                if optional_closed
                else RequirementSlotStatus.EXPLICIT
                if features or feature_constraints_changed
                else RequirementSlotStatus.UNSPECIFIED
            ),
            summary=(
                f"硬性设备：{features}；其他要求已结束"
                if optional_closed and features
                else "没有其他硬性要求"
                if optional_closed
                else f"硬性设备：{features}；可继续补充其他要求"
                if features
                else "已明确放宽设备要求；可继续补充其他要求"
                if feature_constraints_changed
                else "可选：投屏、白板、视频会议设备、地点等硬性要求"
            ),
            source=source if features or optional_closed else None,
        )
    )
    return items


def _time_rule_id(source: str) -> str | None:
    compact = re.sub(r"\s+", "", source)
    date_default = bool(re.search(r"(?<!月)(?<!\d)\d{1,2}号", compact))
    weekday_default = bool(
        re.search(r"(?:本周|这周|本星期|这星期|周|星期)[一二三四五六日天]", compact)
    )
    daypart = _daypart_window(source)
    if date_default and daypart is not None:
        return "CURRENT_MONTH_AND_DAYPART"
    if weekday_default and daypart is not None:
        return "CURRENT_WEEK_AND_DAYPART"
    explicit_date = bool(
        re.search(r"(?:今天|今日|明天|后天|\d{4}年|\d{1,2}月|\d{1,2}号)", compact)
        or re.search(r"(?:下周|下星期|本周|这周|周|星期)[一二三四五六日天]", compact)
    )
    time_only = bool(re.search(r"(?:\d{1,2}:\d{2}|\d{1,2}点)", source))
    if time_only and not explicit_date:
        return "CURRENT_DAY_FROM_TIME_ONLY"
    if daypart is not None:
        return "CURRENT_DAY_AND_DAYPART"
    return None


def _source_has_soft_start_preference_only(source: str) -> bool:
    pattern = (
        r"(?:最好|尽量|优先)(?:是|在)?(?:下午|晚上|上午|早上|中午)?"
        r"\s*\d{1,2}(?::\d{2}|点)"
    )
    if not re.search(pattern, source):
        return False
    if any(marker in source for marker in ("必须", "固定", "就定", "只能")):
        return False
    return not bool(
        re.search(
            r"(?:今天|今日|明天|后天|\d{1,2}月\d{1,2}[日号]|(?<!月)(?<!\d)\d{1,2}号|"
            r"(?:下周|本周|这周|周|星期)[一二三四五六日天])",
            source,
        )
    )


def _source_has_participant_mutation(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "去掉",
            "删除",
            "移除",
            "排除",
            "不参加",
            "不会来",
            "不来",
            "请假",
            "加上",
            "增加",
            "添加",
            "邀请",
            "再叫上",
            "再加",
            "改成",
            "换成",
            "只有",
            "就这些人",
            "参会人是",
        )
    )


def _format_requirement_clarification(items: list[RequirementItem]) -> str:
    labels = {
        "timeWindow": "时间",
        "durationMinutes": "时长",
        "requiredParticipants": "参会人",
        "optionalRequirements": "其他条件",
    }
    status_labels = {
        RequirementSlotStatus.DEFAULTED: "系统补全",
        RequirementSlotStatus.DIRECTORY_RESOLVED: "通讯录推定",
        RequirementSlotStatus.INHERITED: "原会议继承",
        RequirementSlotStatus.MISSING: "还需补充",
        RequirementSlotStatus.EXPLICIT: "已明确",
        RequirementSlotStatus.AMBIGUOUS: "需要确认",
        RequirementSlotStatus.CONFLICT: "存在冲突",
        RequirementSlotStatus.UNSPECIFIED: "未说明",
        RequirementSlotStatus.CLOSED: "已结束",
    }
    lines = ["我先把需求整理如下，你可以一句话补充或纠正："]
    for index, item in enumerate(items, start=1):
        lines.append(
            f"{index}. {labels[item.field]}（{status_labels[item.status]}）：{item.summary}。"
        )
    if any(item.status is RequirementSlotStatus.DIRECTORY_RESOLVED for item in items):
        lines.append("“我的小组”暂按当前所属部门解释；如名单有误，请直接补充或删除人员。")
    blocking = [labels[item.field] for item in items if item.blocking]
    if blocking:
        lines.append("开始查询前还需要：" + "、".join(blocking) + "。")
    return "\n".join(lines)[:500]


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
        "includesCurrentUser": request.includes_current_user,
        "participantNames": [item.name for item in request.required_participants],
        "participantIds": [
            item.employee_id
            for item in request.required_participants
            if item.employee_id is not None
        ],
        "from": window.start.isoformat() if window is not None else None,
        "to": window.end.isoformat() if window is not None else None,
        "requestedMinimumCapacity": request.minimum_capacity or 1,
        "requiredFeatures": request.required_features,
        "excludeMeetingId": (
            request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None
        ),
        "excludedCandidateIds": state.excluded_candidate_ids,
    }
    return (
        "You are the Scheduling Agent. Use only the supplied READ functions. Never call DRAFT "
        "or WRITE operations, never provide userId/runId/roles, and never expose reasoning. "
        "For MODIFY_MEETING or CANCEL_MEETING, call get_recent_meeting before any availability "
        "tool. After the target is uniquely hydrated, availability calls use the refreshed "
        "destination window and excludeMeetingId from CANONICAL_CONTEXT. "
        "After employee resolution, room minimumCapacity must be the maximum of "
        "requestedMinimumCapacity and the unique organizer plus resolved employee IDs.\n"
        "CANONICAL_CONTEXT=" + json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    )


def _read_facts_ready(
    request: MeetingRequest,
    free_busy_data: dict[str, Any] | None,
    rooms_data: dict[str, Any] | None,
    recent_data: dict[str, Any] | None,
) -> bool:
    if request.intent in {
        Intent.CREATE_MEETING,
        Intent.FIND_COMMON_TIME,
        Intent.RECOMMEND_ROOM,
    }:
        return free_busy_data is not None and rooms_data is not None
    if request.intent is Intent.MODIFY_MEETING:
        return recent_data is not None and free_busy_data is not None and rooms_data is not None
    if request.intent is Intent.CANCEL_MEETING:
        return request.target_meeting_id is not None or recent_data is not None
    return True


def _canonical_fact_read_call(
    *,
    name: str,
    request: MeetingRequest,
    resolved: list[Participant],
    context: AgentContext,
    index: int,
) -> ModelToolCall:
    if name == "get_recent_meeting":
        payload: FreeBusyInput | SearchRoomsInput | RecentMeetingInput = RecentMeetingInput(
            limit=5
        )
        return ModelToolCall(
            id=f"deterministic-fact-{index}-{name}",
            name=name,
            arguments=payload.model_dump_json(by_alias=True),
        )
    window = request.time_window
    if window is None:
        raise WorkflowError("TIME_WINDOW_REQUIRED", "缺少可调度时间窗口")
    employee_ids = sorted(
        {
            context.user_id,
            *(item.employee_id for item in resolved if item.employee_id is not None),
        }
    )
    exclude_meeting_id = (
        request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None
    )
    if name == "get_employee_free_busy":
        payload = FreeBusyInput(
            employee_ids=employee_ids,
            from_=window.start,
            to=window.end,
            exclude_meeting_id=exclude_meeting_id,
        )
    elif name == "search_available_rooms":
        payload = SearchRoomsInput(
            from_=window.start,
            to=window.end,
            minimum_capacity=max(request.minimum_capacity or 1, len(employee_ids)),
            required_features=request.required_features,
            limit=50,
            exclude_meeting_id=exclude_meeting_id,
        )
    else:
        raise WorkflowError("TOOL_NOT_ALLOWED", "确定性事实补全工具不在白名单")
    return ModelToolCall(
        id=f"deterministic-fact-{index}-{name}",
        name=name,
        arguments=payload.model_dump_json(by_alias=True),
    )


def _missing_read_tools(
    *,
    request: MeetingRequest,
    resolved: list[Participant],
    free_busy_data: dict[str, Any] | None,
    rooms_data: dict[str, Any] | None,
    recent_data: dict[str, Any] | None,
) -> list[str]:
    missing: list[str] = []
    expected_names = {item.name for item in request.required_participants}
    resolved_names = {item.name for item in resolved if item.employee_id is not None}
    if expected_names and not expected_names.issubset(resolved_names):
        missing.append("resolve_employees")
    if (
        request.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        and recent_data is None
    ):
        missing.append("get_recent_meeting")
    if request.intent in {
        Intent.CREATE_MEETING,
        Intent.FIND_COMMON_TIME,
        Intent.RECOMMEND_ROOM,
        Intent.MODIFY_MEETING,
    }:
        if free_busy_data is None:
            missing.append("get_employee_free_busy")
        if rooms_data is None:
            missing.append("search_available_rooms")
    return missing


def _recent_meeting_id(data: dict[str, Any] | None) -> int | None:
    meetings = _recent_meetings(data)
    return meetings[0].id if len(meetings) == 1 else None


def _recent_meeting(
    data: dict[str, Any] | None, target_meeting_id: int | None
) -> MeetingView | None:
    meetings = _recent_meetings(data)
    if target_meeting_id is not None:
        return next((item for item in meetings if item.id == target_meeting_id), None)
    return meetings[0] if len(meetings) == 1 else None


def _recent_meetings(data: dict[str, Any] | None) -> list[MeetingView]:
    if data is None:
        return []
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
    return meetings


def _resolve_target_meeting(
    data: dict[str, Any] | None,
    *,
    request: MeetingRequest,
    message: str,
    request_time: datetime,
) -> tuple[list[MeetingView], list[MeetingView]]:
    meetings = _recent_meetings(data)
    if request.target_meeting_id is not None:
        return (
            [item for item in meetings if item.id == request.target_meeting_id],
            meetings,
        )
    reference = (request.target_meeting_reference or "").strip()
    selector = reference or message
    selected = list(meetings)
    target_date = _target_reference_date(selector, request_time=request_time)
    target_clock = _target_reference_clock(selector)
    if target_date is not None:
        selected = [item for item in selected if item.start_at.date() == target_date.date()]
    if target_clock is not None:
        selected = [
            item for item in selected if (item.start_at.hour, item.start_at.minute) == target_clock
        ]
    if target_date is not None or target_clock is not None:
        return selected, meetings
    title_matches = [
        item
        for item in selected
        if item.title in selector or (item.title != "会议安排" and item.title in message)
    ]
    if title_matches:
        return title_matches, meetings
    if any(marker in selector or marker in message for marker in ("刚才", "刚刚", "最近")):
        return meetings[:1], meetings
    return (meetings if len(meetings) == 1 else []), meetings


def _target_reference_date(value: str, *, request_time: datetime) -> datetime | None:
    match = re.search(
        r"(?:(?P<year>20\d{2})年)?(?:(?P<month>1[0-2]|0?[1-9])月)?"
        r"(?P<day>3[01]|[12]?\d)\s*[号日]",
        value,
    )
    if match is None:
        return None
    try:
        return request_time.replace(
            year=int(match.group("year") or request_time.year),
            month=int(match.group("month") or request_time.month),
            day=int(match.group("day")),
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None


def _target_reference_clock(value: str) -> tuple[int, int] | None:
    match = re.search(
        r"(?P<period>上午|早上|中午|下午|晚上)?\s*"
        r"(?P<hour>2[0-3]|[01]?\d|[零〇一二两三四五六七八九十]{1,3})\s*点"
        r"(?P<half>半)?",
        value,
    )
    if match is None:
        return None
    hour = _chinese_hour(match.group("hour"))
    if hour is None or hour > 23:
        return None
    period = match.group("period")
    if (period in {"下午", "晚上"} and hour < 12) or (period == "中午" and hour < 11):
        hour += 12
    elif period in {"上午", "早上"} and hour == 12:
        hour = 0
    return hour, 30 if match.group("half") else 0


def _chinese_hour(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(value) == 1:
        return digits.get(value)
    return None


def _target_meeting_clarification(
    *, matches: list[MeetingView], visible_meetings: list[MeetingView]
) -> str:
    choices = matches if len(matches) > 1 else visible_meetings
    if not choices:
        return "没有找到与该日期、时间或标题匹配且你可管理的会议，请补充会议日期、开始时间或标题。"
    lines = ["目标会议还不能唯一确定，请从以下会议中说明要操作哪一场："]
    for item in choices[:5]:
        lines.append(
            f"- 会议 {item.id}：{item.title}，"
            f"{item.start_at:%Y-%m-%d %H:%M}-{item.end_at:%H:%M}，{item.room_name}"
        )
    return "\n".join(lines)[:500]


def _hydrate_mutation_target(
    *,
    state: AgentState,
    request: MeetingRequest,
    meeting: MeetingView,
    recent_data: dict[str, Any],
) -> tuple[AgentState, MeetingRequest]:
    draft = state.requirement_draft
    original_duration = int((meeting.end_at - meeting.start_at).total_seconds() / 60)
    explicit_duration = draft.duration_minutes if draft is not None else None
    duration = explicit_duration or original_duration
    destination = request.time_window
    if draft is not None and draft.pending_start_at is not None:
        destination = TimeWindow(
            start=draft.pending_start_at,
            end=draft.pending_start_at + timedelta(minutes=duration),
        )
    elif destination is None or (
        _window_only_selects_target(destination, meeting, request.target_meeting_reference)
        and not _source_changes_time_constraints(state.message)
    ):
        destination = TimeWindow(start=meeting.start_at, end=meeting.end_at)
    required = [
        Participant(name=item.display_name, employee_id=item.employee_id)
        for item in meeting.participants
        if item.participant_type == "REQUIRED"
    ]
    preserve_existing = _preserves_existing_requirements(state.message) or (
        draft is not None and _is_exception_replanning(draft.target_meeting_reference or "")
    )
    required_features = request.required_features
    if (
        not required_features
        and preserve_existing
        and not _source_changes_feature_constraints(state.message)
    ):
        required_features = _meeting_room_features(recent_data, meeting.id)
    hydrated = request.model_copy(
        update={
            "title": meeting.title,
            "meeting_type": meeting.meeting_type,
            "duration_minutes": duration,
            "time_window": destination,
            "required_participants": required,
            "required_features": required_features,
            "minimum_capacity": max(request.minimum_capacity or 1, len(required)),
            "target_meeting_id": meeting.id,
        }
    )
    draft_update: dict[str, object] = {"target_meeting_id": meeting.id}
    if draft is not None:
        draft_update.update(
            {
                "title": meeting.title,
                "meeting_type": meeting.meeting_type,
                "duration_minutes": duration,
                "time_window": destination,
                "required_participant_names": [item.name for item in required],
                "required_features": required_features,
                "minimum_capacity": hydrated.minimum_capacity,
            }
        )
    updated_draft = draft.model_copy(update=draft_update) if draft is not None else None
    hydrated_items = state.requirement_items
    if updated_draft is not None:
        hydrated_items = _requirement_items(
            draft=updated_draft,
            request=hydrated,
            missing_fields=[],
            source=state.message,
            previous_items=state.requirement_items,
            optional_closed=state.optional_requirements_closed,
        )
        inherited_fields: set[str] = set()
        if not _source_changes_meeting_duration(state.message):
            inherited_fields.add("durationMinutes")
        if not _source_has_participant_mutation(state.message):
            inherited_fields.add("requiredParticipants")
        if (
            destination.start == meeting.start_at
            and destination.end == meeting.end_at
            and not _source_changes_time_constraints(state.message)
        ):
            inherited_fields.add("timeWindow")
        if (
            required_features
            and preserve_existing
            and not _source_changes_feature_constraints(state.message)
        ):
            inherited_fields.add("optionalRequirements")
        hydrated_items = [
            item.model_copy(
                update={
                    "status": RequirementSlotStatus.INHERITED,
                    "source": f"目标会议 {meeting.id}",
                    "blocking": False,
                }
            )
            if item.field in inherited_fields
            else item
            for item in hydrated_items
        ]
    return (
        state.model_copy(
            update={
                "meeting_request": hydrated,
                "requirement_draft": updated_draft,
                "requirement_items": hydrated_items,
            }
        ),
        hydrated,
    )


def _window_only_selects_target(
    window: TimeWindow, meeting: MeetingView, reference: str | None
) -> bool:
    if not reference:
        return False
    return window.start <= meeting.start_at < window.end


def _preserves_existing_requirements(message: str) -> bool:
    return any(
        marker in message
        for marker in (
            "其他都不变",
            "其它都不变",
            "要求不变",
            "设备不变",
            "保持不变",
            "默认保留",
        )
    )


def _explicit_meeting_id(source: str) -> int | None:
    match = re.search(
        r"(?:会议\s*(?:ID)?|meeting\s*id|meetingId|#)\s*[:：#]?\s*(\d{1,9})",
        source,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match is not None else None


def _is_exception_replanning(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "异常重排",
            "资源失效",
            "会议室不可用",
            "会议室已失效",
            "原会议室已失效",
            "房间不可用",
        )
    )


def _is_exception_replanning_context(state: AgentState) -> bool:
    if _is_exception_replanning(state.message):
        return True
    draft = state.requirement_draft
    return draft is not None and _is_exception_replanning(draft.target_meeting_reference or "")


def _feature_removal_requested(source: str, alias: str) -> bool:
    escaped = re.escape(alias)
    return bool(
        re.search(
            rf"(?:不再要求|不需要|不要|去掉|取消)(?:使用)?\s*{escaped}|"
            rf"{escaped}\s*(?:不再要求|不需要|不要)",
            source,
        )
    )


def _source_changes_feature_constraints(source: str) -> bool:
    return any(
        alias in source
        and (
            _feature_removal_requested(source, alias)
            or not any(
                _feature_removal_requested(source, other)
                for aliases in SourceFidelityEvaluator._FEATURE_ALIASES.values()
                for other in aliases
            )
        )
        for aliases in SourceFidelityEvaluator._FEATURE_ALIASES.values()
        for alias in aliases
    )


def _source_changes_time_constraints(source: str) -> bool:
    return bool(
        re.search(
            r"(?:顺延|延后|推迟)\s*(?:30|60|90|120)\s*分钟|"
            r"(?:改到|调整到|移到|改为)",
            source,
        )
    )


def _source_changes_meeting_duration(source: str) -> bool:
    scrubbed = re.sub(
        r"(?:允许)?(?:顺延|延后|推迟)\s*(?:30|60|90|120)\s*分钟",
        "",
        source,
    )
    return bool(re.search(r"\d+\s*(?:分钟|个?小时)", scrubbed))


def _meeting_room_features(data: dict[str, Any], meeting_id: int) -> list[str]:
    raw = data.get("roomFeaturesByMeetingId", {})
    if not isinstance(raw, dict):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议房间设备响应格式无效")
    features = raw.get(str(meeting_id), raw.get(meeting_id, []))
    if not isinstance(features, list) or any(not isinstance(item, str) for item in features):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议房间设备响应格式无效")
    return list(dict.fromkeys(features))


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
                    BusyInterval(
                        meeting_id=slot.get("meetingId"),
                        start_at=slot["startAt"],
                        end_at=slot["endAt"],
                    )
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
                busy_intervals=[
                    BusyInterval(
                        meeting_id=slot.get("meetingId"),
                        start_at=slot["startAt"],
                        end_at=slot["endAt"],
                    )
                    for slot in item.get("busySlots", [])
                    if isinstance(slot, dict)
                ],
            )
            for item in raw_rooms
            if isinstance(item, dict)
        ]
        if len(rooms) != len(raw_rooms):
            raise ValueError("room item is invalid")
        return AvailabilitySnapshot(rooms=rooms, employee_busy_slots=employees)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("TOOL_RESPONSE_INVALID", "可用性查询响应格式无效") from exc


def _enrich_unsat_analysis(
    analysis: UnsatAnalysis,
    *,
    resolved: list[Participant],
    organizer_id: int,
) -> UnsatAnalysis:
    names: dict[int, str] = {}
    for item in resolved:
        if item.employee_id is not None:
            names[item.employee_id] = item.name
    names.setdefault(organizer_id, "当前登录用户（我）")
    blockers = [
        blocker.model_copy(
            update={
                "resource_name": (
                    names.get(blocker.resource_id) or blocker.resource_name
                    if blocker.resource_id is not None
                    else blocker.resource_name
                )
            }
        )
        for blocker in analysis.blocking_intervals
    ]
    if not blockers:
        return analysis
    window = analysis.requested_window
    request_window = _visible_conflict_time_range(window.start, window.end)
    visible = []
    for blocker in blockers[:5]:
        label = blocker.resource_name or f"员工 {blocker.resource_id}"
        existing = f"{label}的已有安排"
        if blocker.meeting_id is not None:
            existing += f"（会议 {blocker.meeting_id}）"
        overlap_start = max(window.start, blocker.start_at)
        overlap_end = min(window.end, blocker.end_at)
        overlap = (
            _visible_conflict_time_range(overlap_start, overlap_end)
            if overlap_start < overlap_end
            else "请求窗口内无直接重叠"
        )
        visible.append(
            "本次待排请求"
            f"（{request_window}，连续 {analysis.duration_minutes} 分钟）与{existing}冲突："
            f"已有安排为 {_visible_conflict_time_range(blocker.start_at, blocker.end_at)}，"
            f"重叠时段为 {overlap}；原因：{blocker.reason}"
        )
    summary = (
        "未找到满足全部硬约束的方案。" + "；".join(visible) + "。"
    )
    suggestions = list(analysis.relaxation_suggestions)
    latest_end = max(blocker.end_at for blocker in blockers)
    local_day_end = latest_end.replace(hour=18, minute=0, second=0, microsecond=0)
    if latest_end + timedelta(minutes=analysis.duration_minutes) <= local_day_end:
        suggestions.insert(
            0,
            (
                f"可尝试 {latest_end:%Y-%m-%d %H:%M} 开始，"
                "回复“按你推荐的最近可行时间”后系统会重新校验全部约束。"
            ),
        )
    return analysis.model_copy(
        update={
            "summary": summary[:500],
            "blocking_intervals": blockers,
            "relaxation_suggestions": suggestions[:3],
        }
    )


def _visible_conflict_time_range(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M}-{end:%Y-%m-%d %H:%M}"


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
                "requirement_agent": "requirement_agent",
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
        self._ensure_limits(state, model_increment=1, tool_increment=1)
        sequence_no = state.step_count + 1
        started = time.perf_counter()
        try:
            updated, summary, model_calls, outcomes = self.requirement.execute(state)
            if state.model_call_count + model_calls > self.settings.agent_max_model_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到模型调用上限")
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + model_calls,
                    "tool_call_count": state.tool_call_count + len(outcomes),
                }
            )
            for outcome in outcomes:
                self._record_tool(state=updated, outcome=outcome)
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
            if updated.requirement_items:
                self.sink.emit(
                    "requirement.updated",
                    {
                        "runId": updated.run_id,
                        "revision": updated.requirement_revision,
                        "ready": not updated.missing_fields,
                        "items": [
                            item.model_dump(by_alias=True, mode="json")
                            for item in updated.requirement_items
                        ],
                    },
                )
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
        model_increment = 0 if state.conflict_repair_feedback is not None else 1
        self._ensure_limits(
            state, model_increment=model_increment, tool_increment=tool_increment
        )
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
            updated, summary, outcomes, model_calls = self.scheduling.execute(state, self.context)
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
            if updated.requirement_items != state.requirement_items:
                self.sink.emit(
                    "requirement.updated",
                    {
                        "runId": updated.run_id,
                        "revision": updated.requirement_revision,
                        "ready": not updated.missing_fields,
                        "items": [
                            item.model_dump(by_alias=True, mode="json")
                            for item in updated.requirement_items
                        ],
                    },
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
            if updated.unsat_analysis is not None:
                self.sink.emit(
                    "plan.unsat",
                    {
                        "runId": updated.run_id,
                        "unsatAnalysis": updated.unsat_analysis.model_dump(
                            by_alias=True, mode="json"
                        ),
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
                        "answerSummary": updated.answer_summary,
                        "conflictRepair": updated.conflict_repair_feedback is not None,
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
                or stable_idempotency_identity(state.run_id, operation, state.confirmation_token)
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
                    error_code=exc.details.get("conflict.type", "BOOKING_CONFLICT"),
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
            answer = state.answer_summary or str(
                _clarification_contract(
                    issue_codes=state.missing_fields,
                    request=state.meeting_request,
                )["fallbackMessage"]
            )
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
        sequence = state.step_count * 10 + iteration + (5 if event["phase"] == "VERIFY" else 0)
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
        error_code: str,
    ) -> None:
        self.repository.record_tool_call(
            tool_call_id=tool_call_id,
            run_id=state.run_id,
            tool_name=tool_name,
            risk_level=risk_level,
            sanitized_args={"riskGuard": "HITL_ACCEPTED", "errorCode": error_code},
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
                "errorCode": error_code,
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
        if state.continuation_turn and state.requirement_draft is not None:
            return "requirement_agent"
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


def _synchronous_conflict_replan_state(*, state: AgentState, error: JavaToolError) -> AgentState:
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
        f"hard:{constraint.type}={constraint.value}" for constraint in request.hard_constraints
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
        requirement=RequirementAgent(
            provider=provider, runner=runner, tools=tools, context=context
        ),
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
