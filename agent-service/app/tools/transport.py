"""Business commands independent of HTTP routes and MCP session identities."""

from dataclasses import dataclass
from typing import Any, Protocol


class JavaToolError(RuntimeError):
    def __init__(self, code: str, *, details: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class TransportResult:
    data: dict[str, Any]
    http_status: int | None = None
    attempts: int = 1


class ToolTransport(Protocol):
    def call(
        self, name: str, arguments: dict[str, Any], headers: dict[str, str]
    ) -> TransportResult: ...


def business_error(body: dict[str, Any], status: int | None = None) -> JavaToolError:
    code = body.get("code")
    if status in {401, 403} or code in {
        "AUTH_REQUIRED",
        "SERVICE_TOKEN_INVALID",
        "AGENT_CONTEXT_INVALID",
        "FORBIDDEN",
        "TOOL_NOT_ALLOWED",
    }:
        return JavaToolError("TOOL_FORBIDDEN")
    if code == "BOOKING_CONFLICT":
        details = body.get("details", [])
        allowed = {"conflict.type", "conflict.roomId", "conflict.slots"}
        parsed = (
            {
                item["field"]: item["reason"]
                for item in details
                if isinstance(details, list)
                and isinstance(item, dict)
                and item.get("field") in allowed
                and isinstance(item.get("reason"), str)
                and len(item["reason"]) <= 256
            }
            if isinstance(details, list)
            else {}
        )
        return JavaToolError("TOOL_CONFLICT", details=parsed)
    if status == 503 or code in {"DEPENDENCY_UNAVAILABLE", "AGENT_UNAVAILABLE"}:
        return JavaToolError("TOOL_UNAVAILABLE")
    conflict_codes = {
        "ROOM_CODE_CONFLICT",
        "ROOM_STATE_CONFLICT",
        "EMPLOYEE_USERNAME_CONFLICT",
        "EMPLOYEE_EMAIL_CONFLICT",
        "EMPLOYEE_STATE_CONFLICT",
        "REPLAN_CASE_STATE_CONFLICT",
        "REPLAN_CANDIDATE_STALE",
        "IDEMPOTENCY_KEY_REUSED",
        "MEETING_STATE_CONFLICT",
        "MEETING_CONTENT_STATE_CONFLICT",
        "POST_MEETING_DRAFT_STATE_CONFLICT",
        "ACTION_ITEM_STATE_CONFLICT",
        "DRAFT_EXPIRED",
        "DRAFT_ALREADY_USED",
        "AGENT_RUN_STATE_CONFLICT",
        "RAG_DOCUMENT_CONFLICT",
    }
    if status == 409 or code in conflict_codes:
        return JavaToolError("TOOL_REJECTED")
    return JavaToolError(
        code if isinstance(code, str) and 1 <= len(code) <= 64 else "TOOL_REJECTED"
    )
