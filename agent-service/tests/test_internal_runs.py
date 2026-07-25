from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.api.internal import (
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
from app.tools.java import ToolOutcome


class FakeJavaReadTools:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_employees(self, *, context: object, names: list[str]) -> ToolOutcome:
        del context
        self.calls += 1
        employees = [
            {"employeeId": 1001, "displayName": "张三"},
            {"employeeId": 1002, "displayName": "李四"},
        ]
        return ToolOutcome(
            tool_call_id="tool_fixture_resolve",
            tool_name="resolve_employees",
            risk_level="READ",
            data={"employees": employees[: len(names)], "unresolvedNames": []},
            summary=f"已解析 {len(names)} 名员工",
            duration_ms=4,
        )


@pytest.fixture
def metadata_repository() -> Iterator[MetadataRepository]:
    engine: Engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield MetadataRepository(engine)
    finally:
        engine.dispose()


@pytest.fixture
def fixture_tools() -> FakeJavaReadTools:
    return FakeJavaReadTools()


@pytest.fixture
def configured_app(
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaReadTools,
) -> Iterator[None]:
    settings = Settings()
    app.dependency_overrides[get_repository] = lambda: metadata_repository
    app.dependency_overrides[get_model_provider] = lambda: FixtureModelProvider(
        datetime.fromisoformat(settings.fixture_now)
    )
    app.dependency_overrides[get_policy_retriever] = lambda: InMemoryPolicyRetriever()
    app.dependency_overrides[get_java_tools] = lambda: fixture_tools
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def _headers(
    *,
    run_id: str,
    trace_id: str,
    user_id: int = 1001,
    roles: list[str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": str(user_id),
            "roles": roles or ["EMPLOYEE"],
            "traceId": trace_id,
            "runId": run_id,
            "aud": settings.agent_context_audience,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.agent_context_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Service-Token": settings.internal_service_token.get_secret_value(),
        "X-Trace-Id": trace_id,
        "X-Run-Id": run_id,
    }


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        event_line, data_line = block.split("\n")
        event_name = event_line.removeprefix("event: ")
        payload = json.loads(data_line.removeprefix("data: "))
        events.append((event_name, payload))
    return events


def test_fixture_normal_stream_persists_metadata_and_only_uses_read_tool(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaReadTools,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/agent-runs/stream",
            headers=_headers(run_id=run_id, trace_id=trace_id),
            json={
                "threadId": None,
                "message": "下周三下午帮张三和李四安排一个90分钟架构评审，要大屏",
                "clientRequestId": "fixture-request-1",
            },
        )

    assert response.status_code == 200
    events = _events(response.text)
    assert events[0][0] == "run.started"
    assert events[-1] == (
        "run.completed",
        {
            "runId": run_id,
            "status": "SUCCEEDED",
            "answerSummary": "已完成结构化解析和只读查询",
            "citations": [],
        },
    )
    step_agents = [event[1]["agentName"] for event in events if event[0] == "agent.step"]
    assert {"supervisor", "requirement", "scheduling"}.issubset(step_agents)
    tool_events = [event[1] for event in events if event[0] == "tool.call"]
    assert tool_events == [
        {
            "runId": run_id,
            "toolCallId": "tool_fixture_resolve",
            "toolName": "resolve_employees",
            "riskLevel": "READ",
            "status": "SUCCEEDED",
            "summary": "已解析 2 名员工",
            "durationMs": 4,
        }
    ]
    assert fixture_tools.calls == 1

    trace = metadata_repository.get_trace(run_id)
    assert trace is not None
    assert trace["run"]["status"] == "SUCCEEDED"  # type: ignore[index]
    question = trace["run"]["questionSummary"]  # type: ignore[index]
    assert question == "用户提交架构评审、时长、设备任务（正文长度=27）"
    assert "张三" not in question
    assert "李四" not in question
    assert [step["agentName"] for step in trace["steps"]][:3] == [  # type: ignore[index]
        "supervisor",
        "requirement",
        "scheduling",
    ]
    assert trace["toolCalls"] == [  # type: ignore[index]
        {
            "toolCallId": "tool_fixture_resolve",
            "toolName": "resolve_employees",
            "riskLevel": "READ",
            "sanitizedArgs": {"nameCount": 2},
            "resultSummary": "已解析 2 名员工",
            "status": "SUCCEEDED",
            "durationMs": 4,
            "createdAt": trace["toolCalls"][0]["createdAt"],  # type: ignore[index]
        }
    ]


def test_policy_stream_uses_citation_from_retrieval_result(configured_app: None) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/agent-runs/stream",
            headers=_headers(run_id=run_id, trace_id=trace_id),
            json={
                "threadId": None,
                "message": "VIP会议室有什么使用规则？",
                "clientRequestId": "fixture-policy-1",
            },
        )

    assert response.status_code == 200
    events = _events(response.text)
    steps = [event[1]["agentName"] for event in events if event[0] == "agent.step"]
    assert "supervisor" in steps
    assert "policy" in steps
    completed = events[-1][1]
    assert completed["citations"] == [
        {
            "chunkId": "chunk_vip_room_v1",
            "title": "VIP会议室使用规则",
            "headingPath": ["VIP会议室", "预约规则"],
            "page": 1,
        }
    ]


def test_internal_auth_and_run_ownership_are_enforced(
    configured_app: None,
    metadata_repository: MetadataRepository,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        invalid = client.post(
            "/internal/v1/agent-runs/stream",
            json={"message": "安排会议", "clientRequestId": "missing-auth"},
        )
        assert invalid.status_code == 401

        stream = client.post(
            "/internal/v1/agent-runs/stream",
            headers=_headers(run_id=run_id, trace_id=trace_id),
            json={
                "message": "下周三下午帮张三和李四安排一个90分钟架构评审，要大屏",
                "clientRequestId": "ownership-stream",
            },
        )
        assert stream.status_code == 200
        forbidden = client.get(
            f"/internal/v1/agent-runs/{run_id}/trace",
            headers=_headers(run_id=run_id, trace_id="trc_other", user_id=1002),
        )

    assert forbidden.status_code == 404
    assert metadata_repository.get_trace(run_id) is not None


def test_step_limit_emits_single_failed_terminal_event(
    configured_app: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MAX_GRAPH_NODES", "1")
    get_settings.cache_clear()
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/internal/v1/agent-runs/stream",
                headers=_headers(run_id=run_id, trace_id=trace_id),
                json={
                    "message": "下周三下午帮张三和李四安排一个90分钟架构评审，要大屏",
                    "clientRequestId": "step-limit",
                },
            )
    finally:
        get_settings.cache_clear()

    events = _events(response.text)
    assert events[0][0] == "run.started"
    assert [event[0] for event in events].count("run.failed") == 1
    assert [event[0] for event in events].count("run.completed") == 0
    assert events[-1][1]["errorCode"] == "AGENT_STEP_LIMIT_EXCEEDED"
