"""Day 5 LangGraph orchestration for exactly four specialised runtime Agents.

``SupervisorAgent``, ``RequirementAgent``, ``PolicyAgent`` and
``SchedulingAgent`` remain the only runtime Agents.  Solver, HITL and booking
operations below are deterministic graph nodes; model output never receives a
general write Tool surface.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.checkpoints import checkpoint_thread_id
from app.checkpoints.redis import RedisCheckpointError
from app.config import Settings
from app.persistence import MetadataRepository
from app.providers.base import (
    ModelOutputError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    StructuredModelRunner,
)
from app.rag.policies import PolicyRetrievalError, PolicyRetriever
from app.scheduling import ScheduleSolver, ScheduleSolverError
from app.schemas.agent import (
    AgentResumeRequest,
    AgentState,
    AvailabilitySnapshot,
    BusyInterval,
    EmployeeBusySlots,
    HitlResumeCommand,
    Intent,
    Participant,
    PolicyResult,
    PolicySelection,
    RequirementExtraction,
    ResumeAction,
    RoomAvailability,
    Route,
    RunStatus,
    SchedulingPlan,
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
    ToolOutcome,
    stable_idempotency_identity,
    stable_tool_identity,
)

logger = logging.getLogger(__name__)


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

    def execute(self, state: AgentState) -> tuple[AgentState, str]:
        decision = _model_output(
            provider=self.provider,
            runner=self.runner,
            agent_name="supervisor",
            system_prompt=(
                "You are the Supervisor Agent for an enterprise meeting scheduler. "
                "Route only with the supplied JSON schema. Never call business tools and never "
                "expose reasoning."
            ),
            user_prompt=state.message,
            output_type=SupervisorDecision,
        )
        intent = Intent.QUERY_POLICY if decision.route is Route.POLICY else state.intent
        return (
            state.model_copy(update={"next_route": decision.route, "intent": intent}),
            decision.summary,
        )


@dataclass(frozen=True)
class RequirementAgent:
    provider: ModelProvider
    runner: StructuredModelRunner

    def execute(self, state: AgentState) -> tuple[AgentState, str]:
        extraction = _model_output(
            provider=self.provider,
            runner=self.runner,
            agent_name="requirement",
            system_prompt=(
                "You are the Requirement Agent. Extract a meeting request into the exact schema. "
                "Normalize relative dates to Asia/Shanghai absolute ISO timestamps. "
                "Do not schedule, draft, confirm, or expose reasoning."
            ),
            user_prompt=state.message,
            output_type=RequirementExtraction,
        )
        request = extraction.meeting_request
        # EDIT is intentionally revalidated by Requirement before it reaches
        # Scheduling.  Only the documented bounded fields may override it.
        if state.edited_draft is not None and state.edited_draft.start_at is not None:
            start_at = state.edited_draft.start_at
            request = request.model_copy(
                update={
                    "time_window": TimeWindow(
                        start=start_at,
                        end=start_at + timedelta(minutes=request.duration_minutes),
                    )
                }
            )
        next_route = (
            Route.CLARIFICATION
            if extraction.missing_fields
            else Route.POLICY
            if extraction.needs_policy
            else Route.SCHEDULING
        )
        return (
            state.model_copy(
                update={
                    "intent": request.intent,
                    "meeting_request": request,
                    "missing_fields": extraction.missing_fields,
                    "next_route": next_route,
                }
            ),
            extraction.summary,
        )


@dataclass(frozen=True)
class PolicyAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    retriever: PolicyRetriever

    def execute(self, state: AgentState) -> tuple[AgentState, str]:
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
            ), result.summary

        candidate_summary = "; ".join(
            f"{chunk.chunk_id}: {chunk.title}" for chunk in candidates[:5]
        )
        selection = _model_output(
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
            state.model_copy(
                update={"policy_result": result, "citations": citations, "next_route": next_route}
            ),
            result.summary,
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

    def execute(
        self, state: AgentState, context: AgentContext
    ) -> tuple[AgentState, str, list[ToolOutcome]]:
        request = state.meeting_request
        if request is None:
            raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
        plan = _model_output(
            provider=self.provider,
            runner=self.runner,
            agent_name="scheduling",
            system_prompt=(
                "You are the Scheduling Agent. Choose only Java READ tools. "
                "Do not invoke a solver, create a draft, confirm a booking, or expose reasoning."
            ),
            user_prompt=_scheduling_model_prompt(request),
            output_type=SchedulingPlan,
        )
        if "resolve_employees" not in plan.tool_names:
            raise WorkflowError("TOOL_PLAN_INVALID", "调度计划必须先解析必需参会者")
        names = list(
            dict.fromkeys(participant.name for participant in request.required_participants)
        )
        if not names:
            raise WorkflowError("REQUIREMENT_MISSING", "缺少必需参会者")
        try:
            resolved_outcome = self.tools.resolve_employees(context=context, names=names)
        except JavaToolError as exc:
            raise WorkflowError(exc.code, "Java 只读查询暂不可用") from exc
        resolved = _participants_from_java(resolved_outcome.data)
        unresolved = resolved_outcome.data.get("unresolvedNames", [])
        if not isinstance(unresolved, list) or unresolved or len(resolved) < len(names):
            raise WorkflowError("EMPLOYEE_UNRESOLVED", "存在无法解析的必需参会者")
        if request.intent is not Intent.CREATE_MEETING:
            return (
                state.model_copy(
                    update={"resolved_employees": resolved, "next_route": Route.FINAL}
                ),
                plan.summary,
                [resolved_outcome],
            )

        window = request.time_window
        if window is None:
            raise WorkflowError("REQUIREMENT_MISSING", "缺少可调度的时间窗口")
        required_ids = sorted(
            {
                context.user_id,
                *(participant.employee_id for participant in resolved if participant.employee_id),
            }
        )
        minimum_capacity = max(request.minimum_capacity or 1, len(required_ids))
        try:
            free_busy_outcome = self.tools.get_employee_free_busy(
                context=context,
                employee_ids=required_ids,
                from_=window.start,
                to=window.end,
            )
            rooms_outcome = self.tools.search_available_rooms(
                context=context,
                from_=window.start,
                to=window.end,
                minimum_capacity=minimum_capacity,
                required_features=request.required_features,
            )
        except JavaToolError as exc:
            raise WorkflowError(exc.code, "Java 可用性查询暂不可用") from exc
        snapshot = _snapshot_from_java(free_busy_outcome.data, rooms_outcome.data)
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

        outcomes = [resolved_outcome, free_busy_outcome, rooms_outcome]
        if not result.has_solution:
            assert result.unsat is not None
            return (
                state.model_copy(
                    update={
                        "resolved_employees": resolved,
                        "availability_snapshot": snapshot,
                        "schedule_candidates": [],
                        "selected_candidate_id": None,
                        "unsat_analysis": result.unsat,
                        "answer_summary": result.unsat.summary,
                        "next_route": Route.FINAL,
                    }
                ),
                result.unsat.summary,
                outcomes,
            )

        candidates = list(result.candidates)
        # The solver already invokes this validator.  Keeping this explicit
        # boundary makes the public event invariant obvious to future changes.
        if any(
            not self.solver.validator.is_valid(problem=problem, candidate=item)
            for item in candidates
        ):
            raise WorkflowError("SCHEDULE_VALIDATION_FAILED", "候选方案未通过独立硬约束校验")
        selected = candidates[0]
        generation = state.draft_generation + 1
        draft_call_id = stable_tool_identity(
            state.run_id, "create_booking_draft", f"{selected.candidate_id}:{generation}"
        )
        try:
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
        except JavaToolError as exc:
            raise WorkflowError(exc.code, "预约草案创建暂不可用") from exc
        outcomes.append(draft_outcome)
        return (
            state.model_copy(
                update={
                    "resolved_employees": resolved,
                    "availability_snapshot": snapshot,
                    "schedule_candidates": candidates,
                    "selected_candidate_id": selected.candidate_id,
                    "unsat_analysis": None,
                    "draft": draft_response.draft,
                    "confirmation_token": draft_response.confirmation_token,
                    "draft_expires_at": draft_response.expires_at,
                    "draft_tool_call_id": draft_call_id,
                    "draft_generation": generation,
                    "confirm_tool_call_id": None,
                    "confirm_idempotency_key": None,
                    "pending_request_no": None,
                    "business_result": None,
                    "status": RunStatus.WAITING_CONFIRMATION,
                    "next_route": Route.HITL,
                }
            ),
            "已生成并校验候选方案，等待用户确认草案",
            outcomes,
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


def _scheduling_model_prompt(request: Any) -> str:
    participant_names = [participant.name for participant in request.required_participants]
    return (
        f"Intent: {request.intent.value}; title: {request.title}; "
        f"required participants: {participant_names}"
    )


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


def _model_output(
    *,
    provider: ModelProvider,
    runner: StructuredModelRunner,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    output_type: type[Any],
) -> Any:
    try:
        return runner.invoke(
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
        graph.add_edge(START, "supervisor_route")
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
            {"compose_final": "compose_final", "end": END},
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
        return self._record_agent_step(
            state=state,
            agent_name="requirement",
            node_name="requirement_agent",
            input_summary="Extract a validated meeting request.",
            execute=self.requirement.execute,
        )

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
        try:
            updated, summary, outcomes = self.scheduling.execute(state, self.context)
            actual_tool_count = len(outcomes)
            if state.tool_call_count + actual_tool_count > self.settings.agent_max_tool_calls:
                raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到工具调用上限")
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + 1,
                    "tool_call_count": state.tool_call_count + actual_tool_count,
                }
            )
            for outcome in outcomes:
                self._record_tool(state=updated, outcome=outcome)
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
                        "draft": updated.draft.model_dump(by_alias=True, mode="json"),
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
            update["confirm_tool_call_id"] = state.confirm_tool_call_id or stable_tool_identity(
                state.run_id, "confirm_booking", state.confirmation_token
            )
            update["confirm_idempotency_key"] = (
                state.confirm_idempotency_key
                or stable_idempotency_identity(
                    state.run_id, "confirm_booking", state.confirmation_token
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
        try:
            outcome, confirmation = self.scheduling.tools.confirm_booking(
                context=self.context,
                confirmation_token=state.confirmation_token,
                tool_call_id=state.confirm_tool_call_id,
                idempotency_key=state.confirm_idempotency_key,
            )
        except JavaToolError as exc:
            error = WorkflowError(exc.code, "预约确认暂不可用")
            self._record_failed_step(state, "deterministic", "confirm_booking", sequence_no, error)
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
        execute: Callable[[AgentState], tuple[AgentState, str]],
    ) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=1, tool_increment=0)
        sequence_no = state.step_count + 1
        started = time.perf_counter()
        try:
            updated, summary = execute(state)
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + 1,
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
            raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到图步骤或调用上限")

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
        return "end" if state.status is RunStatus.WAITING_BUSINESS_RESULT else "compose_final"


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
    if outcome.tool_name == "create_booking_draft":
        return {"candidateId": state.selected_candidate_id, "riskGuard": "SOLVER_VALIDATED"}
    if outcome.tool_name == "confirm_booking":
        # Never place confirmation token or idempotency key into Trace.
        return {"riskGuard": "HITL_ACCEPTED"}
    return {}


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
        scheduling=SchedulingAgent(provider=provider, runner=runner, tools=tools),
        context=context,
        checkpoint_saver=checkpoint_saver,
    )
