"""Canonical read-tool definitions, validation, and execution gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.providers.base import ModelToolCall, ToolDefinition
from app.schemas.agent import (
    AgentState,
    Intent,
    Participant,
)
from app.security import AgentContext
from app.tools.java import (
    FreeBusyInput,
    JavaReadToolClient,
    JavaToolError,
    RecentMeetingInput,
    ResolveEmployeesInput,
    SearchRoomsInput,
    ToolInput,
    ToolOutcome,
    stable_tool_identity,
)

READ_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="resolve_employees",
        description="Resolve participant names against the authenticated user's organisation.",
        parameters=ResolveEmployeesInput.model_json_schema(by_alias=True),
    ),
    ToolDefinition(
        name="get_employee_free_busy",
        description="Read required participants' busy intervals in the canonical time window.",
        parameters=FreeBusyInput.model_json_schema(by_alias=True),
    ),
    ToolDefinition(
        name="search_available_rooms",
        description="Read rooms satisfying canonical time, capacity and feature constraints.",
        parameters=SearchRoomsInput.model_json_schema(by_alias=True),
    ),
    ToolDefinition(
        name="get_recent_meeting",
        description="Resolve an explicit recent-meeting reference for modify/cancel requests.",
        parameters=RecentMeetingInput.model_json_schema(by_alias=True),
    ),
)


class ToolGateError(RuntimeError):
    def __init__(self, code: str, *, recoverable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.recoverable = recoverable


@dataclass(frozen=True)
class GatedToolResult:
    outcome: ToolOutcome
    fingerprint: str
    observation: str


@dataclass
class ReadToolGate:
    tools: JavaReadToolClient

    def execute(
        self,
        *,
        call: ModelToolCall,
        state: AgentState,
        context: AgentContext,
        resolved_employees: list[Participant],
        fingerprints: set[str],
    ) -> GatedToolResult:
        request = state.meeting_request
        if request is None:
            raise ToolGateError("REQUIREMENT_MISSING", recoverable=False)
        input_type = _INPUT_TYPES.get(call.name)
        if input_type is None:
            raise ToolGateError("TOOL_NOT_ALLOWED", recoverable=False)
        try:
            payload = input_type.model_validate_json(call.arguments)
        except ValidationError as exc:
            raise ToolGateError("TOOL_ARGUMENTS_INVALID") from exc
        payload = self._canonicalize_mutation_exclusion(
            name=call.name,
            payload=payload,
            state=state,
        )
        self._validate_business_context(
            name=call.name,
            payload=payload,
            state=state,
            context=context,
            resolved_employees=resolved_employees,
        )
        canonical = payload.model_dump(by_alias=True, mode="json")
        fingerprint = _fingerprint(call.name, canonical)
        if fingerprint in fingerprints:
            raise ToolGateError("DUPLICATE_TOOL_FINGERPRINT")
        # EDIT and conflict recovery intentionally start a fresh fact epoch.
        # Keep retries idempotent inside an epoch without replaying stale Java
        # audit results or colliding in Python Trace across epochs.
        execution_epoch = (
            f"draft-{state.draft_generation}:replan-{state.replan_count}:"
            f"fact-{state.loop_iteration}:trace-{context.trace_id}"
        )
        business_tool_id = stable_tool_identity(
            context.run_id, call.name, f"{execution_epoch}:{fingerprint}"
        )
        try:
            outcome = self._invoke(
                name=call.name,
                payload=payload,
                context=context,
                tool_call_id=business_tool_id,
            )
        except JavaToolError as exc:
            raise ToolGateError(exc.code, recoverable=exc.code not in {"TOOL_FORBIDDEN"}) from exc
        observation = json.dumps(
            {"toolName": call.name, "data": outcome.data},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(observation.encode("utf-8")) > 32 * 1024:
            raise ToolGateError("TOOL_RESULT_TOO_LARGE", recoverable=False)
        return GatedToolResult(outcome=outcome, fingerprint=fingerprint, observation=observation)

    @staticmethod
    def _canonicalize_mutation_exclusion(
        *, name: str, payload: ToolInput, state: AgentState
    ) -> ToolInput:
        request = state.meeting_request
        assert request is not None
        if (
            request.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
            and request.target_meeting_id is None
            and name != "get_recent_meeting"
        ):
            raise ToolGateError("TARGET_MEETING_REQUIRED")
        if not isinstance(payload, FreeBusyInput | SearchRoomsInput):
            return payload
        expected = request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None
        if payload.exclude_meeting_id not in {None, expected}:
            raise ToolGateError("TOOL_CONTEXT_MISMATCH")
        return payload.model_copy(update={"exclude_meeting_id": expected})

    @staticmethod
    def _validate_business_context(
        *,
        name: str,
        payload: ToolInput,
        state: AgentState,
        context: AgentContext,
        resolved_employees: list[Participant],
    ) -> None:
        request = state.meeting_request
        assert request is not None
        window = request.time_window
        expected_names = list(dict.fromkeys(item.name for item in request.required_participants))
        if name == "resolve_employees":
            assert isinstance(payload, ResolveEmployeesInput)
            if not expected_names:
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
            if payload.names != expected_names or payload.department_names:
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
            return
        if name == "get_recent_meeting":
            if request.intent.value not in {"MODIFY_MEETING", "CANCEL_MEETING"}:
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
            return
        if window is None:
            raise ToolGateError("TIME_WINDOW_REQUIRED", recoverable=False)
        if name == "get_employee_free_busy":
            assert isinstance(payload, FreeBusyInput)
            resolved_ids = {
                item.employee_id for item in resolved_employees if item.employee_id is not None
            }
            expected_ids = sorted({context.user_id, *resolved_ids})
            if (
                payload.employee_ids != expected_ids
                or payload.from_ != window.start
                or payload.to != window.end
                or payload.exclude_meeting_id
                != (request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None)
            ):
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
        elif name == "search_available_rooms":
            assert isinstance(payload, SearchRoomsInput)
            resolved_ids = {
                item.employee_id for item in resolved_employees if item.employee_id is not None
            }
            expected_capacity = max(
                request.minimum_capacity or 1,
                len({context.user_id, *resolved_ids}),
            )
            if (
                payload.from_ != window.start
                or payload.to != window.end
                or payload.minimum_capacity != expected_capacity
                or payload.required_features != request.required_features
                or payload.limit != 50
                or payload.exclude_meeting_id
                != (request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None)
            ):
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")

    def _invoke(
        self,
        *,
        name: str,
        payload: ToolInput,
        context: AgentContext,
        tool_call_id: str,
    ) -> ToolOutcome:
        if isinstance(payload, ResolveEmployeesInput):
            return self.tools.resolve_employees(
                context=context,
                names=payload.names,
                department_names=payload.department_names,
                tool_call_id=tool_call_id,
            )
        if isinstance(payload, FreeBusyInput):
            return self.tools.get_employee_free_busy(
                context=context,
                employee_ids=payload.employee_ids,
                from_=payload.from_,
                to=payload.to,
                exclude_meeting_id=payload.exclude_meeting_id,
                tool_call_id=tool_call_id,
            )
        if isinstance(payload, SearchRoomsInput):
            return self.tools.search_available_rooms(
                context=context,
                from_=payload.from_,
                to=payload.to,
                minimum_capacity=payload.minimum_capacity,
                required_features=payload.required_features,
                exclude_meeting_id=payload.exclude_meeting_id,
                tool_call_id=tool_call_id,
            )
        assert isinstance(payload, RecentMeetingInput)
        return self.tools.get_recent_meeting(
            context=context, limit=payload.limit, tool_call_id=tool_call_id
        )


_INPUT_TYPES: dict[str, type[ToolInput]] = {
    "resolve_employees": ResolveEmployeesInput,
    "get_employee_free_busy": FreeBusyInput,
    "search_available_rooms": SearchRoomsInput,
    "get_recent_meeting": RecentMeetingInput,
}


def _fingerprint(name: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()


def tool_fingerprint(name: str, payload: dict[str, Any]) -> str:
    """Public test/evaluation boundary for canonical Tool-call deduplication."""

    return _fingerprint(name, payload)
