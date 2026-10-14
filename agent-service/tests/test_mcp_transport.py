from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from app.agent_loop import READ_TOOL_DEFINITIONS
from app.config import Settings
from app.security import AgentContext
from app.tools import JavaReadToolClient, JavaToolError, mcp_transport
from app.tools.mcp_transport import LimitedTransport, McpToolTransport, parse_result


def settings(**kwargs: Any) -> Settings:
    return Settings(AGENT_DATABASE_URL="sqlite://", AGENT_TOOL_MAX_RETRIES=0, **kwargs)


def context() -> AgentContext:
    return AgentContext(1001, ("EMPLOYEE",), "trace_test", "run_test", "test-token")


def tool_result(envelope: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structuredContent=envelope,
        isError=error,
    )


def test_default_mcp_and_explicit_rest_and_configuration() -> None:
    assert isinstance(JavaReadToolClient(settings()).transport, McpToolTransport)
    assert settings().mcp_url == "http://business-service:8080/internal/v1/mcp"
    assert settings(BUSINESS_MCP_URL="http://localhost/custom").mcp_url == "http://localhost/custom"
    with pytest.raises(ValidationError):
        settings(AGENT_TOOL_TRANSPORT="unknown")
    with pytest.raises(ValidationError):
        settings(AGENT_TOOL_TIMEOUT_SECONDS=0)


def test_catalog_matches_python_and_model_white_list() -> None:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "catalog", root / "scripts/generate_mcp_catalog.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    saved = json.loads(
        (root / "business-service/src/main/resources/mcp/tool-catalog.json").read_text()
    )
    assert saved == module.build_catalog()
    assert len(saved) == 11
    assert {tool.name for tool in READ_TOOL_DEFINITIONS} == {
        "resolve_employees",
        "get_employee_free_busy",
        "search_available_rooms",
        "get_recent_meeting",
    }


def test_result_contract_and_conflict_evidence() -> None:
    assert parse_result(tool_result({"ok": True, "data": {"status": "PENDING"}})) == {
        "status": "PENDING",
    }
    result = tool_result(
        {
            "ok": False,
            "error": {
                "code": "BOOKING_CONFLICT",
                "message": "冲突",
                "details": [
                    {"field": "conflict.roomId", "reason": "101"},
                    {"field": "secret", "reason": "hidden"},
                ],
            },
        },
        error=True,
    )
    with pytest.raises(JavaToolError) as raised:
        parse_result(result)
    assert raised.value.code == "TOOL_CONFLICT"
    assert raised.value.details == {"conflict.roomId": "101"}
    with pytest.raises(JavaToolError, match="TOOL_RESPONSE_INVALID"):
        parse_result(tool_result({"ok": True, "data": {}, "error": {}}))
    result.content[0] = TextContent(type="text", text="unstructured error")
    with pytest.raises(JavaToolError, match="TOOL_RESPONSE_INVALID"):
        parse_result(result)


def test_official_sdk_headers_and_business_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["X-Run-Id"] == "run_test"
        assert request.headers["X-Tool-Call-Id"] == "tool_fixed"
        body = json.loads(request.content)
        method = body["method"]
        calls.append(method)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "resolve_employees",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        else:
            assert method == "tools/call"
            assert body["params"]["arguments"] == {"names": ["张三"], "departmentNames": []}
            result = tool_result(
                {"ok": True, "data": {"employees": [], "unresolvedNames": []}}
            ).model_dump()
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(
        mcp_transport,
        "LimitedTransport",
        lambda limit: LimitedTransport(
            limit,
            httpx.MockTransport(handler),
        ),
    )
    outcome = JavaReadToolClient(settings()).resolve_employees(
        context=context(),
        names=["张三"],
        tool_call_id="tool_fixed",
    )
    assert outcome.transport == "mcp" and outcome.http_status is None
    assert {"initialize", "notifications/initialized", "tools/call"}.issubset(calls)


def test_retries_keep_identity_and_total_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = McpToolTransport(settings().model_copy(update={"agent_tool_max_retries": 1}))
    calls = []

    async def once(name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        calls.append(dict(headers))
        if len(calls) == 1:
            raise JavaToolError("TOOL_UNAVAILABLE")
        return {}

    monkeypatch.setattr(transport, "_once", once)
    headers = {"X-Run-Id": "run", "X-Trace-Id": "trace", "X-Tool-Call-Id": "stable"}
    assert transport.call("confirm_booking", {}, headers).attempts == 2
    assert calls == [headers, headers]

    async def slow(name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        await anyio.sleep(1)
        return {}

    transport.settings = transport.settings.model_copy(update={"agent_tool_timeout_seconds": 0.01})
    monkeypatch.setattr(transport, "_once", slow)
    with pytest.raises(JavaToolError, match="TOOL_TIMEOUT"):
        transport.call("confirm_booking", {}, headers)


def test_sync_bridge_rejects_event_loop_thread() -> None:
    async def check() -> None:
        with pytest.raises(JavaToolError, match="TOOL_SYNC_CONTEXT_INVALID"):
            McpToolTransport(settings()).call("resolve_employees", {}, {})

    asyncio.run(check())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "TOOL_FORBIDDEN"),
        (403, "TOOL_FORBIDDEN"),
        (503, "TOOL_UNAVAILABLE"),
        (400, "TOOL_PROTOCOL_ERROR"),
    ],
)
def test_http_failure_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/mcp"
        calls.append(request)
        return httpx.Response(status)

    monkeypatch.setattr(
        mcp_transport,
        "LimitedTransport",
        lambda limit: LimitedTransport(
            limit,
            httpx.MockTransport(handler),
        ),
    )
    with pytest.raises(JavaToolError, match=code):
        JavaReadToolClient(settings()).resolve_employees(context=context(), names=[])
    assert len(calls) == 1


def test_bounded_read_stops_before_sdk_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(b"x" * 2048)
        )

    monkeypatch.setattr(
        mcp_transport,
        "LimitedTransport",
        lambda limit: LimitedTransport(
            limit,
            httpx.MockTransport(handler),
        ),
    )
    with pytest.raises(JavaToolError, match="TOOL_RESPONSE_INVALID"):
        JavaReadToolClient(settings(AGENT_TOOL_MAX_RESPONSE_BYTES=1024)).resolve_employees(
            context=context(),
            names=[],
        )
