from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

import app.api.internal as internal
from app.api.internal import (
    get_checkpoint_saver,
    get_java_tools,
    get_model_provider,
    get_policy_retriever,
    get_repository,
)
from app.checkpoints import RedisCheckpointSaver
from app.config import Settings, get_settings
from app.database.base import Base
from app.main import app
from app.persistence import MetadataRepository
from app.providers.fixture import FixtureModelProvider
from app.rag.policies import InMemoryPolicyRetriever
from app.schemas.agent import AgentState, BookingDraft, DraftParticipant, RunStatus
from app.tools.java import (
    ConfirmBookingResponse,
    CreateBookingDraftInput,
    CreateBookingDraftResponse,
    JavaToolError,
    ToolOutcome,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expirations: dict[str, int] = {}

    def get(self, name: str) -> bytes | None:
        return self.values.get(name)

    def set(self, name: str, value: str, ex: int) -> bool:
        self.values[name] = value.encode("utf-8")
        self.expirations[name] = ex
        return True

    def delete(self, *names: str) -> int:
        for name in names:
            self.values.pop(name, None)
            self.expirations.pop(name, None)
        return len(names)

    def ping(self) -> bool:
        return True

    def scan_iter(self, match: str) -> Iterator[bytes]:
        prefix = match.removesuffix("*")
        for name in self.values:
            if name.startswith(prefix):
                yield name.encode("utf-8")


ROOM_103 = {
    "roomId": 103,
    "roomName": "研发楼403",
    "building": "研发楼",
    "capacity": 12,
    "roomType": "STANDARD",
    "features": ["LARGE_SCREEN", "VIDEO_CONFERENCE"],
}
ROOM_102 = {
    "roomId": 102,
    "roomName": "研发楼402",
    "building": "研发楼",
    "capacity": 16,
    "roomType": "STANDARD",
    "features": ["LARGE_SCREEN", "VIDEO_CONFERENCE"],
}


@dataclass
class FakeJavaTools:
    draft_failures_remaining: int = 0
    confirm_results: list[str] = field(default_factory=lambda: ["SUCCESS"])
    confirm_conflict_details: dict[str, str] = field(default_factory=dict)
    room_sequences: list[list[dict[str, object]]] = field(
        default_factory=lambda: [[ROOM_103, ROOM_102]]
    )
    calls: list[str] = field(default_factory=list)
    draft_payloads: list[CreateBookingDraftInput] = field(default_factory=list)
    confirm_calls: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    _draft_count: int = 0

    def resolve_employees(
        self,
        *,
        context: object,
        names: list[str],
        department_names: list[str] | None = None,
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        del context, department_names
        self.calls.append("resolve_employees")
        employees = [
            {"employeeId": 1001, "displayName": "张三"},
            {"employeeId": 1002, "displayName": "李四"},
        ]
        return _outcome(
            "resolve_employees",
            "READ",
            {"employees": employees[: len(names)], "unresolvedNames": []},
            tool_call_id=tool_call_id,
        )

    def get_employee_free_busy(
        self,
        *,
        context: object,
        employee_ids: list[int],
        from_: datetime,
        to: datetime,
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        del context, from_, to
        self.calls.append("get_employee_free_busy")
        return _outcome(
            "get_employee_free_busy",
            "READ",
            {"employees": [{"employeeId": value, "busySlots": []} for value in employee_ids]},
            tool_call_id=tool_call_id,
        )

    def search_available_rooms(
        self,
        *,
        context: object,
        from_: datetime,
        to: datetime,
        minimum_capacity: int,
        required_features: list[str],
        tool_call_id: str | None = None,
    ) -> ToolOutcome:
        del context, from_, to, minimum_capacity, required_features
        self.calls.append("search_available_rooms")
        index = min(self.calls.count("search_available_rooms") - 1, len(self.room_sequences) - 1)
        return _outcome(
            "search_available_rooms",
            "READ",
            {"rooms": self.room_sequences[index]},
            tool_call_id=tool_call_id,
        )

    def create_booking_draft(
        self,
        *,
        context: object,
        payload: CreateBookingDraftInput,
        tool_call_id: str | None = None,
    ) -> tuple[ToolOutcome, CreateBookingDraftResponse]:
        del context
        self.calls.append("create_booking_draft")
        if self.draft_failures_remaining:
            self.draft_failures_remaining -= 1
            raise JavaToolError("TOOL_UNAVAILABLE")
        self._draft_count += 1
        self.draft_payloads.append(payload)
        response = CreateBookingDraftResponse(
            confirmation_token=f"cfm_fixture_{self._draft_count}",
            expires_at=payload.start_at - timedelta(hours=1),
            draft=BookingDraft(
                title=payload.title,
                room_id=payload.room_id,
                room_name=f"会议室{payload.room_id}",
                start_at=payload.start_at,
                end_at=payload.end_at,
                required_participants=[
                    DraftParticipant(employee_id=value, display_name=f"员工{value}")
                    for value in payload.required_participant_ids
                ],
                optional_participants=[],
            ),
        )
        return (
            ToolOutcome(
                tool_call_id=tool_call_id or f"tool_draft_{self._draft_count}",
                tool_name="create_booking_draft",
                risk_level="DRAFT",
                data={},
                summary="已创建待确认预约草案",
                duration_ms=1,
            ),
            response,
        )

    def confirm_booking(
        self,
        *,
        context: object,
        confirmation_token: str,
        tool_call_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[ToolOutcome, ConfirmBookingResponse]:
        del context
        self.calls.append("confirm_booking")
        self.confirm_calls.append((confirmation_token, tool_call_id, idempotency_key))
        result = self.confirm_results.pop(0) if self.confirm_results else "SUCCESS"
        if result == "CONFLICT":
            raise JavaToolError("TOOL_CONFLICT", details=self.confirm_conflict_details)
        response = (
            ConfirmBookingResponse(status="SUCCESS", meeting_id=9001)
            if result == "SUCCESS"
            else ConfirmBookingResponse(status="PENDING", request_no="BR_FIXTURE_001")
        )
        return (
            ToolOutcome(
                tool_call_id=tool_call_id or "tool_confirm_fixture",
                tool_name="confirm_booking",
                risk_level="WRITE",
                data={},
                summary="预约确认已完成" if result == "SUCCESS" else "预约请求已进入排队",
                duration_ms=1,
                idempotency_key=idempotency_key,
            ),
            response,
        )


def _outcome(
    tool_name: str,
    risk_level: str,
    data: dict[str, object],
    *,
    tool_call_id: str | None = None,
) -> ToolOutcome:
    return ToolOutcome(
        tool_call_id=tool_call_id or f"tool_fixture_{tool_name}_{uuid.uuid4().hex}",
        tool_name=tool_name,
        risk_level=risk_level,  # type: ignore[arg-type]
        data=data,
        summary=f"{tool_name} completed",
        duration_ms=1,
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
def fixture_tools() -> FakeJavaTools:
    return FakeJavaTools()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def configured_app(
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
    fake_redis: FakeRedis,
) -> Iterator[None]:
    settings = Settings()
    app.dependency_overrides[get_repository] = lambda: metadata_repository
    app.dependency_overrides[get_model_provider] = lambda: FixtureModelProvider(
        datetime.fromisoformat(settings.fixture_now)
    )
    app.dependency_overrides[get_policy_retriever] = lambda: InMemoryPolicyRetriever()
    app.dependency_overrides[get_java_tools] = lambda: fixture_tools
    # A fresh saver per request proves state is recovered from the same Redis
    # data rather than retained by a process-local checkpointer instance.
    app.dependency_overrides[get_checkpoint_saver] = lambda: RedisCheckpointSaver(client=fake_redis)
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
        events.append(
            (
                event_line.removeprefix("event: "),
                json.loads(data_line.removeprefix("data: ")),
            )
        )
    return events


def _start(client: TestClient, run_id: str, trace_id: str) -> list[tuple[str, dict[str, object]]]:
    response = client.post(
        "/internal/v1/agent-runs/stream",
        headers=_headers(run_id=run_id, trace_id=trace_id),
        json={
            "threadId": None,
            "message": "下周三下午帮张三和李四安排一个90分钟架构评审，10人，要大屏",
            "clientRequestId": "fixture-request",
        },
    )
    assert response.status_code == 200
    return _events(response.text)


def _hitl_token(events: list[tuple[str, dict[str, object]]]) -> str:
    event = next(payload for name, payload in events if name == "hitl.required")
    token = event["confirmationToken"]
    assert isinstance(token, str)
    return token


def test_initial_hitl_persists_candidates_without_leaking_token_to_trace(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        events = _start(client, run_id, trace_id)

    assert events[0][0] == "run.started"
    assert events[-1][0] == "hitl.required"
    candidates = next(
        payload["candidates"] for name, payload in events if name == "plan.candidates"
    )
    assert 1 <= len(candidates) <= 3
    assert [candidate["totalCost"] for candidate in candidates] == sorted(
        candidate["totalCost"] for candidate in candidates
    )
    assert candidates[0]["roomId"] == 103
    assert fixture_tools.calls == [
        "resolve_employees",
        "get_employee_free_busy",
        "search_available_rooms",
        "create_booking_draft",
    ]
    trace = metadata_repository.get_trace(run_id)
    assert trace is not None
    assert trace["run"]["status"] == "WAITING_CONFIRMATION"  # type: ignore[index]
    trace_json = json.dumps(trace, ensure_ascii=False)
    assert "cfm_fixture" not in trace_json
    assert "confirmationToken" not in trace_json
    assert trace["toolCalls"][-1]["riskLevel"] == "DRAFT"  # type: ignore[index]


def test_trajectory_integration_exposes_bounded_native_tool_loop(
    configured_app: None,
) -> None:
    """Run the real LangGraph and assert the observable Plan/Verify trajectory."""

    # The configured fixture implements the same complete_tools protocol as DeepSeek.
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        events = _start(client, run_id, trace_id)

    loops = [payload for name, payload in events if name == "agent.loop"]
    assert [payload["phase"] for payload in loops] == ["PLAN", "VERIFY"]
    assert loops[0]["iteration"] == 1
    assert loops[-1]["iteration"] <= 4
    assert loops[-1]["stopReason"] == "READY_FOR_CONFIRMATION"
    assert loops[-1]["remainingBudget"]["modelCalls"] >= 0  # type: ignore[index]
    assert loops[-1]["remainingBudget"]["toolCalls"] >= 0  # type: ignore[index]


def test_tool_trace_replay_is_idempotent_but_rejects_semantic_reuse(
    metadata_repository: MetadataRepository,
) -> None:
    metadata_repository.create_run(
        run_id="run_tool_trace",
        thread_id="thread_tool_trace",
        trace_id="trc_tool_trace",
        user_id=1001,
        question_summary="安排会议",
    )
    record = {
        "tool_call_id": "tool_trace_stable",
        "run_id": "run_tool_trace",
        "tool_name": "resolve_employees",
        "risk_level": "READ",
        "sanitized_args": {"nameCount": 1},
        "result_summary": "已解析 1 名员工",
        "status": "SUCCEEDED",
        "duration_ms": 3,
    }

    metadata_repository.record_tool_call(**record)
    metadata_repository.record_tool_call(**{**record, "duration_ms": 9})

    trace = metadata_repository.get_trace("run_tool_trace")
    assert trace is not None
    assert len(trace["toolCalls"]) == 1  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="different audit semantics"):
        metadata_repository.record_tool_call(
            **{**record, "sanitized_args": {"nameCount": 2}}
        )


def test_stream_executes_graph_on_one_dedicated_producer_thread(
    configured_app: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE frame delivery must not resume a live LangGraph iterator on AnyIO workers."""

    class ThreadBoundWorkflow:
        def __init__(self) -> None:
            self.latest_state: AgentState | None = None
            self.execution_threads: list[tuple[int, str]] = []

        def stream(self, state: AgentState) -> Iterator[tuple[str, dict[str, object]]]:
            self.execution_threads.append((threading.get_ident(), threading.current_thread().name))
            yield "test.progress", {"runId": state.run_id, "sequence": 1}
            self.execution_threads.append((threading.get_ident(), threading.current_thread().name))
            self.latest_state = state.model_copy(update={"status": RunStatus.WAITING_CONFIRMATION})
            yield "test.progress", {"runId": state.run_id, "sequence": 2}
            self.execution_threads.append((threading.get_ident(), threading.current_thread().name))

    workflow = ThreadBoundWorkflow()
    monkeypatch.setattr(internal, "_workflow", lambda **_: workflow)
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/agent-runs/stream",
            headers=_headers(run_id=run_id, trace_id=trace_id),
            json={"message": "线程亲和回归", "clientRequestId": "thread-affinity"},
        )

    assert response.status_code == 200
    assert [name for name, _ in _events(response.text)] == [
        "run.started",
        "test.progress",
        "test.progress",
    ]
    assert len(workflow.execution_threads) == 3
    assert {thread_id for thread_id, _ in workflow.execution_threads} == {
        workflow.execution_threads[0][0]
    }
    assert {thread_name for _, thread_name in workflow.execution_threads} == {"agent-sse-producer"}


def test_run_recovery_view_only_exposes_confirmation_for_current_waiting_checkpoint(
    configured_app: None,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        recovery = client.get(
            f"/internal/v1/agent-runs/{run_id}",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
        )
        trace = client.get(
            f"/internal/v1/agent-runs/{run_id}/trace",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
        )

    assert recovery.status_code == 200
    assert recovery.headers["cache-control"] == "no-store"
    assert recovery.json()["confirmationToken"] == token
    assert recovery.json()["candidates"]
    assert "confirmationToken" not in json.dumps(trace.json(), ensure_ascii=False)
    assert token not in json.dumps(trace.json(), ensure_ascii=False)


def test_accept_uses_fresh_trace_but_keeps_initial_trace_and_completes(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    initial_trace = f"trc_{uuid.uuid4().hex}"
    resumed_trace = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, initial_trace))
        response = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=resumed_trace),
            json={"action": "ACCEPT", "confirmationToken": token, "feedback": None},
        )

    assert response.status_code == 200
    events = _events(response.text)
    assert events[0] == ("run.resumed", {"runId": run_id, "status": "RUNNING"})
    assert any(name == "booking.completed" for name, _ in events)
    assert events[-1][0] == "run.completed"
    assert fixture_tools.calls.count("confirm_booking") == 1
    trace = metadata_repository.get_trace(run_id)
    assert trace is not None
    assert trace["run"]["traceId"] == initial_trace  # type: ignore[index]
    assert trace["run"]["status"] == "SUCCEEDED"  # type: ignore[index]
    write = trace["toolCalls"][-1]  # type: ignore[index]
    assert write["riskLevel"] == "WRITE"
    assert "cfm_fixture" not in json.dumps(write, ensure_ascii=False)


def test_synchronous_conflict_replans_before_returning_to_hitl(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    fixture_tools.confirm_results = ["CONFLICT"]
    fixture_tools.confirm_conflict_details = {
        "conflict.type": "BOOKING_CONFLICT",
        "conflict.roomId": "103",
        "conflict.slots": "30,31,32",
    }
    fixture_tools.room_sequences = [[ROOM_103, ROOM_102], [ROOM_102]]
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        response = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "ACCEPT", "confirmationToken": token},
        )

    events = _events(response.text)
    assert response.status_code == 200
    assert any(
        name == "agent.step" and payload["nodeName"] == "conflict_repair"
        for name, payload in events
    )
    assert events[-1][0] == "hitl.required"
    assert fixture_tools.calls.count("confirm_booking") == 1
    assert fixture_tools.calls.count("get_employee_free_busy") == 2
    assert fixture_tools.calls.count("search_available_rooms") == 2
    assert fixture_tools.draft_payloads[-1].room_id == 102
    trace = metadata_repository.get_trace(run_id)
    assert trace is not None
    assert trace["run"]["status"] == "WAITING_CONFIRMATION"  # type: ignore[index]


def test_edit_revalidates_and_creates_new_draft_without_direct_write(
    configured_app: None,
    fixture_tools: FakeJavaTools,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        response = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={
                "action": "EDIT",
                "confirmationToken": token,
                "editedDraft": {"roomId": 102},
                "feedback": None,
            },
        )

    events = _events(response.text)
    assert response.status_code == 200
    assert events[0][0] == "run.resumed"
    assert events[-1][0] == "hitl.required"
    assert fixture_tools.calls.count("confirm_booking") == 0
    assert fixture_tools.calls.count("get_employee_free_busy") == 2
    assert fixture_tools.draft_payloads[-1].room_id == 102


def test_reject_ends_without_write_tool(configured_app: None, fixture_tools: FakeJavaTools) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        response = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "REJECT", "confirmationToken": token, "feedback": None},
        )

    assert response.status_code == 200
    assert _events(response.text)[-1][1]["status"] == "CANCELLED"
    assert "confirm_booking" not in fixture_tools.calls


def test_pending_then_success_callback_is_idempotent(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    fixture_tools.confirm_results = ["PENDING"]
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        resumed = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "ACCEPT", "confirmationToken": token},
        )
        payload = {
            "eventId": "evt_success_1",
            "requestNo": "BR_FIXTURE_001",
            "status": "SUCCESS",
            "meetingId": 9001,
        }
        callback = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )
        duplicate = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )

    assert _events(resumed.text)[-1][0] == "booking.pending"
    assert callback.json() == {"status": "PROCESSED", "reason": "SUCCESS", "candidateCount": 0}
    assert duplicate.json() == {"status": "IGNORED", "reason": "DUPLICATE", "candidateCount": 0}
    trace = metadata_repository.get_trace(run_id)
    assert trace is not None
    assert trace["run"]["status"] == "SUCCEEDED"  # type: ignore[index]
    assert any(step["nodeName"] == "business_result" for step in trace["steps"])  # type: ignore[index]


def test_early_hot_callback_is_retried_until_pending_state_is_durable(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    """A result can arrive immediately after Java returns the PENDING confirm response."""

    fixture_tools.confirm_results = ["PENDING"]
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    payload = {
        "eventId": "evt_early_hot_callback",
        "requestNo": "BR_FIXTURE_001",
        "status": "SUCCESS",
        "meetingId": 9001,
    }
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        early = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )
        assert early.status_code == 503
        assert early.json()["detail"] == "AGENT_CALLBACK_RETRY"
        assert not metadata_repository.has_business_event("evt_early_hot_callback")
        resumed = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "ACCEPT", "confirmationToken": token},
        )
        processed = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )

    assert _events(resumed.text)[-1][0] == "booking.pending"
    assert processed.json() == {"status": "PROCESSED", "reason": "SUCCESS", "candidateCount": 0}
    assert metadata_repository.get_trace(run_id)["run"]["status"] == "SUCCEEDED"  # type: ignore[index]


def test_conflict_replans_with_fresh_read_tools_and_is_idempotent(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    fixture_tools.confirm_results = ["PENDING"]
    fixture_tools.room_sequences = [[ROOM_103, ROOM_102], [ROOM_102]]
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    payload = {
        "eventId": "evt_conflict_1",
        "requestNo": "BR_FIXTURE_001",
        "status": "CONFLICT",
        "conflict": {"type": "ROOM_CONFLICT", "roomId": 103, "slots": [1, 2]},
    }
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "ACCEPT", "confirmationToken": token},
        )
        callback = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )
        duplicate = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )

    assert callback.status_code == 200
    assert callback.json()["status"] == "REPLANNED"
    assert callback.json()["candidateCount"] >= 1
    assert duplicate.json()["reason"] == "DUPLICATE"
    assert fixture_tools.calls.count("get_employee_free_busy") == 2
    assert fixture_tools.calls.count("search_available_rooms") == 2
    assert fixture_tools.draft_payloads[-1].room_id == 102
    trace = metadata_repository.get_trace(run_id)
    assert trace is not None
    assert trace["run"]["status"] == "WAITING_CONFIRMATION"  # type: ignore[index]
    assert any(step["nodeName"] == "business_result" for step in trace["steps"])  # type: ignore[index]
    assert "cfm_fixture" not in json.dumps(trace, ensure_ascii=False)


def test_failed_conflict_replan_does_not_consume_event_and_retries(
    configured_app: None,
    metadata_repository: MetadataRepository,
    fixture_tools: FakeJavaTools,
) -> None:
    fixture_tools.confirm_results = ["PENDING"]
    fixture_tools.room_sequences = [[ROOM_103, ROOM_102], [ROOM_102]]
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    payload = {
        "eventId": "evt_conflict_retry",
        "requestNo": "BR_FIXTURE_001",
        "status": "CONFLICT",
        "conflict": {"type": "ROOM_CONFLICT", "roomId": 103, "slots": [1]},
    }
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "ACCEPT", "confirmationToken": token},
        )
        fixture_tools.draft_failures_remaining = 1
        failed = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )
        recovered = client.post(
            f"/internal/v1/agent-runs/{run_id}/business-result",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}"),
            json=payload,
        )

    assert failed.status_code == 503
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "REPLANNED"
    assert metadata_repository.get_trace(run_id)["run"]["status"] == "WAITING_CONFIRMATION"  # type: ignore[index]


def test_resume_requires_current_owner_and_matching_context_run(
    configured_app: None,
) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        token = _hitl_token(_start(client, run_id, trace_id))
        forbidden = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id=run_id, trace_id=f"trc_{uuid.uuid4().hex}", user_id=1002),
            json={"action": "REJECT", "confirmationToken": token},
        )
        mismatched = client.post(
            f"/internal/v1/agent-runs/{run_id}/resume",
            headers=_headers(run_id="run_other", trace_id=f"trc_{uuid.uuid4().hex}"),
            json={"action": "REJECT", "confirmationToken": token},
        )

    assert forbidden.status_code == 404
    assert mismatched.status_code == 401


def test_policy_path_keeps_verified_citation_regression(configured_app: None) -> None:
    run_id = f"run_{uuid.uuid4().hex}"
    trace_id = f"trc_{uuid.uuid4().hex}"
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/agent-runs/stream",
            headers=_headers(run_id=run_id, trace_id=trace_id),
            json={
                "message": "VIP会议室有什么使用规则？",
                "clientRequestId": "fixture-policy",
            },
        )

    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1][0] == "run.completed"
    citations = events[-1][1]["citations"]
    assert citations == [
        {
            "chunkId": "chunk_vip_room_v1",
            "title": "VIP会议室使用规则",
            "headingPath": ["VIP会议室", "预约规则"],
            "page": 1,
        }
    ]


def test_stream_rejects_missing_agent_context(configured_app: None) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/agent-runs/stream",
            json={"message": "安排会议", "clientRequestId": "missing-auth"},
        )

    assert response.status_code == 401


def test_graph_limit_emits_one_failed_terminal_event(
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
                    "message": "下周三下午帮张三和李四安排一个90分钟架构评审，10人，要大屏",
                    "clientRequestId": "step-limit",
                },
            )
    finally:
        get_settings.cache_clear()

    events = _events(response.text)
    assert events[0][0] == "run.started"
    assert [name for name, _ in events].count("run.failed") == 1
    assert [name for name, _ in events].count("run.completed") == 0
    assert events[-1][1]["errorCode"] == "BUDGET_EXHAUSTED"
