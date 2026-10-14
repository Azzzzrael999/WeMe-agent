"""Explicit legacy REST transport. Never selected by an MCP failure."""

import time
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.tools.transport import JavaToolError, TransportResult, business_error

ROUTES = {
    "resolve_employees": "resolve-employees",
    "resolve_participant_scope": "resolve-participant-scope",
    "get_employee_free_busy": "get-employee-free-busy",
    "search_available_rooms": "search-available-rooms",
    "get_recent_meeting": "get-recent-meeting",
    "create_booking_draft": "booking-drafts",
    "create_reschedule_draft": "reschedule-drafts",
    "create_cancellation_preview": "cancellation-previews",
    "confirm_booking": "booking-drafts",
    "confirm_reschedule": "reschedule-drafts",
    "confirm_cancellation": "cancellation-previews",
}


class RestToolTransport:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client

    def call(
        self, name: str, arguments: dict[str, Any], headers: dict[str, str]
    ) -> TransportResult:
        path = "/internal/v1/tools/" + ROUTES[name]
        if name.startswith("confirm_"):
            path += "/" + quote(arguments["confirmationToken"], safe="") + "/confirm"
            arguments = {}
        deadline = time.monotonic() + self.settings.agent_tool_timeout_seconds
        owned = self.client is None
        client = self.client or httpx.Client()
        try:
            for attempt in range(self.settings.agent_tool_max_retries + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JavaToolError("TOOL_TIMEOUT")
                try:
                    with client.stream(
                        "POST",
                        self.settings.business_service_url.rstrip("/") + path,
                        headers=headers,
                        json=arguments,
                        timeout=remaining,
                    ) as response:
                        chunks = bytearray()
                        for chunk in response.iter_bytes():
                            if time.monotonic() >= deadline:
                                raise JavaToolError("TOOL_TIMEOUT")
                            if (
                                len(chunks) + len(chunk)
                                > self.settings.agent_tool_max_response_bytes
                            ):
                                raise JavaToolError("TOOL_RESPONSE_INVALID")
                            chunks.extend(chunk)
                        body = httpx.Response(response.status_code, content=bytes(chunks))
                        try:
                            parsed = body.json()
                        except ValueError as exc:
                            if response.status_code >= 400:
                                parsed = {}
                            else:
                                raise JavaToolError("TOOL_RESPONSE_INVALID") from exc
                        if not isinstance(parsed, dict):
                            raise JavaToolError("TOOL_RESPONSE_INVALID")
                        if response.status_code >= 400:
                            raise business_error(parsed, response.status_code)
                        data = parsed.get("data")
                        if not isinstance(data, dict):
                            raise JavaToolError("TOOL_RESPONSE_INVALID")
                        return TransportResult(data, response.status_code, attempt + 1)
                except httpx.TimeoutException as exc:
                    error = JavaToolError("TOOL_TIMEOUT")
                    error.__cause__ = exc
                except httpx.RequestError as exc:
                    error = JavaToolError("TOOL_UNAVAILABLE")
                    error.__cause__ = exc
                except JavaToolError as exc:
                    error = exc
                if error.code not in {"TOOL_TIMEOUT", "TOOL_UNAVAILABLE"}:
                    raise error
                if attempt == self.settings.agent_tool_max_retries:
                    raise error
                time.sleep(min(0.1 * 2**attempt, max(0, deadline - time.monotonic())))
        finally:
            if owned:
                client.close()
        raise JavaToolError("TOOL_UNAVAILABLE")
