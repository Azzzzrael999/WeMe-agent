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
from app.rag.policies import InMemoryPolicyRetriever
from app.schemas.agent import (
    AgentState,
    BusinessResultCallback,
    ClarificationResponse,
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
from app.workflow import PolicyAgent, RequirementAgent, SupervisorAgent


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

    updated, _, calls = SupervisorAgent(provider=provider, runner=StructuredModelRunner()).execute(
        state
    )

    assert calls == 2
    assert updated.next_route is Route.REQUIREMENT
    assert updated.meeting_request is None


def test_supervisor_fills_missing_intent_from_high_confidence_source_anchor() -> None:
    provider = QueueProvider(
        ['{"route":"REQUIREMENT","summary":"Read-only availability lookup."}']
    )
    state = AgentState(
        thread_id="thread_supervisor_intent_fallback",
        run_id="run_supervisor_intent_fallback",
        trace_id="trc_supervisor_intent_fallback",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="张三和李四下周三上午能否找到120分钟共同空闲？",
        request_time=datetime.fromisoformat("2026-08-15T10:00:00+08:00"),
    )

    updated, _, calls = SupervisorAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert calls == 1
    assert updated.next_route is Route.REQUIREMENT
    assert updated.intent is Intent.FIND_COMMON_TIME


def test_policy_agent_answers_from_openable_evidence_content() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "answerSummary": "架构评审展示材料时应选择配备大屏的会议室。",
                    "selectedChunkIds": ["chunk_architecture_review_v1"],
                    "confidence": 0.95,
                    "constraints": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_policy_evidence",
        run_id="run_policy_evidence",
        trace_id="trc_policy_evidence",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="架构评审的会议室设备规则是什么？",
        request_time=datetime.fromisoformat("2026-08-15T10:00:00+08:00"),
        intent=Intent.QUERY_POLICY,
    )

    updated, summary, calls = PolicyAgent(
        provider=provider,
        runner=StructuredModelRunner(),
        retriever=InMemoryPolicyRetriever(),
    ).execute(state)

    assert calls == 1
    assert summary == "架构评审展示材料时应选择配备大屏的会议室。"
    assert "架构评审展示材料时应选择配备大屏的会议室" in provider.requests[0].user_prompt
    assert [item.chunk_id for item in updated.citations] == ["chunk_architecture_review_v1"]


def test_policy_agent_returns_explicit_unverified_result_when_no_chunk_is_selected() -> None:
    empty_selection = json.dumps(
        {
            "answerSummary": "周五禁止开会。",
            "selectedChunkIds": [],
            "confidence": 0.8,
            "constraints": [],
        },
        ensure_ascii=False,
    )
    provider = QueueProvider(
        [
            empty_selection,
            empty_selection,
        ]
    )
    state = AgentState(
        thread_id="thread_policy_unknown",
        run_id="run_policy_unknown",
        trace_id="trc_policy_unknown",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="公司是否禁止周五开会？",
        request_time=datetime.fromisoformat("2026-08-15T10:00:00+08:00"),
        intent=Intent.QUERY_POLICY,
    )

    updated, summary, calls = PolicyAgent(
        provider=provider,
        runner=StructuredModelRunner(),
        retriever=InMemoryPolicyRetriever(),
    ).execute(state)

    assert calls == 2
    assert summary == "未找到可验证的会议制度证据。"
    assert updated.policy_result is not None
    assert updated.policy_result.verification_status == "UNVERIFIED"
    assert updated.policy_result.confidence == 0.0
    assert updated.citations == []


def test_policy_agent_rechecks_an_empty_selection_before_returning_unverified() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "answerSummary": "未找到依据。",
                    "selectedChunkIds": [],
                    "confidence": 0.0,
                    "constraints": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "answerSummary": "VIP会议室使用前需要管理员审批。",
                    "selectedChunkIds": ["chunk_vip_room_v1"],
                    "confidence": 0.95,
                    "constraints": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    state = AgentState(
        thread_id="thread_policy_retry",
        run_id="run_policy_retry",
        trace_id="trc_policy_retry",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="VIP会议室预约前有哪些审批规则？",
        request_time=datetime.fromisoformat("2026-08-15T10:00:00+08:00"),
        intent=Intent.QUERY_POLICY,
    )

    updated, summary, calls = PolicyAgent(
        provider=provider,
        runner=StructuredModelRunner(),
        retriever=InMemoryPolicyRetriever(),
    ).execute(state)

    assert calls == 2
    assert summary == "VIP会议室使用前需要管理员审批。"
    assert [item.chunk_id for item in updated.citations] == ["chunk_vip_room_v1"]


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
    reordered = tool_fingerprint("resolve_employees", {"departmentNames": [], "names": ["张三"]})
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

    updated, _, calls, _ = RequirementAgent(
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


def test_source_fidelity_treats_between_range_as_search_window() -> None:
    source = (
        "请在2026年8月25日13:00到18:00之间，为张三和李四安排一场60分钟架构评审，"
        "需要白板和大屏。先给出最多3个候选方案，不要替我确认。"
    )
    draft = RequirementDraft.model_validate(
        {
            "intent": "CREATE_MEETING",
            "title": "架构评审",
            "meetingType": "ARCHITECTURE_REVIEW",
            "durationMinutes": 60,
            "timeWindow": {
                "start": "2026-08-25T13:00:00+08:00",
                "end": "2026-08-25T18:00:00+08:00",
            },
            "requiredParticipantNames": ["张三", "李四"],
            "requiredFeatures": ["WHITEBOARD", "LARGE_SCREEN"],
            "minimumCapacity": 3,
            "fieldEvidence": [
                {"field": "intent", "source": "安排", "provenance": "USER_DERIVED"},
                {"field": "title", "source": "架构评审", "provenance": "USER_EXPLICIT"},
                {
                    "field": "meetingType",
                    "source": "ARCHITECTURE_REVIEW",
                    "provenance": "USER_DERIVED",
                },
                {
                    "field": "durationMinutes",
                    "source": "60分钟",
                    "provenance": "USER_EXPLICIT",
                },
                {
                    "field": "timeWindow",
                    "source": "13:00到18:00之间",
                    "provenance": "USER_EXPLICIT",
                },
                {
                    "field": "requiredParticipantNames",
                    "source": "张三和李四",
                    "provenance": "USER_EXPLICIT",
                },
                {
                    "field": "requiredFeatures",
                    "source": "WHITEBOARD,LARGE_SCREEN",
                    "provenance": "USER_DERIVED",
                },
                {
                    "field": "minimumCapacity",
                    "source": "3",
                    "provenance": "USER_DERIVED",
                },
            ],
            "summary": "安排架构评审",
        }
    )

    feedback = SourceFidelityEvaluator().evaluate(draft, source)

    assert feedback is None


def test_source_fidelity_accepts_colloquial_range_and_english_feature_alias() -> None:
    draft = RequirementDraft.model_validate(
        {
            "intent": "CREATE_MEETING",
            "durationMinutes": 90,
            "timeWindow": {
                "start": "2026-08-24T13:00:00+08:00",
                "end": "2026-08-24T17:00:00+08:00",
            },
            "requiredParticipantNames": ["李四"],
            "requiredFeatures": ["WHITEBOARD"],
            "minimumCapacity": 8,
            "fieldEvidence": [],
            "summary": "安排上线评审",
        }
    )

    feedback = SourceFidelityEvaluator().evaluate(
        draft,
        "8月24日下午1点到5点这个范围开90分钟会，李四参加，8个人，whiteboard required。",
    )

    assert feedback is None


def test_source_fidelity_accepts_deterministic_mixed_language_evidence() -> None:
    draft = RequirementDraft.model_validate(
        {
            "intent": "CREATE_MEETING",
            "durationMinutes": 30,
            "timeWindow": {
                "start": "2026-08-23T12:00:00+08:00",
                "end": "2026-08-23T18:00:00+08:00",
            },
            "requiredParticipantNames": ["李四"],
            "requiredFeatures": ["WHITEBOARD"],
            "minimumCapacity": 4,
            "fieldEvidence": [
                {
                    "field": "durationMinutes",
                    "source": "30 minutes",
                    "provenance": "USER_DERIVED",
                },
                {
                    "field": "timeWindow",
                    "source": "2026-08-23 13:00-18:00",
                    "provenance": "USER_DERIVED",
                },
                {
                    "field": "minimumCapacity",
                    "source": "capacity 4",
                    "provenance": "USER_DERIVED",
                },
                {
                    "field": "requiredFeatures",
                    "source": "Whiteboard",
                    "provenance": "USER_DERIVED",
                },
            ],
            "summary": "安排同步会议",
        }
    )

    feedback = SourceFidelityEvaluator().evaluate(
        draft,
        "Please book a 30-minute sync with 李四 on 2026-08-23 afternoon, "
        "4 people, whiteboard required.",
    )

    assert feedback is None


def test_source_fidelity_rejects_conflicting_fixed_interval_duration() -> None:
    draft = RequirementDraft.model_validate(
        {
            "intent": "CREATE_MEETING",
            "durationMinutes": 60,
            "timeWindow": {
                "start": "2026-08-25T13:00:00+08:00",
                "end": "2026-08-25T15:00:00+08:00",
            },
            "requiredParticipantNames": [],
            "requiredFeatures": [],
            "fieldEvidence": [],
            "summary": "安排会议",
        }
    )

    feedback = SourceFidelityEvaluator().evaluate(
        draft,
        "请安排2026年8月25日13:00到15:00开会，会议时长60分钟。",
    )

    assert feedback is not None
    assert "DURATION_INTERVAL_MISMATCH" in feedback.codes


def test_clarification_response_rejects_internal_code_and_effect_claim() -> None:
    with pytest.raises(ValidationError):
        ClarificationResponse(message="请补充：TIME_WINDOW_REQUIRED")
    with pytest.raises(ValidationError):
        ClarificationResponse(message="已创建会议，请补充时间。")


def test_route_evaluator_distinguishes_mutation_rules_from_mutation_request() -> None:
    evaluator = RouteEvaluator()

    assert evaluator.fallback("接待重要用户能直接使用vip会议室吗") == (
        Route.POLICY,
        Intent.QUERY_POLICY,
    )
    assert evaluator.fallback("会议取消和改期有哪些规则？") == (
        Route.POLICY,
        Intent.QUERY_POLICY,
    )
    assert evaluator.fallback("会议改期规则是否要求再次确认？给我制度依据。") == (
        Route.POLICY,
        Intent.QUERY_POLICY,
    )
    assert evaluator.fallback("取消会议时是否必须展示预览并确认？请引用政策。") == (
        Route.POLICY,
        Intent.QUERY_POLICY,
    )
    assert evaluator.fallback("张三和李四下周三上午能否找到共同空闲？") == (
        Route.REQUIREMENT,
        Intent.FIND_COMMON_TIME,
    )
    assert evaluator.fallback(
        "先不要创建会议，帮李四和王经理找下周三下午120分钟共同空闲，12人。"
    ) == (Route.REQUIREMENT, Intent.FIND_COMMON_TIME)
    assert evaluator.fallback(
        "帮王经理推荐明天下午可容纳6人的白板会议室，30分钟，不要代我预订。"
    ) == (Route.REQUIREMENT, Intent.RECOMMEND_ROOM)
    assert evaluator.fallback("取消会议 ID 9001，先给我预览。") == (
        Route.REQUIREMENT,
        Intent.CANCEL_MEETING,
    )
    assert evaluator.fallback("把 227 号会议撤掉，不过先让我看清楚目标。") == (
        Route.REQUIREMENT,
        Intent.CANCEL_MEETING,
    )
    assert evaluator.fallback("请根据制度要求取消会议 ID 9001，先给我预览。") == (
        Route.REQUIREMENT,
        Intent.CANCEL_MEETING,
    )
    assert evaluator.fallback("请处理异常重排，会议室已失效。") == (
        Route.REQUIREMENT,
        Intent.MODIFY_MEETING,
    )
    assert evaluator.fallback("帮我找李四和周经理一起空出的时间，只查时间，不预约。") == (
        Route.REQUIREMENT,
        Intent.FIND_COMMON_TIME,
    )
    assert evaluator.fallback("找一间有白板的会议室，只推荐，不要预约。") == (
        Route.REQUIREMENT,
        Intent.RECOMMEND_ROOM,
    )
    assert evaluator.fallback("Please book a sync with 李四.") == (
        Route.REQUIREMENT,
        Intent.CREATE_MEETING,
    )
    assert evaluator.fallback("下周三上午替王经理预留30分钟会议室。") == (
        Route.REQUIREMENT,
        Intent.CREATE_MEETING,
    )
    assert evaluator.fallback("把会议ID 121挪到8月26日上午10点。") == (
        Route.REQUIREMENT,
        Intent.MODIFY_MEETING,
    )
    assert evaluator.fallback("明天下午帮我跟李四碰一下，先让我挑房间。") == (
        Route.REQUIREMENT,
        Intent.CREATE_MEETING,
    )
    assert evaluator.fallback("2026年8月28日2点帮我和李四开一小时会。") == (
        Route.REQUIREMENT,
        Intent.CREATE_MEETING,
    )
    assert evaluator.fallback("调整参会人：赵六不参加，加上孙琪。") == (
        Route.CLARIFICATION,
        None,
    )


def test_requirement_deterministically_normalizes_mixed_language_explicit_facts() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "FIND_COMMON_TIME",
                        "durationMinutes": 60,
                        "timeWindow": {
                            "start": "2026-08-23T12:00:00+08:00",
                            "end": "2026-08-23T18:00:00+08:00",
                        },
                        "requiredParticipantNames": ["李四"],
                        "requiredFeatures": ["whiteboard"],
                        "minimumCapacity": 1,
                        "fieldEvidence": [],
                        "summary": "book a sync",
                    },
                    "missingFields": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_mixed_language",
        run_id="run_mixed_language",
        trace_id="trc_mixed_language",
        user_id=1001,
        roles=["EMPLOYEE"],
        message=(
            "Please book a 30-minute sync with 李四 on 2026-08-23 afternoon, "
            "4 people, whiteboard required."
        ),
        request_time=datetime.fromisoformat("2026-08-15T09:00:00+08:00"),
        intent=Intent.CREATE_MEETING,
    )

    updated, _, _, _ = RequirementAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert updated.meeting_request is not None
    assert updated.meeting_request.intent is Intent.CREATE_MEETING
    assert updated.meeting_request.duration_minutes == 30
    assert updated.meeting_request.minimum_capacity == 4
    assert updated.meeting_request.required_features == ["WHITEBOARD"]
    assert updated.meeting_request.time_window == TimeWindow(
        start=datetime.fromisoformat("2026-08-23T12:00:00+08:00"),
        end=datetime.fromisoformat("2026-08-23T18:00:00+08:00"),
    )
    assert updated.missing_fields == []


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

    updated, _, _, _ = RequirementAgent(provider=provider, runner=StructuredModelRunner()).execute(
        state
    )

    assert updated.meeting_request is not None
    assert updated.meeting_request.target_meeting_reference == "刚才那个会议"
    assert updated.missing_fields == []


def test_requirement_rejects_hallucinated_target_id_and_uses_source_selector() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "MODIFY_MEETING",
                        "durationMinutes": None,
                        "targetMeetingId": 121,
                        "targetMeetingReference": "架构评审",
                        "requiredParticipantNames": [],
                        "requiredFeatures": [],
                        "fieldEvidence": [],
                        "summary": "改期",
                    },
                    "missingFields": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    source = "把8月25日下午的会挪到第二天上午，其他照旧。"
    state = AgentState(
        thread_id="thread_safe_target_selector",
        run_id="run_safe_target_selector",
        trace_id="trc_safe_target_selector",
        user_id=1001,
        roles=["EMPLOYEE"],
        message=source,
        request_time=datetime.fromisoformat("2026-08-15T10:00:00+08:00"),
        intent=Intent.MODIFY_MEETING,
    )

    updated, _, _, _ = RequirementAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert updated.meeting_request is not None
    assert updated.meeting_request.target_meeting_id is None
    assert updated.meeting_request.target_meeting_reference == "把8月25日下午的会"


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

    updated, _, _, _ = RequirementAgent(provider=provider, runner=StructuredModelRunner()).execute(
        state
    )

    assert updated.missing_fields == []
    assert updated.meeting_request is not None
    assert updated.meeting_request.title == "架构评审"
    assert updated.meeting_request.meeting_type == "ARCHITECTURE_REVIEW"


def test_requirement_resolves_first_person_to_current_authenticated_user() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "CREATE_MEETING",
                        "durationMinutes": 60,
                        "timeWindow": {
                            "start": "2026-08-28T14:00:00+08:00",
                            "end": "2026-08-28T15:00:00+08:00",
                        },
                        "requiredParticipantNames": ["李四"],
                        "requiredFeatures": [],
                        "fieldEvidence": [],
                        "summary": "安排会议",
                    },
                    "missingFields": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_first_person",
        run_id="run_first_person",
        trace_id="trc_first_person",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="2026年8月28日下午2点帮我和李四开一小时会。",
        request_time=datetime.fromisoformat("2026-08-15T09:00:00+08:00"),
        intent=Intent.CREATE_MEETING,
    )

    updated, _, _, _ = RequirementAgent(provider=provider, runner=StructuredModelRunner()).execute(
        state
    )

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.includes_current_user is True
    assert updated.meeting_request is not None
    assert updated.meeting_request.includes_current_user is True
    participant_item = next(
        item for item in updated.requirement_items if item.field == "requiredParticipants"
    )
    assert participant_item.summary == "2人：当前登录用户（我）、李四"


@pytest.mark.parametrize(
    ("message", "request_time", "expected_start", "expected_end"),
    [
        (
            "25号上午安排会议，我自己参加，开60分钟。",
            "2026-08-14T09:00:00+08:00",
            "2026-08-25T06:00:00+08:00",
            "2026-08-25T12:00:00+08:00",
        ),
        (
            "周三中午安排会议，我自己参加，开60分钟。",
            "2026-08-10T09:00:00+08:00",
            "2026-08-12T11:00:00+08:00",
            "2026-08-12T14:00:00+08:00",
        ),
        (
            "下午安排会议，我自己参加，开60分钟。",
            "2026-08-14T09:00:00+08:00",
            "2026-08-14T12:00:00+08:00",
            "2026-08-14T18:00:00+08:00",
        ),
        (
            "今天晚上安排会议，我自己参加，开60分钟。",
            "2026-08-14T09:00:00+08:00",
            "2026-08-14T18:00:00+08:00",
            "2026-08-15T06:00:00+08:00",
        ),
        (
            "下午2点安排会议，我自己参加，开60分钟。",
            "2026-08-14T09:00:00+08:00",
            "2026-08-14T14:00:00+08:00",
            "2026-08-14T15:00:00+08:00",
        ),
        (
            "14点安排会议，我自己参加，开60分钟。",
            "2026-08-14T09:00:00+08:00",
            "2026-08-14T14:00:00+08:00",
            "2026-08-14T15:00:00+08:00",
        ),
    ],
)
def test_requirement_applies_deterministic_partial_time_defaults(
    message: str,
    request_time: str,
    expected_start: str,
    expected_end: str,
) -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "CREATE_MEETING",
                        "durationMinutes": 60,
                        "requiredParticipantNames": [],
                        "fieldEvidence": [],
                        "summary": "安排会议",
                    },
                    "missingFields": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_time_defaults",
        run_id="run_time_defaults",
        trace_id="trc_time_defaults",
        user_id=1001,
        roles=["EMPLOYEE"],
        message=message,
        request_time=datetime.fromisoformat(request_time),
    )

    updated, _, _, _ = RequirementAgent(provider=provider, runner=StructuredModelRunner()).execute(
        state
    )

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.time_window is not None
    assert updated.requirement_draft.time_window.start.isoformat() == expected_start
    assert updated.requirement_draft.time_window.end.isoformat() == expected_end
    assert updated.requirement_items[0].status.value == "DEFAULTED"


def test_requirement_asks_for_meridiem_when_single_hour_is_ambiguous() -> None:
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": 60,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "安排会议",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_ambiguous_hour",
        run_id="run_ambiguous_hour",
        trace_id="trc_ambiguous_hour",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="2点安排会议，我自己参加，开60分钟。",
        request_time=datetime.fromisoformat("2026-08-14T09:00:00+08:00"),
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction, extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.time_window is None
    assert updated.requirement_draft.pending_start_at is not None
    assert updated.requirement_draft.pending_start_ambiguous is True
    assert "TIME_MERIDIEM_AMBIGUOUS" in updated.missing_fields
    assert updated.requirement_items[0].status.value == "AMBIGUOUS"
    assert updated.requirement_items[0].blocking is True

    second_extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": None,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "确认下午",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    second_state = updated.model_copy(
        update={
            "message": "是下午。",
            "continuation_turn": True,
            "request_time": datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        }
    )

    resolved, _, _, _ = RequirementAgent(
        provider=QueueProvider([second_extraction]),
        runner=StructuredModelRunner(),
    ).execute(second_state)

    assert resolved.requirement_draft is not None
    assert resolved.requirement_draft.time_window is not None
    assert resolved.requirement_draft.time_window.start.isoformat() == "2026-08-14T14:00:00+08:00"
    assert resolved.requirement_draft.time_window.end.isoformat() == "2026-08-14T15:00:00+08:00"
    assert resolved.missing_fields == []


@pytest.mark.parametrize(
    ("message", "expected_destination"),
    [
        (
            "把25号下午1点的架构评审改到25号下午2点，其他都不变。",
            "2026-08-25T14:00:00+08:00",
        ),
        (
            "把25号下午两点的架构评审改到27号同一时间，其他都不变。",
            "2026-08-27T14:00:00+08:00",
        ),
    ],
)
def test_requirement_deterministically_separates_mutation_selector_and_destination(
    message: str,
    expected_destination: str,
) -> None:
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "MODIFY_MEETING",
                # Simulate the live model incorrectly returning the selector as
                # the destination. The deterministic normalizer must override it.
                "timeWindow": {
                    "start": "2026-08-25T13:00:00+08:00",
                    "end": "2026-08-25T14:00:00+08:00",
                },
                "targetMeetingReference": "架构评审",
                "fieldEvidence": [],
                "summary": "改期架构评审",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_mutation_destination",
        run_id="run_mutation_destination",
        trace_id="trc_mutation_destination",
        user_id=1001,
        roles=["EMPLOYEE"],
        message=message,
        request_time=datetime.fromisoformat("2026-08-14T09:00:00+08:00"),
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.time_window is None
    assert updated.requirement_draft.pending_start_at is not None
    assert updated.requirement_draft.pending_start_at.isoformat() == expected_destination
    assert "25号" in (updated.requirement_draft.target_meeting_reference or "")


def test_time_only_start_survives_until_duration_arrives_on_next_turn() -> None:
    first_extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": None,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "安排会议",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    first_state = AgentState(
        thread_id="thread_pending_start",
        run_id="run_pending_start",
        trace_id="trc_pending_start",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="14点安排会议，我自己参加。",
        request_time=datetime.fromisoformat("2026-08-14T09:00:00+08:00"),
    )

    first, _, _, _ = RequirementAgent(
        provider=QueueProvider([first_extraction]),
        runner=StructuredModelRunner(),
    ).execute(first_state)

    assert first.requirement_draft is not None
    assert first.requirement_draft.pending_start_at is not None
    assert first.requirement_draft.pending_start_at.isoformat() == "2026-08-14T14:00:00+08:00"
    assert first.missing_fields == ["durationMinutes"]
    assert first.requirement_items[0].status.value == "DEFAULTED"

    second_extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": 60,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "补充时长",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    second_state = first.model_copy(
        update={
            "message": "开60分钟。",
            "continuation_turn": True,
            "request_time": datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        }
    )

    second, _, _, _ = RequirementAgent(
        provider=QueueProvider([second_extraction]),
        runner=StructuredModelRunner(),
    ).execute(second_state)

    assert second.requirement_draft is not None
    assert second.requirement_draft.time_window is not None
    assert second.requirement_draft.time_window.start.isoformat() == "2026-08-14T14:00:00+08:00"
    assert second.requirement_draft.time_window.end.isoformat() == "2026-08-14T15:00:00+08:00"
    assert second.missing_fields == []


def test_soft_start_preference_keeps_previous_date_window_on_continuation() -> None:
    previous = RequirementDraft(
        intent=Intent.CREATE_MEETING,
        duration_minutes=None,
        time_window=TimeWindow(
            start=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            end=datetime.fromisoformat("2026-08-25T18:00:00+08:00"),
        ),
        required_participant_names=["张三", "李四", "王五", "赵六"],
        participant_scope="MY_DEPARTMENT",
        minimum_capacity=4,
        summary="25日下午的小组会议",
    )
    # Simulate a provider incorrectly promoting the soft "最好2点" preference
    # to an ambiguous hard time on the day of this continuation request.
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": 120,
                "pendingStartAt": "2026-08-14T02:00:00+08:00",
                "pendingStartAmbiguous": True,
                "requiredParticipantNames": [],
                "requiredFeatures": ["LARGE_SCREEN"],
                "fieldEvidence": [],
                "summary": "补充时长、投屏和开始偏好",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_soft_preference",
        run_id="run_soft_preference",
        trace_id="trc_soft_preference",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="会开2个小时，要有投屏，没别的要求，最好是2点开始。",
        request_time=datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        requirement_draft=previous,
        continuation_turn=True,
        requirement_revision=1,
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.time_window is not None
    assert updated.requirement_draft.time_window.start.isoformat() == "2026-08-25T12:00:00+08:00"
    assert updated.requirement_draft.time_window.end.isoformat() == "2026-08-25T18:00:00+08:00"
    assert updated.requirement_draft.pending_start_at is None
    assert updated.requirement_draft.duration_minutes == 120
    assert updated.requirement_draft.required_features == ["LARGE_SCREEN"]
    assert updated.requirement_draft.soft_constraints[-1].value == "14:00"
    assert updated.missing_fields == []


def test_optional_requirements_distinguish_unspecified_from_closed() -> None:
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": None,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "安排会议",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    first_state = AgentState(
        thread_id="thread_optional_state",
        run_id="run_optional_state",
        trace_id="trc_optional_state",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="25号下午安排会议，我自己参加。",
        request_time=datetime.fromisoformat("2026-08-14T09:00:00+08:00"),
    )
    first, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction, extraction]),
        runner=StructuredModelRunner(),
    ).execute(first_state)

    first_optional = next(
        item for item in first.requirement_items if item.field == "optionalRequirements"
    )
    assert first_optional.status.value == "UNSPECIFIED"
    assert first_optional.blocking is False
    assert first_optional.source is None

    second_extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": 120,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "补充时长并结束其他要求",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    second_state = first.model_copy(
        update={
            "message": "会开2个小时，没别的要求。",
            "continuation_turn": True,
            "request_time": datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        }
    )
    second, _, _, _ = RequirementAgent(
        provider=QueueProvider([second_extraction]),
        runner=StructuredModelRunner(),
    ).execute(second_state)

    second_optional = next(
        item for item in second.requirement_items if item.field == "optionalRequirements"
    )
    assert second_optional.status.value == "CLOSED"
    assert second_optional.blocking is False
    assert second_optional.summary == "没有其他硬性要求"


def test_participant_removal_is_applied_to_verified_previous_roster() -> None:
    previous = RequirementDraft(
        intent=Intent.CREATE_MEETING,
        duration_minutes=120,
        time_window=TimeWindow(
            start=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            end=datetime.fromisoformat("2026-08-25T18:00:00+08:00"),
        ),
        required_participant_names=["张三", "李四", "王五", "赵六"],
        participant_scope="MY_DEPARTMENT",
        minimum_capacity=4,
        summary="小组会议",
    )
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "赵六不参加",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_remove_participant",
        run_id="run_remove_participant",
        trace_id="trc_remove_participant",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="赵六请假不会来。",
        request_time=datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        requirement_draft=previous,
        resolved_employees=[
            Participant(name="张三", employee_id=1001),
            Participant(name="李四", employee_id=1002),
            Participant(name="王五", employee_id=1010),
            Participant(name="赵六", employee_id=1011),
        ],
        continuation_turn=True,
        requirement_revision=1,
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.required_participant_names == ["张三", "李四", "王五"]
    assert updated.requirement_draft.participant_scope == "MY_DEPARTMENT"
    assert updated.requirement_draft.participant_list_modified is True
    assert updated.requirement_draft.minimum_capacity == 3
    assert [item.name for item in updated.resolved_employees] == ["张三", "李四", "王五"]
    participant_item = next(
        item for item in updated.requirement_items if item.field == "requiredParticipants"
    )
    assert participant_item.status.value == "EXPLICIT"
    assert participant_item.summary == "3人：张三、李四、王五"


def test_participant_removal_and_addition_are_applied_in_one_continuation() -> None:
    previous = RequirementDraft(
        intent=Intent.CREATE_MEETING,
        duration_minutes=60,
        time_window=TimeWindow(
            start=datetime.fromisoformat("2026-08-25T13:00:00+08:00"),
            end=datetime.fromisoformat("2026-08-25T17:00:00+08:00"),
        ),
        required_participant_names=["李四", "赵六", "周经理"],
        minimum_capacity=4,
        summary="项目评审",
    )
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "MODIFY_MEETING",
                "requiredParticipantNames": ["孙琪"],
                "fieldEvidence": [],
                "summary": "调整参会人",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_replace_participant",
        run_id="run_replace_participant",
        trace_id="trc_replace_participant",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="调整参会人：赵六不参加了，加上孙琪必须参加。",
        request_time=datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        intent=Intent.CREATE_MEETING,
        requirement_draft=previous,
        continuation_turn=True,
        requirement_revision=1,
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.intent is Intent.CREATE_MEETING
    assert updated.requirement_draft is not None
    assert updated.requirement_draft.required_participant_names == [
        "李四",
        "周经理",
        "孙琪",
    ]


def test_create_continuation_time_change_does_not_become_reschedule() -> None:
    previous = RequirementDraft(
        intent=Intent.CREATE_MEETING,
        duration_minutes=60,
        time_window=TimeWindow(
            start=datetime.fromisoformat("2026-08-25T13:00:00+08:00"),
            end=datetime.fromisoformat("2026-08-25T16:00:00+08:00"),
        ),
        participant_scope="ORGANIZER_ONLY",
        minimum_capacity=4,
        summary="会议安排",
    )
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "MODIFY_MEETING",
                "durationMinutes": 180,
                "timeWindow": {
                    "start": "2026-08-26T09:00:00+08:00",
                    "end": "2026-08-26T12:00:00+08:00",
                },
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "改到次日上午",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_create_time_change",
        run_id="run_create_time_change",
        trace_id="trc_create_time_change",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="那改到8月26日上午9点到12点这个范围，时长和其他要求不变。",
        request_time=datetime.fromisoformat("2026-08-14T09:05:00+08:00"),
        intent=Intent.CREATE_MEETING,
        requirement_draft=previous,
        continuation_turn=True,
        requirement_revision=1,
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.intent is Intent.CREATE_MEETING
    assert updated.meeting_request is not None
    assert updated.meeting_request.intent is Intent.CREATE_MEETING
    assert updated.meeting_request.duration_minutes == 60
    assert updated.meeting_request.time_window is not None
    assert updated.meeting_request.time_window.start.isoformat() == "2026-08-26T09:00:00+08:00"
    assert updated.meeting_request.time_window.end.isoformat() == "2026-08-26T12:00:00+08:00"


def test_requirement_derives_duration_only_from_explicit_fixed_interval() -> None:
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": None,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "安排会议",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    provider = QueueProvider([extraction, extraction])
    state = AgentState(
        thread_id="thread_fixed_interval",
        run_id="run_fixed_interval",
        trace_id="trc_fixed_interval",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="今天13点到14点安排会议，我自己参加。",
        request_time=datetime.fromisoformat("2026-08-14T09:00:00+08:00"),
    )

    updated, _, _, _ = RequirementAgent(provider=provider, runner=StructuredModelRunner()).execute(
        state
    )

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.duration_minutes == 60
    assert "durationMinutes" not in updated.missing_fields


def test_past_current_month_default_is_clarified_without_rolling_forward() -> None:
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": 60,
                "requiredParticipantNames": [],
                "fieldEvidence": [],
                "summary": "安排会议",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_past_default",
        run_id="run_past_default",
        trace_id="trc_past_default",
        user_id=1001,
        roles=["EMPLOYEE"],
        message="10号下午安排会议，我自己参加，开60分钟。",
        request_time=datetime.fromisoformat("2026-08-14T09:00:00+08:00"),
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction, extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.time_window is not None
    assert updated.requirement_draft.time_window.start.isoformat() == "2026-08-10T12:00:00+08:00"
    assert "TIME_WINDOW_IN_PAST" in updated.missing_fields
    assert updated.requirement_items[0].status.value == "CONFLICT"
    assert updated.requirement_items[0].blocking is True


def test_spaced_absolute_date_and_first_person_enumeration_are_normalized() -> None:
    message = (
        "请在 2026 年 8 月 26 日 13:00 到 15:00 之间安排 60 分钟会议，"
        "我，李四，王五，赵六，没有设备要求"
    )
    extraction = json.dumps(
        {
            "requirementDraft": {
                "intent": "CREATE_MEETING",
                "durationMinutes": 60,
                "timeWindow": {
                    "start": "2026-08-15T13:00:00+08:00",
                    "end": "2026-08-15T15:00:00+08:00",
                },
                "requiredParticipantNames": ["李四", "王五", "赵六"],
                "requiredFeatures": [],
                "fieldEvidence": [],
                "summary": "安排会议",
            },
            "missingFields": [],
        },
        ensure_ascii=False,
    )
    state = AgentState(
        thread_id="thread_spaced_date",
        run_id="run_spaced_date",
        trace_id="trc_spaced_date",
        user_id=1001,
        roles=["EMPLOYEE"],
        message=message,
        request_time=datetime.fromisoformat("2026-08-15T09:00:00+08:00"),
    )

    updated, _, _, _ = RequirementAgent(
        provider=QueueProvider([extraction]),
        runner=StructuredModelRunner(),
    ).execute(state)

    assert updated.requirement_draft is not None
    assert updated.requirement_draft.time_window is not None
    assert updated.requirement_draft.time_window.start.isoformat() == "2026-08-26T13:00:00+08:00"
    assert updated.requirement_draft.time_window.end.isoformat() == "2026-08-26T15:00:00+08:00"
    assert updated.requirement_draft.includes_current_user is True
    assert updated.optional_requirements_closed is True
    optional = next(
        item for item in updated.requirement_items if item.field == "optionalRequirements"
    )
    assert optional.status.value == "CLOSED"


def test_requirement_uses_supervisor_create_intent_without_rewriting_faithful_fields() -> None:
    provider = QueueProvider(
        [
            json.dumps(
                {
                    "requirementDraft": {
                        "intent": "RECOMMEND_ROOM",
                        "durationMinutes": 60,
                        "timeWindow": {
                            "start": "2026-08-25T13:00:00+08:00",
                            "end": "2026-08-25T18:00:00+08:00",
                        },
                        "requiredParticipantNames": ["张三", "李四"],
                        "requiredFeatures": ["WHITEBOARD", "LARGE_SCREEN"],
                        "fieldEvidence": [
                            {
                                "field": "durationMinutes",
                                "source": "60分钟",
                                "provenance": "USER_EXPLICIT",
                            },
                            {
                                "field": "timeWindow",
                                "source": "2026年8月25日13:00到18:00之间",
                                "provenance": "USER_EXPLICIT",
                            },
                            {
                                "field": "requiredParticipantNames",
                                "source": "张三和李四",
                                "provenance": "USER_EXPLICIT",
                            },
                            {
                                "field": "requiredFeatures",
                                "source": "需要白板和大屏",
                                "provenance": "USER_EXPLICIT",
                            },
                        ],
                        "summary": "给出候选方案",
                    },
                    "missingFields": [
                        "title",
                        "meetingType",
                        "minimumCapacity",
                        "preferredBuildings",
                    ],
                },
                ensure_ascii=False,
            )
        ]
    )
    state = AgentState(
        thread_id="thread_candidate_is_hitl",
        run_id="run_candidate_is_hitl",
        trace_id="trc_candidate_is_hitl",
        user_id=1001,
        roles=["EMPLOYEE"],
        message=(
            "请在2026年8月25日13:00到18:00之间，为张三和李四安排一场60分钟架构评审，"
            "需要白板和大屏。先给出最多3个候选方案，不要替我确认。"
        ),
        request_time=datetime.fromisoformat("2026-08-14T10:00:00+08:00"),
        intent=Intent.CREATE_MEETING,
    )

    updated, _, calls, _ = RequirementAgent(
        provider=provider, runner=StructuredModelRunner()
    ).execute(state)

    assert calls == 1
    assert updated.meeting_request is not None
    assert updated.meeting_request.intent is Intent.CREATE_MEETING
    assert updated.meeting_request.duration_minutes == 60
    assert updated.missing_fields == []
    assert updated.next_route is Route.SCHEDULING


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
def test_create_draft_requires_asia_shanghai_offset(start_at: str, end_at: str) -> None:
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
        user_prompt=(
            "下周三下午帮张三安排一个90分钟架构评审，10人，"
            "要视频会议设备，优先总部楼"
        ),
        schema_name="RequirementExtraction",
        schema={},
    )

    completion = provider.complete(request)
    assert completion.content is not None
    result = json.loads(completion.content)
    meeting = result["requirementDraft"]

    assert meeting["minimumCapacity"] == 10
    assert meeting["requiredFeatures"] == ["VIDEO_CONFERENCE"]
    assert meeting["preferredBuildings"] == ["总部楼"]


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
        ToolModelRequest(agent_name="scheduling", messages=(system, user), tools=(), iteration=1)
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
