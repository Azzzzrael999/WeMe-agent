"""Python -> real Java Servlet smoke, launched by the isolated JUnit host.

Credentials are test-only JWTs received on stdin; never accepted from model arguments.
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

os.environ.setdefault("AGENT_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "deterministic")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.api.internal import (
    get_checkpoint_saver,
    get_java_tools,
    get_model_provider,
    get_policy_retriever,
    get_repository,
)
from app.config import Settings, get_settings
from app.database.base import Base
from app.main import app
from app.persistence import MetadataRepository
from app.providers.fixture import FixtureModelProvider
from app.rag.policies import InMemoryPolicyRetriever
from app.security import AgentContext
from app.tools import JavaReadToolClient, JavaToolError
from app.tools.java import CreateBookingDraftInput, RescheduleDraftInput


def events(text: str) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if name and data:
            result.append((name, json.loads(data)))
    return result


def main() -> None:
    credentials = json.load(sys.stdin)
    base_url = sys.argv[1]
    settings = Settings(
        AGENT_DATABASE_URL="sqlite+pysqlite:///:memory:",
        BUSINESS_SERVICE_URL=base_url,
        INTERNAL_SERVICE_TOKEN="test-only-internal-service-token",
        AGENT_CONTEXT_JWT_SECRET="test-only-agent-context-secret-with-at-least-thirty-two-bytes",
        AGENT_TOOL_TRANSPORT="mcp",
        AGENT_TOOL_MAX_RETRIES=0,
        RAG_EMBEDDING_PROVIDER="deterministic",
    )
    context = AgentContext(
        1001, ("EMPLOYEE",), credentials["traceId"], credentials["runId"], credentials["token"]
    )
    other = AgentContext(
        1003,
        ("EMPLOYEE",),
        credentials["otherTraceId"],
        credentials["otherRunId"],
        credentials["otherToken"],
    )
    headers = {
        "Authorization": "Bearer " + context.token,
        "X-Service-Token": "test-only-internal-service-token",
        "X-Run-Id": context.run_id,
        "X-Trace-Id": context.trace_id,
        "X-Tool-Call-Id": "tool_discover",
    }

    async def discover() -> str:
        async with (
            httpx.AsyncClient(headers=headers) as client,
            streamable_http_client(settings.mcp_url, http_client=client) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            initialized = await session.initialize()
            assert len((await session.list_tools()).tools) == 11
            assert streams[2]() is None  # Stateless server never allocates reusable sessions.
            return initialized.protocolVersion

    protocol = anyio.run(discover)
    tools = JavaReadToolClient(settings)
    rest = JavaReadToolClient(settings.model_copy(update={"agent_tool_transport": "rest"}))
    day = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)).replace(
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
    )
    duration = []

    def measured(action: Any) -> Any:
        started = time.perf_counter()
        result = action()
        duration.append(int((time.perf_counter() - started) * 1000))
        return result

    employees = measured(
        lambda: tools.resolve_employees(
            context=context, names=["张三", "李四"], tool_call_id="tool_people"
        )
    )
    assert len(employees.data["employees"]) == 2
    assert (
        rest.resolve_employees(
            context=context, names=["张三", "李四"], tool_call_id="tool_people"
        ).data
        == employees.data
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            tools.resolve_participant_scope, context=context, tool_call_id="tool_scope"
        )
        second = pool.submit(
            tools.resolve_participant_scope, context=other, tool_call_id="tool_scope"
        )
        assert first.result().data["scopeName"] == second.result().data["scopeName"]
    free = measured(
        lambda: tools.get_employee_free_busy(
            context=context, employee_ids=[1001, 1003], from_=day, to=day + timedelta(hours=1)
        )
    )
    assert len(free.data["employees"]) == 2
    rooms = measured(
        lambda: tools.search_available_rooms(
            context=context,
            from_=day,
            to=day + timedelta(hours=1),
            minimum_capacity=2,
            required_features=[],
        )
    )
    assert rooms.data["rooms"]
    prior = rest.get_recent_meeting(context=context, tool_call_id="tool_recent")
    assert tools.get_recent_meeting(context=context, tool_call_id="tool_recent").data == prior.data
    payload = CreateBookingDraftInput(
        title="MCP booking",
        meeting_type="REVIEW",
        room_id=101,
        start_at=day,
        end_at=day + timedelta(hours=1),
        required_participant_ids=[1001, 1003],
    )
    draft_out, draft = measured(
        lambda: tools.create_booking_draft(
            context=context, payload=payload, tool_call_id="tool_draft"
        )
    )
    assert (
        rest.create_booking_draft(context=context, payload=payload, tool_call_id="tool_draft")[
            0
        ].data
        == draft_out.data
    )
    _, confirmation = tools.confirm_booking(
        context=context, confirmation_token=draft.confirmation_token
    )
    assert confirmation.status == "SUCCESS"
    assert (
        rest.confirm_booking(context=context, confirmation_token=draft.confirmation_token)[1]
        == confirmation
    )
    reschedule = RescheduleDraftInput(
        **payload.model_dump(),
        meeting_id=confirmation.meeting_id,
        expected_version=0,
    ).model_copy(
        update={
            "room_id": 102,
            "start_at": day + timedelta(hours=2),
            "end_at": day + timedelta(hours=3),
        }
    )
    _, changed = tools.create_reschedule_draft(
        context=context, payload=reschedule, tool_call_id="tool_reschedule_draft"
    )
    _, change_result = tools.confirm_reschedule(
        context=context,
        confirmation_token=changed.confirmation_token,
        tool_call_id="tool_reschedule_confirm",
        idempotency_key="idem_reschedule",
    )
    assert change_result.status == "SUCCESS"
    _, cancellation = tools.create_cancellation_preview(
        context=context, meeting_id=confirmation.meeting_id, tool_call_id="tool_cancel_draft"
    )
    _, cancel_result = tools.confirm_cancellation(
        context=context,
        confirmation_token=cancellation.confirmation_token,
        tool_call_id="tool_cancel_confirm",
        idempotency_key="idem_cancel",
    )
    assert cancel_result.status == "SUCCESS"
    hot_payload = payload.model_copy(update={"room_id": 103})
    _, hot = tools.create_booking_draft(
        context=context, payload=hot_payload, tool_call_id="tool_hot_draft"
    )
    hot_out, pending = tools.confirm_booking(
        context=context, confirmation_token=hot.confirmation_token
    )
    assert pending.status == "PENDING" and hot_out.http_status is None
    assert (
        rest.confirm_booking(context=context, confirmation_token=hot.confirmation_token)[1]
        == pending
    )
    # A denied caller cannot use another user's confirmation token.
    try:
        tools.confirm_booking(context=other, confirmation_token=hot.confirmation_token)
    except JavaToolError as error:
        assert error.code == "TOOL_FORBIDDEN"
    else:
        raise AssertionError("Draft ownership was bypassed")

    # Exercise the real synchronous StreamingResponse/LangGraph path through MCP to Java.
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    repository = MetadataRepository(engine)
    saver = InMemorySaver()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_model_provider] = lambda: FixtureModelProvider(
        datetime.now(ZoneInfo("Asia/Shanghai"))
    )
    app.dependency_overrides[get_policy_retriever] = lambda: InMemoryPolicyRetriever()
    app.dependency_overrides[get_java_tools] = lambda: tools
    app.dependency_overrides[get_checkpoint_saver] = lambda: saver
    try:
        with TestClient(app) as client:
            workflow_day = day + timedelta(days=1)
            text = f"请在{workflow_day.year}年{workflow_day.month}月{workflow_day.day}日13:00到17:00之间，为张三和李四安排60分钟项目讨论，2人，要大屏"
            response = client.post(
                "/internal/v1/agent-runs/stream",
                headers=headers,
                json={
                    "message": text,
                    "clientRequestId": "mcp_workflow",
                },
            )
            parsed = events(response.text)
            assert response.status_code == 200 and parsed[-1][0] == "hitl.required", response.text
            token = parsed[-1][1]["confirmationToken"]
            resumed = client.post(
                f"/internal/v1/agent-runs/{context.run_id}/resume",
                headers=headers,
                json={"action": "ACCEPT", "confirmationToken": token},
            )
            assert resumed.status_code == 200
            assert events(resumed.text)[-1][0] == "run.completed", resumed.text
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
    print(
        json.dumps(
            {
                "result": "MCP_BRIDGE_OK",
                "protocol": protocol,
                "tools": 11,
                "durations_ms": duration,
                "workflow": "HITL_ACCEPT_COMPLETED",
            }
        )
    )


if __name__ == "__main__":
    main()
