from __future__ import annotations

import json

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
from app.schemas.agent import SupervisorDecision
from app.security import AgentContext
from app.tools.java import JavaReadToolClient, JavaToolError


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
            json={
                "choices": [
                    {"message": {"content": '{"route":"REQUIREMENT","summary":"ok"}'}}
                ]
            },
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
