"""Python client for the allow-listed Java READ Tool API only.

There are intentionally no draft or confirmation methods in this module.
Day 5 adds those bounded write transitions after HITL exists.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.security import AgentContext


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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


class JavaToolError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ToolOutcome:
    tool_call_id: str
    tool_name: str
    risk_level: str
    data: dict[str, Any]
    summary: str
    duration_ms: int


@dataclass
class JavaReadToolClient:
    settings: Settings
    http_client: httpx.Client | None = None

    def resolve_employees(
        self, *, context: AgentContext, names: list[str], department_names: list[str] | None = None
    ) -> ToolOutcome:
        payload = ResolveEmployeesInput(
            names=names,
            department_names=department_names or [],
        )
        outcome = self._invoke(
            context=context,
            tool_name="resolve_employees",
            path="/internal/v1/tools/resolve-employees",
            payload=payload,
        )
        employee_count = len(outcome.data.get("employees", []))
        return ToolOutcome(**{**outcome.__dict__, "summary": f"已解析 {employee_count} 名员工"})

    def get_employee_free_busy(
        self,
        *,
        context: AgentContext,
        employee_ids: list[int],
        from_: datetime,
        to: datetime,
    ) -> ToolOutcome:
        payload = FreeBusyInput(employee_ids=employee_ids, from_=from_, to=to)
        outcome = self._invoke(
            context=context,
            tool_name="get_employee_free_busy",
            path="/internal/v1/tools/get-employee-free-busy",
            payload=payload,
        )
        return ToolOutcome(
            **{
                **outcome.__dict__,
                "summary": f"已查询 {len(outcome.data.get('employees', []))} 名员工的忙闲信息",
            }
        )

    def search_available_rooms(
        self,
        *,
        context: AgentContext,
        from_: datetime,
        to: datetime,
        minimum_capacity: int,
        required_features: list[str],
    ) -> ToolOutcome:
        payload = SearchRoomsInput(
            from_=from_,
            to=to,
            minimum_capacity=minimum_capacity,
            required_features=required_features,
        )
        outcome = self._invoke(
            context=context,
            tool_name="search_available_rooms",
            path="/internal/v1/tools/search-available-rooms",
            payload=payload,
        )
        return ToolOutcome(
            **{
                **outcome.__dict__,
                "summary": f"已查询 {len(outcome.data.get('rooms', []))} 间可用会议室",
            }
        )

    def get_recent_meeting(self, *, context: AgentContext, limit: int = 5) -> ToolOutcome:
        payload = RecentMeetingInput(limit=limit)
        outcome = self._invoke(
            context=context,
            tool_name="get_recent_meeting",
            path="/internal/v1/tools/get-recent-meeting",
            payload=payload,
        )
        return ToolOutcome(
            **{
                **outcome.__dict__,
                "summary": f"已查询 {len(outcome.data.get('meetings', []))} 条最近会议",
            }
        )

    def _invoke(
        self,
        *,
        context: AgentContext,
        tool_name: str,
        path: str,
        payload: ToolInput,
    ) -> ToolOutcome:
        tool_call_id = f"tool_{uuid.uuid4().hex}"
        started = time.perf_counter()
        headers = {
            "Authorization": f"Bearer {context.token}",
            "X-Service-Token": self.settings.internal_service_token.get_secret_value(),
            "X-Trace-Id": context.trace_id,
            "X-Run-Id": context.run_id,
            "X-Tool-Call-Id": tool_call_id,
        }
        response = self._post_with_retry(
            path=path,
            headers=headers,
            payload=payload.model_dump(by_alias=True, mode="json"),
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        data = response.get("data")
        if not isinstance(data, dict):
            raise JavaToolError("TOOL_RESPONSE_INVALID")
        return ToolOutcome(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            risk_level="READ",
            data=data,
            summary="Java READ tool completed",
            duration_ms=duration_ms,
        )

    def _post_with_retry(
        self, *, path: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
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
                        raise JavaToolError("TOOL_CONFLICT")
                    if response.status_code == 503:
                        raise JavaToolError("TOOL_UNAVAILABLE")
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise JavaToolError("TOOL_RESPONSE_INVALID")
                    return body
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
