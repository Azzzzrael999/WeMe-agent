from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.providers.base import (
    ModelOutputError,
    ModelRequest,
    StructuredModelRunner,
)
from app.providers.deepseek import DeepSeekModelProvider
from app.providers.fixture import FixtureModelProvider
from app.schemas.agent import BusinessResultCallback, SupervisorDecision
from app.security import AgentContext
from app.tools.java import (
    ConfirmBookingResponse,
    CreateBookingDraftInput,
    JavaReadToolClient,
    JavaToolError,
)


class QueueProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return self._responses.pop(0)


def _settings(*, retries: int = 1) -> Settings:
    return Settings(
        AGENT_DATABASE_URL="sqlite+pysqlite:///:memory:",
        DEEPSEEK_API_KEY="test-deepseek-key",
        DEEPSEEK_BASE_URL="https://api.example.invalid",
        DEEPSEEK_MODEL="test-model",
        MODEL_MAX_RETRIES=retries,
        INTERNAL_SERVICE_TOKEN="test-service-token",
        AGENT_CONTEXT_JWT_SECRET="test-agent-context-secret-with-at-least-32-bytes",
    )


def _model_request() -> ModelRequest:
    return ModelRequest(
        agent_name="supervisor",
        system_prompt="Return a route.",
        user_prompt="安排会议",
        schema_name="SupervisorDecision",
        schema=SupervisorDecision.model_json_schema(by_alias=True),
    )


def test_structured_model_runner_repairs_invalid_output_once() -> None:
    provider = QueueProvider(
        [
            '{"route":"NOT_A_ROUTE","summary":"bad"}',
            '{"route":"REQUIREMENT","summary":"已路由到需求解析。"}',
        ]
    )

    result = StructuredModelRunner().invoke(
        provider=provider,
        request=_model_request(),
        output_type=SupervisorDecision,
    )

    assert result.route.value == "REQUIREMENT"
    assert len(provider.requests) == 2
    assert provider.requests[0].repair_attempt == 0
    assert provider.requests[1].repair_attempt == 1


def test_structured_model_runner_stops_after_one_repair() -> None:
    provider = QueueProvider(["{}", "{}"])

    with pytest.raises(ModelOutputError):
        StructuredModelRunner().invoke(
            provider=provider,
            request=_model_request(),
            output_type=SupervisorDecision,
        )

    assert len(provider.requests) == 2


def test_deepseek_provider_retries_temporary_failure_without_real_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_calls.append(request)
        if len(request_calls) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"route":"REQUIREMENT","summary":"ok"}'}}]},
        )

    monkeypatch.setattr("app.providers.deepseek.time.sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = DeepSeekModelProvider(_settings(), client=client).complete(_model_request())

    assert len(request_calls) == 2
    assert result == '{"route":"REQUIREMENT","summary":"ok"}'
    request_body = json.loads(request_calls[-1].content)
    assert request_body["response_format"] == {"type": "json_object"}
    assert "Return only one JSON object" in request_body["messages"][0]["content"]
    assert '"route"' in request_body["messages"][0]["content"]


def test_java_read_tool_client_retries_503_and_rejects_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "employees": [
                        {"employeeId": 1001, "displayName": "张三"},
                        {"employeeId": 1002, "displayName": "李四"},
                    ],
                    "unresolvedNames": [],
                }
            },
        )

    monkeypatch.setattr("app.tools.java.time.sleep", lambda _: None)
    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_tool_test",
        run_id="run_tool_test",
        token="signed-context-token",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = JavaReadToolClient(_settings(), http_client=client)
        outcome = tools.resolve_employees(context=context, names=["张三", "李四"])
        with pytest.raises(ValidationError):
            tools.resolve_employees(context=context, names=["员工"] * 51)

    assert len(calls) == 2
    assert outcome.tool_name == "resolve_employees"
    assert outcome.risk_level == "READ"
    assert outcome.summary == "已解析 2 名员工"
    assert calls[-1].headers["X-Service-Token"] == "test-service-token"
    assert calls[-1].headers["X-Trace-Id"] == "trc_tool_test"
    assert calls[-1].headers["X-Run-Id"] == "run_tool_test"


def test_java_read_tool_client_maps_auth_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_tool_auth",
        run_id="run_tool_auth",
        token="signed-context-token",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = JavaReadToolClient(_settings(retries=0), http_client=client)
        with pytest.raises(JavaToolError, match="TOOL_FORBIDDEN") as error:
            tools.resolve_employees(context=context, names=["张三"])

    assert error.value.code == "TOOL_FORBIDDEN"


def test_java_confirmation_identity_is_stable_for_replay() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"data": {"status": "SUCCESS", "meetingId": 9001, "requestNo": None}},
        )

    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_replay",
        run_id="run_replay",
        token="signed-context-token",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = JavaReadToolClient(_settings(retries=0), http_client=client)
        first, first_result = tools.confirm_booking(
            context=context, confirmation_token="cfm_replay_test"
        )
        second, second_result = tools.confirm_booking(
            context=context, confirmation_token="cfm_replay_test"
        )

    assert first_result.status == second_result.status == "SUCCESS"
    assert first.tool_call_id == second.tool_call_id
    assert first.idempotency_key == second.idempotency_key
    assert calls[0].headers["X-Tool-Call-Id"] == calls[1].headers["X-Tool-Call-Id"]
    assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]
    assert "/cfm_replay_test/confirm" in str(calls[0].url)


def test_create_draft_uses_caller_supplied_stable_tool_identity() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "confirmationToken": "cfm_draft",
                    "expiresAt": "2026-08-12T10:10:00+08:00",
                    "draft": {
                        "title": "架构评审",
                        "roomId": 103,
                        "roomName": "研发楼403",
                        "startAt": "2026-08-19T15:00:00+08:00",
                        "endAt": "2026-08-19T16:30:00+08:00",
                        "requiredParticipants": [],
                        "optionalParticipants": [],
                        "createVideoConference": False,
                    },
                }
            },
        )

    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_draft_replay",
        run_id="run_draft_replay",
        token="signed-context-token",
    )
    payload = CreateBookingDraftInput(
        title="架构评审",
        meeting_type="ARCHITECTURE_REVIEW",
        room_id=103,
        start_at=datetime.fromisoformat("2026-08-19T15:00:00+08:00"),
        end_at=datetime.fromisoformat("2026-08-19T16:30:00+08:00"),
        required_participant_ids=[1001],
        optional_participant_ids=[],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = JavaReadToolClient(_settings(retries=0), http_client=client)
        first, _ = tools.create_booking_draft(
            context=context, payload=payload, tool_call_id="tool_stable_draft"
        )
        second, _ = tools.create_booking_draft(
            context=context, payload=payload, tool_call_id="tool_stable_draft"
        )

    assert first.tool_call_id == second.tool_call_id == "tool_stable_draft"
    assert calls[0].headers["X-Tool-Call-Id"] == calls[1].headers["X-Tool-Call-Id"]


@pytest.mark.parametrize(
    ("start_at", "end_at"),
    [
        ("2026-08-19T15:00:00+00:00", "2026-08-19T16:30:00+00:00"),
        ("2026-08-19T15:00:00+09:00", "2026-08-19T16:30:00+09:00"),
        ("2026-08-19T15:00:00+08:00", "2026-08-19T16:30:00+00:00"),
    ],
)
def test_create_draft_requires_asia_shanghai_offset(
    start_at: str, end_at: str
) -> None:
    with pytest.raises(ValidationError, match=r"\+08:00"):
        CreateBookingDraftInput(
            title="架构评审",
            meeting_type="ARCHITECTURE_REVIEW",
            room_id=103,
            start_at=datetime.fromisoformat(start_at),
            end_at=datetime.fromisoformat(end_at),
            required_participant_ids=[1001],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "SUCCESS", "requestNo": "BR1"},
        {"status": "SUCCESS", "meetingId": 9001, "requestNo": "BR1"},
        {"status": "PENDING", "meetingId": 9001},
        {"status": "PENDING", "meetingId": 9001, "requestNo": "BR1"},
    ],
)
def test_confirmation_response_requires_exclusive_success_or_pending_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ConfirmBookingResponse.model_validate(payload)

    assert ConfirmBookingResponse(status="SUCCESS", meeting_id=9001).meeting_id == 9001
    assert ConfirmBookingResponse(status="PENDING", request_no="BR1").request_no == "BR1"


def test_fixture_requirement_extracts_video_feature_and_explicit_capacity() -> None:
    provider = FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00"))
    request = ModelRequest(
        agent_name="requirement",
        system_prompt="fixture",
        user_prompt="下周三下午帮张三安排一个90分钟架构评审，10人，要视频会议设备",
        schema_name="RequirementExtraction",
        schema={},
    )

    result = json.loads(provider.complete(request))
    meeting = result["meetingRequest"]

    assert meeting["minimumCapacity"] == 10
    assert meeting["requiredFeatures"] == ["VIDEO_CONFERENCE"]
    assert meeting["createVideoConference"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"eventId": "evt", "requestNo": "BR1", "status": "SUCCESS"},
        {
            "eventId": "evt",
            "requestNo": "BR1",
            "status": "SUCCESS",
            "meetingId": 9001,
            "conflict": {"type": "ROOM_CONFLICT"},
        },
        {"eventId": "evt", "requestNo": "BR1", "status": "CONFLICT"},
        {
            "eventId": "evt",
            "requestNo": "BR1",
            "status": "CONFLICT",
            "meetingId": 9001,
            "conflict": {"type": "ROOM_CONFLICT"},
        },
        {"eventId": "evt", "requestNo": "BR1", "status": "FAILED"},
    ],
)
def test_business_result_callback_rejects_invalid_or_unsupported_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        BusinessResultCallback.model_validate(payload)
