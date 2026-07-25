"""Day 4 LangGraph orchestration for exactly four specialised agents."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

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
from app.schemas.agent import (
    AgentState,
    Intent,
    Participant,
    PolicyResult,
    PolicySelection,
    RequirementExtraction,
    Route,
    RunStatus,
    SchedulingPlan,
    SupervisorDecision,
)
from app.security import AgentContext
from app.tools.java import JavaReadToolClient, JavaToolError, ToolOutcome


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
                    "intent": extraction.meeting_request.intent,
                    "meeting_request": extraction.meeting_request,
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
            return (
                state.model_copy(update={"policy_result": result, "citations": []}),
                result.summary,
            )

        candidate_summary = "; ".join(
            f"{chunk.chunk_id}: {chunk.title}" for chunk in candidates[:5]
        )
        selection = _model_output(
            provider=self.provider,
            runner=self.runner,
            agent_name="policy",
            system_prompt=(
                "You are the Policy Agent. Select only evidence chunk IDs supplied by "
                "the retriever and "
                "return a concise rule answer. Never invent citations or make a booking decision."
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
    provider: ModelProvider
    runner: StructuredModelRunner
    tools: JavaReadToolClient

    def execute(
        self, state: AgentState, context: AgentContext
    ) -> tuple[AgentState, str, ToolOutcome]:
        request = state.meeting_request
        if request is None:
            raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
        plan = _model_output(
            provider=self.provider,
            runner=self.runner,
            agent_name="scheduling",
            system_prompt=(
                "You are the Scheduling Agent. Choose only Day 4 Java READ tools. "
                "Do not invoke a solver, create a draft, confirm a booking, or expose reasoning."
            ),
            user_prompt=(
                f"Intent: {request.intent.value}; title: {request.title}; "
                "required participants: "
                f"{[participant.name for participant in request.required_participants]}"
            ),
            output_type=SchedulingPlan,
        )
        if "resolve_employees" not in plan.tool_names:
            raise WorkflowError("TOOL_PLAN_INVALID", "调度计划必须先解析必需参会者")
        names = [participant.name for participant in request.required_participants]
        if not names:
            raise WorkflowError("REQUIREMENT_MISSING", "缺少必需参会者")
        try:
            outcome = self.tools.resolve_employees(context=context, names=names)
        except JavaToolError as exc:
            raise WorkflowError(exc.code, "Java只读查询暂不可用") from exc
        resolved = [
            _participant_from_java(employee)
            for employee in outcome.data.get("employees", [])
            if isinstance(employee, dict)
        ]
        return (
            state.model_copy(update={"resolved_employees": resolved, "next_route": Route.FINAL}),
            plan.summary,
            outcome,
        )


def _participant_from_java(value: dict[str, Any]) -> Participant:
    employee_id = value.get("employeeId")
    display_name = value.get("displayName")
    if isinstance(employee_id, int) and isinstance(display_name, str):
        return Participant(name=display_name, employee_id=employee_id)
    return Participant(name="已解析员工", employee_id=None)


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
    sink: EventSink = field(default_factory=EventSink)
    latest_state: AgentState | None = None

    def stream(self, initial_state: AgentState) -> Iterator[tuple[str, dict[str, object]]]:
        graph = self._build_graph()
        self.latest_state = initial_state
        try:
            for update in graph.stream(
                initial_state,
                # The explicit state counter is the authoritative Day 4 limit. Give
                # LangGraph one extra transition so its generic recursion guard does
                # not mask the stable AGENT_STEP_LIMIT_EXCEEDED business error.
                config={"recursion_limit": self.settings.agent_max_graph_nodes + 1},
            ):
                for node_state in update.values():
                    self.latest_state = AgentState.model_validate(node_state)
                yield from self.sink.drain()
        except WorkflowError:
            raise
        except GraphRecursionError as exc:
            raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到图步骤上限") from exc
        except Exception as exc:
            raise WorkflowError("AGENT_GRAPH_FAILED", "智能调度工作流执行失败") from exc

    def _build_graph(self) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
        graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
        graph.add_node("supervisor_route", self._supervisor_node)
        graph.add_node("requirement_agent", self._requirement_node)
        graph.add_node("policy_agent", self._policy_node)
        graph.add_node("scheduling_agent", self._scheduling_node)
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
        graph.add_edge("scheduling_agent", "compose_final")
        graph.add_edge("compose_final", END)
        return graph.compile()

    @staticmethod
    def _dump(state: AgentState) -> dict[str, Any]:
        return state.model_dump(mode="json")

    def _supervisor_node(self, state: AgentState) -> dict[str, Any]:
        return self._record_agent_step(
            state=state,
            agent_name="supervisor",
            node_name="supervisor_route",
            input_summary="接收用户任务并选择下一专业节点。",
            execute=self.supervisor.execute,
        )

    def _requirement_node(self, state: AgentState) -> dict[str, Any]:
        return self._record_agent_step(
            state=state,
            agent_name="requirement",
            node_name="requirement_agent",
            input_summary="从用户任务提取可验证的会议约束。",
            execute=self.requirement.execute,
        )

    def _policy_node(self, state: AgentState) -> dict[str, Any]:
        return self._record_agent_step(
            state=state,
            agent_name="policy",
            node_name="policy_agent",
            input_summary="检索会议制度并选择可验证引用。",
            execute=self.policy.execute,
        )

    def _scheduling_node(self, state: AgentState) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=1, tool_increment=1)
        started = time.perf_counter()
        sequence_no = state.step_count + 1
        try:
            updated, summary, outcome = self.scheduling.execute(state, self.context)
            updated = updated.model_copy(
                update={
                    "step_count": sequence_no,
                    "model_call_count": state.model_call_count + 1,
                    "tool_call_count": state.tool_call_count + 1,
                }
            )
            self.repository.record_tool_call(
                tool_call_id=outcome.tool_call_id,
                run_id=updated.run_id,
                tool_name=outcome.tool_name,
                risk_level=outcome.risk_level,
                sanitized_args={"nameCount": len(state.meeting_request.required_participants)}
                if state.meeting_request is not None
                else {},
                result_summary=outcome.summary,
                status="SUCCEEDED",
                duration_ms=outcome.duration_ms,
            )
            self.sink.emit(
                "tool.call",
                {
                    "runId": updated.run_id,
                    "toolCallId": outcome.tool_call_id,
                    "toolName": outcome.tool_name,
                    "riskLevel": outcome.risk_level,
                    "status": "SUCCEEDED",
                    "summary": outcome.summary,
                    "durationMs": outcome.duration_ms,
                },
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            step_id = _new_id("step")
            self.repository.record_step(
                step_id=step_id,
                run_id=updated.run_id,
                sequence_no=sequence_no,
                agent_name="scheduling",
                node_name="scheduling_agent",
                status="SUCCEEDED",
                input_summary="基于结构化需求选择并执行Java只读查询。",
                output_summary=summary,
                duration_ms=duration_ms,
            )
            self.sink.emit(
                "agent.step",
                {
                    "runId": updated.run_id,
                    "stepId": step_id,
                    "sequenceNo": sequence_no,
                    "agentName": "scheduling",
                    "nodeName": "scheduling_agent",
                    "status": "SUCCEEDED",
                    "summary": summary,
                    "durationMs": duration_ms,
                },
            )
            self.latest_state = updated
            return self._dump(updated)
        except WorkflowError as exc:
            self._record_failed_step(state, "scheduling", "scheduling_agent", sequence_no, exc)
            raise

    def _final_node(self, state: AgentState) -> dict[str, Any]:
        self._ensure_limits(state, model_increment=0, tool_increment=0)
        sequence_no = state.step_count + 1
        started = time.perf_counter()
        if state.missing_fields:
            answer = "请补充：" + "、".join(state.missing_fields)
            status = RunStatus.WAITING_USER_INPUT
        elif state.policy_result is not None and state.intent is Intent.QUERY_POLICY:
            answer = state.policy_result.summary
            status = RunStatus.SUCCEEDED
        else:
            answer = "已完成结构化解析和只读查询"
            status = RunStatus.SUCCEEDED
        updated = state.model_copy(
            update={
                "step_count": sequence_no,
                "answer_summary": answer,
                "status": status,
                "next_route": Route.FINAL,
            }
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        step_id = _new_id("step")
        self.repository.record_step(
            step_id=step_id,
            run_id=updated.run_id,
            sequence_no=sequence_no,
            agent_name="deterministic",
            node_name="compose_final",
            status="SUCCEEDED",
            input_summary="汇总已验证的结构化结果。",
            output_summary=answer,
            duration_ms=duration_ms,
        )
        self.sink.emit(
            "agent.step",
            {
                "runId": updated.run_id,
                "stepId": step_id,
                "sequenceNo": sequence_no,
                "agentName": "deterministic",
                "nodeName": "compose_final",
                "status": "SUCCEEDED",
                "summary": answer,
                "durationMs": duration_ms,
            },
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
            duration_ms = int((time.perf_counter() - started) * 1000)
            step_id = _new_id("step")
            self.repository.record_step(
                step_id=step_id,
                run_id=updated.run_id,
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
                    "runId": updated.run_id,
                    "stepId": step_id,
                    "sequenceNo": sequence_no,
                    "agentName": agent_name,
                    "nodeName": node_name,
                    "status": "SUCCEEDED",
                    "summary": summary,
                    "durationMs": duration_ms,
                },
            )
            self.latest_state = updated
            return self._dump(updated)
        except WorkflowError as exc:
            self._record_failed_step(state, agent_name, node_name, sequence_no, exc)
            raise

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
            input_summary="执行节点时发生受控错误。",
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
            raise WorkflowError("AGENT_STEP_LIMIT_EXCEEDED", "已达到图步骤上限")

    @staticmethod
    def _route_after_supervisor(state: AgentState) -> str:
        route = state.next_route
        if route is Route.POLICY:
            return "policy_agent"
        if route is Route.REQUIREMENT:
            return "requirement_agent"
        return "compose_final"

    @staticmethod
    def _route_after_requirement(state: AgentState) -> str:
        route = state.next_route
        if route is Route.POLICY:
            return "policy_agent"
        if route is Route.SCHEDULING:
            return "scheduling_agent"
        return "compose_final"

    @staticmethod
    def _route_after_policy(state: AgentState) -> str:
        route = state.next_route
        return "scheduling_agent" if route is Route.SCHEDULING else "compose_final"


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
    )
