"""Explicit, allow-listed Java Tool client.

The model-facing scheduling plan may select READ operations only.  Draft and
write operations deliberately have dedicated Python methods; deterministic
workflow nodes invoke them after solver and HITL guards respectively.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from app.config import Settings
from app.schemas.agent import AgentSchema, BookingDraft, MeetingView
from app.security import AgentContext


class ToolInput(AgentSchema):
    """Strict schema base for every Java Tool argument."""


class ResolveEmployeesInput(ToolInput):
    names: list[str] = Field(max_length=50)
    department_names: list[str] = Field(default_factory=list, serialization_alias="departmentNames")


class FreeBusyInput(ToolInput):
    employee_ids: list[int] = Field(min_length=1, max_length=50, serialization_alias="employeeIds")
    from_: datetime = Field(serialization_alias="from")
    to: datetime

    @model_validator(mode="after")
    def validate_window(self) -> FreeBusyInput:
        if self.to <= self.from_ or (self.to - self.from_).days > 14:
            raise ValueError("free-busy window must be positive and no more than 14 days")
        return self


class SearchRoomsInput(ToolInput):
    from_: datetime = Field(serialization_alias="from")
    to: datetime
    minimum_capacity: int = Field(ge=1, le=10000, serialization_alias="minimumCapacity")
    required_features: list[str] = Field(
        default_factory=list, max_length=50, serialization_alias="requiredFeatures"
    )
    limit: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def validate_window(self) -> SearchRoomsInput:
        if self.to <= self.from_ or (self.to - self.from_).days > 14:
            raise ValueError("room search window must be positive and no more than 14 days")
        return self


class RecentMeetingInput(ToolInput):
    limit: int = Field(default=5, ge=1, le=5)


class CreateBookingDraftInput(ToolInput):
    title: str = Field(min_length=1, max_length=128)
    meeting_type: str = Field(min_length=1, max_length=32, serialization_alias="meetingType")
    room_id: int = Field(ge=1, serialization_alias="roomId")
    start_at: datetime = Field(serialization_alias="startAt")
    end_at: datetime = Field(serialization_alias="endAt")
    required_participant_ids: list[int] = Field(
        min_length=1, max_length=100, serialization_alias="requiredParticipantIds"
    )
    optional_participant_ids: list[int] = Field(
        default_factory=list, max_length=100, serialization_alias="optionalParticipantIds"
    )
    create_video_conference: bool = Field(
        default=False, serialization_alias="createVideoConference"
    )

    @model_validator(mode="after")
    def validate_draft_window(self) -> CreateBookingDraftInput:
        if self.end_at <= self.start_at:
            raise ValueError("draft endAt must be after startAt")
        if self.start_at.minute % 30 or self.end_at.minute % 30:
            raise ValueError("draft times must use 30-minute slots")
        if self.start_at.second or self.end_at.second:
            raise ValueError("draft times must not contain seconds")
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("draft times must include an offset")
        shanghai_offset = timedelta(hours=8)
        if (
            self.start_at.utcoffset() != shanghai_offset
            or self.end_at.utcoffset() != shanghai_offset
        ):
            raise ValueError("draft times must use the +08:00 Asia/Shanghai offset")
        if set(self.required_participant_ids).intersection(self.optional_participant_ids):
            raise ValueError("an employee cannot be both required and optional")
        return self


class CreateBookingDraftResponse(AgentSchema):
    confirmation_token: str = Field(min_length=1, max_length=80)
    expires_at: datetime
    draft: BookingDraft


class RescheduleDraftInput(CreateBookingDraftInput):
    meeting_id: int = Field(ge=1, serialization_alias="meetingId")
    expected_version: int = Field(ge=0, serialization_alias="expectedVersion")


class CancellationPreviewInput(ToolInput):
    meeting_id: int = Field(ge=1, serialization_alias="meetingId")


class CreateRescheduleDraftResponse(AgentSchema):
    confirmation_token: str = Field(min_length=1, max_length=80)
    expires_at: datetime
    before: MeetingView
    after: BookingDraft


class CreateCancellationPreviewResponse(AgentSchema):
    confirmation_token: str = Field(min_length=1, max_length=80)
    expires_at: datetime
    meeting: MeetingView


class ConfirmBookingResponse(AgentSchema):
    status: Literal["SUCCESS", "PENDING"]
    meeting_id: int | None = Field(default=None, ge=1)
    request_no: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_confirmation_shape(self) -> ConfirmBookingResponse:
        if self.status == "SUCCESS":
            if self.meeting_id is None or self.request_no is not None:
                raise ValueError("SUCCESS confirmation requires meetingId and no requestNo")
        elif self.request_no is None or self.meeting_id is not None:
            raise ValueError("PENDING confirmation requires requestNo and no meetingId")
        return self


class JavaToolError(RuntimeError):
    def __init__(self, code: str, *, details: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ToolOutcome:
    tool_call_id: str
    tool_name: str
    risk_level: Literal["READ", "DRAFT", "WRITE"]
    data: dict[str, Any]
    summary: str
    duration_ms: int
    idempotency_key: str | None = None
    http_status: int | None = None


@dataclass
class JavaReadToolClient:
    """Java client with model-safe reads and explicitly-gated mutation methods."""

    settings: Settings
    http_client: httpx.Client | None = None

    def resolve_employees(
        self,
        *,
        context: AgentContext,
        names: list[str],
        department_names: list[str] | None = None,
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        payload = ResolveEmployeesInput(names=names, department_names=department_names or [])
        outcome = self._invoke(
            context=context,
            tool_name="resolve_employees",
            path="/internal/v1/tools/resolve-employees",
            payload=payload,
            risk_level="READ",
            tool_call_id=tool_call_id,
        )
        employee_count = len(outcome.data.get("employees", []))
        return _with_summary(outcome, f"已解析 {employee_count} 名员工")

    def get_employee_free_busy(
        self,
        *,
        context: AgentContext,
        employee_ids: list[int],
        from_: datetime,
        to: datetime,
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        outcome = self._invoke(
            context=context,
            tool_name="get_employee_free_busy",
            path="/internal/v1/tools/get-employee-free-busy",
            payload=FreeBusyInput(employee_ids=employee_ids, from_=from_, to=to),
            risk_level="READ",
            tool_call_id=tool_call_id,
        )
        return _with_summary(
            outcome,
            f"已查询 {len(outcome.data.get('employees', []))} 名员工的忙闲信息",
        )

    def search_available_rooms(
        self,
        *,
        context: AgentContext,
        from_: datetime,
        to: datetime,
        minimum_capacity: int,
        required_features: list[str],
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        outcome = self._invoke(
            context=context,
            tool_name="search_available_rooms",
            path="/internal/v1/tools/search-available-rooms",
            payload=SearchRoomsInput(
                from_=from_,
                to=to,
                minimum_capacity=minimum_capacity,
                required_features=required_features,
            ),
            risk_level="READ",
            tool_call_id=tool_call_id,
        )
        return _with_summary(outcome, f"已查询 {len(outcome.data.get('rooms', []))} 间可用会议室")

    def get_recent_meeting(
        self, *, context: AgentContext, limit: int = 5, tool_call_id: str | None = None
    ) -> ToolOutcome:
        outcome = self._invoke(
            context=context,
            tool_name="get_recent_meeting",
            path="/internal/v1/tools/get-recent-meeting",
            payload=RecentMeetingInput(limit=limit),
            risk_level="READ",
            tool_call_id=tool_call_id,
        )
        return _with_summary(outcome, f"已查询 {len(outcome.data.get('meetings', []))} 条最近会议")

    def create_booking_draft(
        self,
        *,
        context: AgentContext,
        payload: CreateBookingDraftInput,
        tool_call_id: str | None = None,
    ) -> tuple[ToolOutcome, CreateBookingDraftResponse]:
        """Create a non-reserving Java draft after deterministic solver validation.

        This method is intentionally not represented by ``SchedulingPlan``;
        callers must construct the validated payload themselves.
        """

        outcome = self._invoke(
            context=context,
            tool_name="create_booking_draft",
            path="/internal/v1/tools/booking-drafts",
            payload=payload,
            risk_level="DRAFT",
            tool_call_id=tool_call_id,
        )
        try:
            draft = CreateBookingDraftResponse.model_validate(outcome.data)
        except ValueError as exc:
            raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
        if outcome.http_status not in {None, 200}:
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        return _with_summary(outcome, "已创建待确认预约草案"), draft

    def confirm_booking(
        self,
        *,
        context: AgentContext,
        confirmation_token: str,
        tool_call_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[ToolOutcome, ConfirmBookingResponse]:
        """Confirm a draft only after a validated HITL ``ACCEPT`` transition."""

        if not confirmation_token or len(confirmation_token) > 80:
            raise JavaToolError("CONFIRMATION_TOKEN_INVALID")
        stable_tool_call_id = tool_call_id or stable_tool_identity(
            context.run_id, "confirm_booking", confirmation_token
        )
        stable_idempotency_key = idempotency_key or stable_idempotency_identity(
            context.run_id, "confirm_booking", confirmation_token
        )
        outcome = self._invoke(
            context=context,
            tool_name="confirm_booking",
            path=f"/internal/v1/tools/booking-drafts/{confirmation_token}/confirm",
            payload=ToolInput(),
            risk_level="WRITE",
            idempotency_key=stable_idempotency_key,
            tool_call_id=stable_tool_call_id,
        )
        try:
            confirmation = ConfirmBookingResponse.model_validate(outcome.data)
        except ValueError as exc:
            raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
        expected_status = 200 if confirmation.status == "SUCCESS" else 202
        if outcome.http_status not in {None, expected_status}:
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        summary = "预约确认已完成" if confirmation.status == "SUCCESS" else "预约请求已进入排队"
        return _with_summary(outcome, summary), confirmation

    def create_reschedule_draft(
        self,
        *,
        context: AgentContext,
        payload: RescheduleDraftInput,
        tool_call_id: str,
    ) -> tuple[ToolOutcome, CreateRescheduleDraftResponse]:
        outcome = self._invoke(
            context=context,
            tool_name="create_reschedule_draft",
            path="/internal/v1/tools/reschedule-drafts",
            payload=payload,
            risk_level="DRAFT",
            tool_call_id=tool_call_id,
        )
        try:
            response = CreateRescheduleDraftResponse.model_validate(outcome.data)
        except ValueError as exc:
            raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
        return _with_summary(outcome, "已创建待确认改期草案"), response

    def create_cancellation_preview(
        self,
        *,
        context: AgentContext,
        meeting_id: int,
        tool_call_id: str,
    ) -> tuple[ToolOutcome, CreateCancellationPreviewResponse]:
        outcome = self._invoke(
            context=context,
            tool_name="create_cancellation_preview",
            path="/internal/v1/tools/cancellation-previews",
            payload=CancellationPreviewInput(meeting_id=meeting_id),
            risk_level="DRAFT",
            tool_call_id=tool_call_id,
        )
        try:
            response = CreateCancellationPreviewResponse.model_validate(outcome.data)
        except ValueError as exc:
            raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
        return _with_summary(outcome, "已创建待确认取消预览"), response

    def confirm_reschedule(
        self,
        *,
        context: AgentContext,
        confirmation_token: str,
        tool_call_id: str,
        idempotency_key: str,
    ) -> tuple[ToolOutcome, ConfirmBookingResponse]:
        return self._confirm_mutation(
            context=context,
            confirmation_token=confirmation_token,
            operation="confirm_reschedule",
            path_prefix="/internal/v1/tools/reschedule-drafts",
            tool_call_id=tool_call_id,
            idempotency_key=idempotency_key,
        )

    def confirm_cancellation(
        self,
        *,
        context: AgentContext,
        confirmation_token: str,
        tool_call_id: str,
        idempotency_key: str,
    ) -> tuple[ToolOutcome, ConfirmBookingResponse]:
        return self._confirm_mutation(
            context=context,
            confirmation_token=confirmation_token,
            operation="confirm_cancellation",
            path_prefix="/internal/v1/tools/cancellation-previews",
            tool_call_id=tool_call_id,
            idempotency_key=idempotency_key,
        )

    def _confirm_mutation(
        self,
        *,
        context: AgentContext,
        confirmation_token: str,
        operation: str,
        path_prefix: str,
        tool_call_id: str,
        idempotency_key: str,
    ) -> tuple[ToolOutcome, ConfirmBookingResponse]:
        if not confirmation_token or len(confirmation_token) > 80:
            raise JavaToolError("CONFIRMATION_TOKEN_INVALID")
        outcome = self._invoke(
            context=context,
            tool_name=operation,
            path=f"{path_prefix}/{confirmation_token}/confirm",
            payload=ToolInput(),
            risk_level="WRITE",
            idempotency_key=idempotency_key,
            tool_call_id=tool_call_id,
        )
        try:
            response = ConfirmBookingResponse.model_validate(outcome.data)
        except ValueError as exc:
            raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
        if response.status != "SUCCESS":
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        summary = "改期确认已完成" if operation == "confirm_reschedule" else "取消确认已完成"
        return _with_summary(outcome, summary), response

    def _invoke(
        self,
        *,
        context: AgentContext,
        tool_name: str,
        path: str,
        payload: ToolInput,
        risk_level: Literal["READ", "DRAFT", "WRITE"],
        idempotency_key: str | None = None,
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        tool_call_id = tool_call_id or f"tool_{uuid.uuid4().hex}"
        started = time.perf_counter()
        headers = {
            "Authorization": f"Bearer {context.token}",
            "X-Service-Token": self.settings.internal_service_token.get_secret_value(),
            "X-Trace-Id": context.trace_id,
            "X-Run-Id": context.run_id,
            "X-Tool-Call-Id": tool_call_id,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        status_code, body = self._post_with_retry(
            path=path,
            headers=headers,
            payload=payload.model_dump(by_alias=True, mode="json"),
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        data = body.get("data")
        if not isinstance(data, dict):
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        return ToolOutcome(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            risk_level=risk_level,
            data=data,
            summary="Java Tool completed",
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            http_status=status_code,
        )

    def _post_with_retry(
        self, *, path: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        owned_client = self.http_client is None
        client = self.http_client or httpx.Client(timeout=self.settings.model_timeout_seconds)
        try:
            for attempt in range(self.settings.model_max_retries + 1):
                try:
                    response = client.post(
                        self.settings.business_service_url.rstrip("/") + path,
                        headers=headers,
                        json=payload,
                    )
                    if response.status_code == 503 and attempt < self.settings.model_max_retries:
                        time.sleep(0.1 * (2**attempt))
                        continue
                    if response.status_code in (401, 403):
                        raise JavaToolError("TOOL_FORBIDDEN")
                    if response.status_code == 409:
                        conflict_details = _conflict_details(response)
                        if conflict_details is not None:
                            raise JavaToolError("TOOL_CONFLICT", details=conflict_details)
                        raise JavaToolError("TOOL_REJECTED")
                    if response.status_code == 503:
                        raise JavaToolError("TOOL_UNAVAILABLE")
                    if response.status_code >= 400:
                        raise JavaToolError(_java_error_code(response) or "TOOL_REJECTED")
                    body = response.json()
                    if not isinstance(body, dict):
                        raise JavaToolError("TOOL_RESPONSE_INVALID")
                    return response.status_code, body
                except httpx.TimeoutException as exc:
                    if attempt < self.settings.model_max_retries:
                        time.sleep(0.1 * (2**attempt))
                        continue
                    raise JavaToolError("TOOL_TIMEOUT") from exc
                except httpx.RequestError as exc:
                    if attempt < self.settings.model_max_retries:
                        time.sleep(0.1 * (2**attempt))
                        continue
                    raise JavaToolError("TOOL_UNAVAILABLE") from exc
        finally:
            if owned_client:
                client.close()
        raise JavaToolError("TOOL_UNAVAILABLE")


def _java_error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    return code if isinstance(code, str) and 1 <= len(code) <= 64 else None


def _with_summary(outcome: ToolOutcome, summary: str) -> ToolOutcome:
    return ToolOutcome(
        tool_call_id=outcome.tool_call_id,
        tool_name=outcome.tool_name,
        risk_level=outcome.risk_level,
        data=outcome.data,
        summary=summary,
        duration_ms=outcome.duration_ms,
        idempotency_key=outcome.idempotency_key,
        http_status=outcome.http_status,
    )


def _conflict_details(response: httpx.Response) -> dict[str, str] | None:
    """Parse only the documented conflict evidence from a Java ApiError."""

    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("code") != "BOOKING_CONFLICT":
        return None
    raw_details = body.get("details")
    if not isinstance(raw_details, list):
        return {}
    allowed = {"conflict.type", "conflict.roomId", "conflict.slots"}
    parsed: dict[str, str] = {}
    for item in raw_details:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        reason = item.get("reason")
        if field in allowed and isinstance(reason, str) and len(reason) <= 256:
            parsed[field] = reason
    return parsed


def stable_tool_identity(run_id: str, operation: str, stable_input: str) -> str:
    """Derive a repeat-safe Java Tool identity without exposing sensitive input."""

    value = uuid.uuid5(uuid.NAMESPACE_URL, f"meeting-agent:{run_id}:{operation}:{stable_input}")
    return f"tool_{value.hex}"


def stable_idempotency_identity(run_id: str, operation: str, stable_input: str) -> str:
    value = uuid.uuid5(uuid.NAMESPACE_OID, f"meeting-agent:{run_id}:{operation}:{stable_input}")
    return f"idem_{value.hex}"
