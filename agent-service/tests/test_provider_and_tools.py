from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError

from app.agent_loop import (
    ReadToolGate,
    RouteEvaluator,
    SourceFidelityEvaluator,
    ToolGateError,
    tool_fingerprint,
)
from app.config import Settings
from app.providers.base import (
    ModelOutputError,
    ModelRequest,
    ModelToolCall,
    StructuredModelRunner,
    ToolDefinition,
    ToolLoopMessage,
    ToolModelRequest,
)
from app.providers.deepseek import DeepSeekModelProvider
from app.providers.fixture import FixtureModelProvider
from app.schemas.agent import (
    AgentState,
    BusinessResultCallback,
    Intent,
    MeetingRequest,
    Participant,
    RequirementDraft,
    Route,
    SupervisorDecision,
    TimeWindow,
)
from app.security import AgentContext
from app.tools.java import (
    ConfirmBookingResponse,
    CreateBookingDraftInput,
    JavaReadToolClient,
    JavaToolError,
)
from app.workflow import RequirementAgent, SupervisorAgent


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


def test_supervisor_normalizes_direct_scheduling_to_requirement_boundary() -> None:
    provider = QueueProvider(
        [
            '{"route":"SCHEDULING","summary":"The request needs scheduling."}',
            '{"route":"REQUIREMENT","summary":"The request needs requirement extraction."}',
        ]
    )
    state = AgentState(
        thread_id="thread_supervisor_guard",
        run_id="run_supervisor_guard",
        trace_id="trc_supervisor_guard",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="Book a meeting room tomorrow afternoon.",
        request_time=datetime.fromisoformat("2026-08-12T10:00:00+08:00"),
    )

    updated, _, calls = SupervisorAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert calls == 2
    assert updated.next_route is Route.REQUIREMENT
    assert updated.meeting_request is None


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
    assert result.content == '{"route":"REQUIREMENT","summary":"ok"}'
    request_body = json.loads(request_calls[-1].content)
    assert request_body["response_format"] == {"type": "json_object"}
    assert "Return only one JSON object" in request_body["messages"][0]["content"]
    assert '"route"' in request_body["messages"][0]["content"]


def test_deepseek_provider_uses_native_non_thinking_tool_call_round_trip() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_resolve_1",
                                        "type": "function",
                                        "function": {
                                            "name": "resolve_employees",
                                            "arguments": '{"names":["张三"]}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 21, "completion_tokens": 8},
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "事实齐备",
                            "tool_calls": [],
                        }
                    }
                ]
            },
        )

    tool = ToolDefinition(
        name="resolve_employees",
        description="Resolve names.",
        parameters={
            "type": "object",
            "properties": {"names": {"type": "array", "items": {"type": "string"}}},
            "required": ["names"],
            "additionalProperties": False,
        },
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekModelProvider(_settings(retries=0), client=client)
        first = provider.complete_tools(
            ToolModelRequest(
                agent_name="scheduling",
                messages=(ToolLoopMessage(role="user", content="安排会议"),),
                tools=(tool,),
                iteration=1,
            )
        )
        second = provider.complete_tools(
            ToolModelRequest(
                agent_name="scheduling",
                messages=(
                    ToolLoopMessage(role="user", content="安排会议"),
                    ToolLoopMessage(
                        role="assistant",
                        content=first.content,
                        tool_calls=first.tool_calls,
                    ),
                    ToolLoopMessage(
                        role="tool",
                        content='{"employees":[{"employeeId":1001}]}',
                        tool_call_id="call_resolve_1",
                    ),
                ),
                tools=(tool,),
                iteration=2,
            )
        )

    assert first.tool_calls == (
        ModelToolCall(
            id="call_resolve_1",
            name="resolve_employees",
            arguments='{"names":["张三"]}',
        ),
    )
    assert first.usage.input_tokens == 21
    assert first.usage.output_tokens == 8
    assert second.content == "事实齐备"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["tool_choice"] == "auto"
    function = requests[0]["tools"][0]["function"]  # type: ignore[index]
    assert "strict" not in function
    assert requests[1]["messages"][1]["tool_calls"][0]["id"] == "call_resolve_1"  # type: ignore[index]
    assert requests[1]["messages"][2] == {  # type: ignore[index]
        "role": "tool",
        "content": '{"employees":[{"employeeId":1001}]}',
        "tool_call_id": "call_resolve_1",
    }


def test_java_client_preserves_only_whitelisted_booking_conflict_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            request=request,
            json={
                "code": "BOOKING_CONFLICT",
                "details": [
                    {"field": "conflict.type", "reason": "BOOKING_CONFLICT"},
                    {"field": "conflict.roomId", "reason": "103"},
                    {"field": "conflict.slots", "reason": "30,31"},
                    {"field": "secret", "reason": "must-not-propagate"},
                ],
            },
        )

    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_conflict",
        run_id="run_conflict",
        token="signed-context-token",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = JavaReadToolClient(_settings(retries=0), http_client=client)
        with pytest.raises(JavaToolError) as raised:
            tools.confirm_booking(context=context, confirmation_token="cfm_conflict")

    assert raised.value.code == "TOOL_CONFLICT"
    assert raised.value.details == {
        "conflict.type": "BOOKING_CONFLICT",
        "conflict.roomId": "103",
        "conflict.slots": "30,31",
    }


def test_java_client_does_not_replan_an_unrelated_http_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            request=request,
            json={"code": "IDEMPOTENCY_CONFLICT", "details": []},
        )

    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_idempotency_conflict",
        run_id="run_idempotency_conflict",
        token="signed-context-token",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = JavaReadToolClient(_settings(retries=0), http_client=client)
        with pytest.raises(JavaToolError) as raised:
            tools.confirm_booking(context=context, confirmation_token="cfm_conflict")

    assert raised.value.code == "TOOL_REJECTED"
    assert raised.value.details == {}


def test_tool_fingerprint_is_canonical_and_changes_with_arguments() -> None:
    first = tool_fingerprint("resolve_employees", {"names": ["张三"], "departmentNames": []})
    reordered = tool_fingerprint(
        "resolve_employees", {"departmentNames": [], "names": ["张三"]}
    )
    changed = tool_fingerprint("resolve_employees", {"names": ["李四"], "departmentNames": []})

    assert first == reordered
    assert first != changed


def test_requirement_evaluator_optimizer_repairs_semantics_once() -> None:
    common = {
        "intent": "CREATE_MEETING",
        "title": "架构评审",
        "meetingType": "ARCHITECTURE_REVIEW",
        "durationMinutes": 60,
        "timeWindow": {
            "start": "2026-08-19T10:00:00+00:00",
            "end": "2026-08-19T12:00:00+00:00",
        },
        "requiredParticipantNames": [],
        "optionalGroups": [],
        "requiredFeatures": [],
        "minimumCapacity": 1,
        "preferredBuildings": [],
        "hardConstraints": [],
        "softConstraints": [],
        "targetMeetingId": None,
        "targetMeetingReference": None,
        "fieldEvidence": [],
        "needsPolicy": False,
        "summary": "首次提取",
    }
    first = common
    repaired = {
        **common,
        "timeWindow": {
            "start": "2026-08-19T10:00:00+08:00",
            "end": "2026-08-19T12:00:00+08:00",
        },
        "summary": "已按反馈修复",
    }
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": first,
                    "missingFields": [],
                }
            ),
            json.dumps(
                {
                    "requirementDraft": repaired,
                    "missingFields": [],
                }
            ),
        ]
    )
    state = AgentState(
        thread_id="thread_eval",
        run_id="run_eval",
        trace_id="trc_eval",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="安排会议",
        request_time=datetime.fromisoformat("2026-08-12T10:00:00+08:00"),
    )

    updated, _, calls = RequirementAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert calls == 2
    assert updated.meeting_request is not None
    assert updated.meeting_request.minimum_capacity == 1
    assert updated.requirement_feedback is None
    assert "EVALUATOR_FEEDBACK" in provider.requests[1].user_prompt
    assert "TIMEZONE_NOT_ASIA_SHANGHAI" in provider.requests[1].user_prompt


def test_source_fidelity_accepts_supported_canonical_evidence_values() -> None:
    draft = RequirementDraft.model_validate(
        {
            "intent": "CREATE_MEETING",
            "durationMinutes": 60,
            "timeWindow": {
                "start": "2026-08-20T15:00:00+08:00",
                "end": "2026-08-20T16:00:00+08:00",
            },
            "requiredParticipantNames": [],
            "requiredFeatures": ["WHITEBOARD"],
            "minimumCapacity": 6,
            "fieldEvidence": [
                {"field": "intent", "source": "CREATE_MEETING", "provenance": "USER_DERIVED"},
                {
                    "field": "requiredFeatures",
                    "source": "WHITEBOARD",
                    "provenance": "USER_EXPLICIT",
                },
                {"field": "minimumCapacity", "source": "6", "provenance": "USER_EXPLICIT"},
            ],
            "summary": "预约会议室",
        }
    )

    feedback = SourceFidelityEvaluator().evaluate(
        draft,
        "帮我预约2026年8月20日下午3点到4点的会议室，6个人，要白板，先给我候选。",
    )

    assert feedback is None


def test_source_fidelity_accepts_canonical_architecture_review_evidence() -> None:
    draft = RequirementDraft.model_validate(
        {
            "intent": "CREATE_MEETING",
            "title": "架构评审",
            "meetingType": "ARCHITECTURE_REVIEW",
            "durationMinutes": 60,
            "timeWindow": {
                "start": "2026-09-10T14:00:00+08:00",
                "end": "2026-09-10T15:00:00+08:00",
            },
            "requiredParticipantNames": ["张三", "李四"],
            "requiredFeatures": ["WHITEBOARD"],
            "fieldEvidence": [
                {"field": "intent", "source": "安排", "provenance": "USER_DERIVED"},
                {"field": "title", "source": "架构评审", "provenance": "USER_EXPLICIT"},
                {
                    "field": "meetingType",
                    "source": "ARCHITECTURE_REVIEW",
                    "provenance": "USER_DERIVED",
                },
                {
                    "field": "requiredParticipantNames",
                    "source": "张三和李四",
                    "provenance": "USER_EXPLICIT",
                },
                {"field": "requiredFeatures", "source": "WHITEBOARD", "provenance": "USER_DERIVED"},
                {"field": "timeWindow", "source": "14:00到15:00", "provenance": "USER_EXPLICIT"},
            ],
            "summary": "安排架构评审",
        }
    )

    feedback = SourceFidelityEvaluator().evaluate(
        draft,
        "请安排张三和李四在2026年9月10日14:00到15:00进行架构评审，需要白板，先给候选，不要直接创建。",
    )

    assert feedback is None


def test_route_evaluator_distinguishes_mutation_rules_from_mutation_request() -> None:
    evaluator = RouteEvaluator()

    assert evaluator.fallback("会议取消和改期有哪些规则？") == (
        Route.POLICY,
        Intent.QUERY_POLICY,
    )
    assert evaluator.fallback("取消会议 ID 9001，先给我预览。") == (
        Route.REQUIREMENT,
        Intent.CANCEL_MEETING,
    )


def test_requirement_preserves_recent_meeting_reference_after_model_omission() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "CANCEL_MEETING",
                        "requiredParticipantNames": [],
                        "requiredFeatures": [],
                        "fieldEvidence": [],
                        "summary": "取消最近会议",
                    },
                    "missingFields": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_recent_reference",
        run_id="run_recent_reference",
        trace_id="trc_recent_reference",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="取消我刚才那个会议，先给我预览。",
        request_time=datetime.fromisoformat("2026-08-13T01:00:00+08:00"),
    )

    updated, _, _ = RequirementAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert updated.meeting_request is not None
    assert updated.meeting_request.target_meeting_reference == "刚才那个会议"
    assert updated.missing_fields == []


def test_requirement_defaults_remove_stale_model_missing_fields() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "CREATE_MEETING",
                        "title": None,
                        "meetingType": None,
                        "durationMinutes": 60,
                        "timeWindow": {
                            "start": "2026-08-20T15:00:00+08:00",
                            "end": "2026-08-20T16:00:00+08:00",
                        },
                        "requiredParticipantNames": ["张三", "李四"],
                        "requiredFeatures": ["WHITEBOARD"],
                        "minimumCapacity": 3,
                        "fieldEvidence": [],
                        "summary": "安排架构评审",
                    },
                    "missingFields": ["title", "meetingType"],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_defaults",
        run_id="run_defaults",
        trace_id="trc_defaults",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="请安排张三和李四在2026年8月20日15:00到16:00开一小时架构评审，需要白板。",
        request_time=datetime.fromisoformat("2026-08-13T01:00:00+08:00"),
    )

    updated, _, _ = RequirementAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert updated.missing_fields == []
    assert updated.meeting_request is not None
    assert updated.meeting_request.title == "架构评审"
    assert updated.meeting_request.meeting_type == "ARCHITECTURE_REVIEW"


def test_read_tool_gate_rejects_duplicate_fingerprint_without_second_effect() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "employees": [{"employeeId": 1001, "displayName": "张三"}],
                    "unresolvedNames": [],
                }
            },
        )

    context = AgentContext(
        user_id=1001,
        roles=("EMPLOYEE",),
        trace_id="trc_gate",
        run_id="run_gate",
        token="signed-context-token",
    )
    request = MeetingRequest(
        intent=Intent.CREATE_MEETING,
        title="架构评审",
        meeting_type="ARCHITECTURE_REVIEW",
        duration_minutes=60,
        time_window=TimeWindow(
            start=datetime.fromisoformat("2026-08-19T10:00:00+08:00"),
            end=datetime.fromisoformat("2026-08-19T12:00:00+08:00"),
        ),
        required_participants=[Participant(name="张三")],
    )
    state = AgentState(
        thread_id="thread_gate",
        run_id="run_gate",
        trace_id="trc_gate",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="安排会议",
        request_time=datetime.fromisoformat("2026-08-12T10:00:00+08:00"),
        meeting_request=request,
        intent=Intent.CREATE_MEETING,
    )
    call = ModelToolCall(
        id="call_gate_1",
        name="resolve_employees",
        arguments='{"names":["张三"],"departmentNames":[]}',
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        gate = ReadToolGate(JavaReadToolClient(_settings(retries=0), http_client=client))
        first = gate.execute(
            call=call,
            state=state,
            context=context,
            resolved_employees=[],
            fingerprints=set(),
        )
        with pytest.raises(ToolGateError, match="DUPLICATE_TOOL_FINGERPRINT"):
            gate.execute(
                call=call,
                state=state,
                context=context,
                resolved_employees=[],
                fingerprints={first.fingerprint},
            )

    assert len(calls) == 1


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

    completion = provider.complete(request)
    assert completion.content is not None
    result = json.loads(completion.content)
    meeting = result["requirementDraft"]

    assert meeting["minimumCapacity"] == 10
    assert meeting["requiredFeatures"] == ["VIDEO_CONFERENCE"]


def test_fixture_tool_loop_computes_unique_organizer_capacity_after_resolution() -> None:
    provider = FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00"))
    system = ToolLoopMessage(
        role="system",
        content=(
            'CANONICAL_CONTEXT={"organizerId":1001,"participantNames":["张三","李四"],'
            '"from":"2026-08-19T10:00:00+08:00","to":"2026-08-19T12:00:00+08:00",'
            '"requestedMinimumCapacity":1,"requiredFeatures":[],"excludedCandidateIds":[]}'
        ),
    )
    user = ToolLoopMessage(role="user", content="帮张三和李四安排会议")
    first = provider.complete_tools(
        ToolModelRequest(
            agent_name="scheduling", messages=(system, user), tools=(), iteration=1
        )
    )
    second = provider.complete_tools(
        ToolModelRequest(
            agent_name="scheduling",
            messages=(
                system,
                user,
                ToolLoopMessage(
                    role="assistant", content=first.content, tool_calls=first.tool_calls
                ),
                ToolLoopMessage(
                    role="tool",
                    tool_call_id=first.tool_calls[0].id,
                    content=(
                        '{"toolName":"resolve_employees","data":{"employees":['
                        '{"employeeId":1001,"displayName":"张三"},'
                        '{"employeeId":1002,"displayName":"李四"}]}}'
                    ),
                ),
            ),
            tools=(),
            iteration=2,
        )
    )

    rooms = next(call for call in second.tool_calls if call.name == "search_available_rooms")
    assert json.loads(rooms.arguments)["minimumCapacity"] == 2


def test_fixture_tool_loop_allows_create_without_named_participants() -> None:
    provider = FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00"))
    system = ToolLoopMessage(
        role="system",
        content=(
            'CANONICAL_CONTEXT={"organizerId":1001,"intent":"CREATE_MEETING",'
            '"targetMeetingId":null,"participantNames":[],"from":'
            '"2026-08-19T10:00:00+08:00","to":"2026-08-19T12:00:00+08:00",'
            '"requestedMinimumCapacity":1,"requiredFeatures":[],"excludedCandidateIds":[]}'
        ),
    )
    response = provider.complete_tools(
        ToolModelRequest(
            agent_name="scheduling",
            messages=(system, ToolLoopMessage(role="user", content="明天下午安排会议")),
            tools=(),
            iteration=1,
        )
    )

    assert {call.name for call in response.tool_calls} == {
        "get_employee_free_busy",
        "search_available_rooms",
    }
    assert "resolve_employees" not in {call.name for call in response.tool_calls}


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
