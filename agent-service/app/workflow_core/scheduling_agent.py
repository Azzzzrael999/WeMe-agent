"""Scheduling Agent read loop and deterministic candidate/draft construction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.agent_loop import (
    READ_TOOL_DEFINITIONS,
    LoopStopReason,
    ReadToolGate,
    ToolGateError,
)
from app.providers.base import (
    ModelCompletion,
    ModelProvider,
    ModelProviderError,
    StructuredModelRunner,
    ToolLoopMessage,
    ToolModelRequest,
)
from app.scheduling import ScheduleSolver, ScheduleSolverError
from app.schemas.agent import (
    AgentState,
    CancellationDraftView,
    CreateDraftView,
    Intent,
    OperationType,
    Participant,
    RescheduleDraftView,
    Route,
    RunStatus,
)
from app.security import AgentContext
from app.tools.java import (
    CreateBookingDraftInput,
    JavaReadToolClient,
    JavaToolError,
    RescheduleDraftInput,
    ToolOutcome,
    stable_tool_identity,
)
from app.workflow_core.clarification import _compose_clarification
from app.workflow_core.common import WorkflowError, _apply_completions
from app.workflow_core.scheduling_support import (
    _canonical_fact_read_call,
    _enrich_unsat_analysis,
    _hydrate_mutation_target,
    _is_exception_replanning_context,
    _missing_read_tools,
    _participants_from_java,
    _read_facts_ready,
    _recent_meeting,
    _recent_meeting_id,
    _resolve_target_meeting,
    _scheduling_problem,
    _scheduling_system_prompt,
    _snapshot_from_java,
    _target_meeting_clarification,
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
            "当前会议室已被占用，请切换其他的编排选项。已重新读取最新占用情况并生成其他可用方案。"
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
