"""LangGraph construction, HITL execution, persistence, and workflow telemetry."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.agent_loop import (
    LoopStopReason,
)
from app.checkpoints import checkpoint_thread_id
from app.checkpoints.redis import RedisCheckpointError
from app.config import Settings
from app.persistence import MetadataRepository
from app.providers.base import (
    ModelProvider,
    StructuredModelRunner,
)
from app.rag.policies import PolicyRetriever
from app.schemas.agent import (
    AgentResumeRequest,
    AgentState,
    CancellationDraftView,
    ConflictRepairFeedbackState,
    CreateDraftView,
    HitlResumeCommand,
    Intent,
    OperationType,
    RescheduleDraftView,
    ResumeAction,
    Route,
    RunStatus,
)
from app.security import AgentContext
from app.tools.java import (
    JavaReadToolClient,
    JavaToolError,
    ToolOutcome,
    stable_idempotency_identity,
    stable_tool_identity,
)
from app.workflow_core.agents import PolicyAgent, SupervisorAgent
from app.workflow_core.clarification import _clarification_contract
from app.workflow_core.common import EventSink, WorkflowError
from app.workflow_core.requirement_agent import RequirementAgent
from app.workflow_core.scheduling_agent import SchedulingAgent

logger = logging.getLogger(__name__)


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
        self._ensure_limits(state, model_increment=model_increment, tool_increment=tool_increment)
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
