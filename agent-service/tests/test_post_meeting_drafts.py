from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.internal import get_model_provider
from app.config import get_settings
from app.main import app
from app.providers.base import (
    ModelCompletion,
    ModelProviderError,
    ModelRequest,
    ToolModelRequest,
    ToolModelResponse,
)
from app.providers.fixture import FixtureModelProvider
from app.schemas.agent import PostMeetingDraft, PostMeetingDraftRequest


class QueueProvider:
    def __init__(self, responses: list[ModelCompletion | str | Exception]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []
        self.tool_requests: list[ToolModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelCompletion | str:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def complete_tools(self, request: ToolModelRequest) -> ToolModelResponse:
        self.tool_requests.append(request)
        raise AssertionError("post-meeting analysis must not enter the Tool loop")


def _headers(*, run_id: str, trace_id: str) -> dict[str, str]:
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": "1001",
            "roles": ["EMPLOYEE"],
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


def _request_payload() -> dict[str, object]:
    return {
        "meetingId": 9001,
        "title": "支付网关 V2 上线评审",
        "meetingType": "ARCHITECTURE_REVIEW",
        "startAt": "2026-08-19T15:00:00+08:00",
        "endAt": "2026-08-19T16:00:00+08:00",
        "participants": [
            {"employeeId": 1001, "displayName": "张三"},
            {"employeeId": 1002, "displayName": "李四"},
        ],
        "transcript": (
            "会议讨论了支付网关 V2 的灰度发布范围。"
            "会议决定先完成回滚演练再发布。"
            "行动项：李四负责补充回滚演练，截止 2026-08-20 18:00。"
        ),
    }


def _draft_json(*, assignee_id: int | None = 1002) -> str:
    return json.dumps(
        {
            "minutes": {
                "background": "支付网关 V2 上线评审讨论发布准备。",
                "discussionSummary": "与会人员讨论了灰度范围和回滚演练。",
                "conclusion": "完成回滚演练后再发布。",
            },
            "decisions": [
                {"content": "先完成回滚演练再发布。", "rationale": None}
            ],
            "actionItems": [
                {
                    "title": "补充回滚演练",
                    "description": None,
                    "assigneeEmployeeId": assignee_id,
                    "dueAt": "2026-08-20T18:00:00+08:00",
                }
            ],
        },
        ensure_ascii=False,
    )


@contextmanager
def _client_with_provider(provider: object) -> Iterator[TestClient]:
    app.dependency_overrides[get_model_provider] = lambda: provider
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_model_provider, None)


def test_fixture_generates_deterministic_post_meeting_draft_without_network() -> None:
    provider = FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00"))
    with _client_with_provider(provider) as client:
        first = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_post_meeting_1", trace_id="trc_post_meeting_1"),
            json=_request_payload(),
        )
        second = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_post_meeting_2", trace_id="trc_post_meeting_2"),
            json=_request_payload(),
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = first.json()
    assert set(body) == {
        "agentRunId",
        "model",
        "promptVersion",
        "schemaVersion",
        "draft",
    }
    assert body["agentRunId"] == "run_post_meeting_1"
    assert body["model"] == "fixture"
    assert body["promptVersion"] == "post-meeting-analysis-v1"
    assert body["schemaVersion"] == "post-meeting-draft-v1"
    assert body["draft"] == second.json()["draft"]
    assert body["draft"]["actionItems"] == [
        {
            "title": "补充回滚演练",
            "description": None,
            "assigneeEmployeeId": 1002,
            "dueAt": "2026-08-20T18:00:00+08:00",
        }
    ]
    assert provider.network_calls == 0
    assert first.headers["cache-control"] == "no-store"


def test_unknown_model_assignee_is_normalized_to_null() -> None:
    provider = QueueProvider(
        [ModelCompletion(content=_draft_json(assignee_id=9999), model="safe-test-model")]
    )
    with _client_with_provider(provider) as client:
        response = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_unknown_assignee", trace_id="trc_unknown_assignee"),
            json=_request_payload(),
        )

    assert response.status_code == 200
    assert response.json()["model"] == "safe-test-model"
    assert response.json()["draft"]["actionItems"][0]["assigneeEmployeeId"] is None
    assert len(provider.requests) == 1
    assert provider.requests[0].agent_name == "requirement"
    assert provider.requests[0].schema_name == "PostMeetingDraft"
    assert provider.tool_requests == []


def test_invalid_model_output_is_repaired_at_most_once() -> None:
    provider = QueueProvider(["{", _draft_json()])
    with _client_with_provider(provider) as client:
        response = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_repaired", trace_id="trc_repaired"),
            json=_request_payload(),
        )

    assert response.status_code == 200
    assert [request.repair_attempt for request in provider.requests] == [0, 1]
    assert provider.tool_requests == []


@pytest.mark.parametrize(
    ("responses", "expected_status", "expected_detail", "expected_calls"),
    [
        (["{", "{}"], 502, "POST_MEETING_OUTPUT_INVALID", 2),
        ([ModelCompletion(content=None)], 502, "POST_MEETING_OUTPUT_INVALID", 1),
        (
            [ModelProviderError("provider failed")],
            503,
            "POST_MEETING_PROVIDER_UNAVAILABLE",
            1,
        ),
    ],
)
def test_post_meeting_model_failures_return_stable_errors(
    responses: list[ModelCompletion | str | Exception],
    expected_status: int,
    expected_detail: str,
    expected_calls: int,
) -> None:
    provider = QueueProvider(responses)
    with _client_with_provider(provider) as client:
        response = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_model_failure", trace_id="trc_model_failure"),
            json=_request_payload(),
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert len(provider.requests) == expected_calls
    assert provider.tool_requests == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"runId": "run_forged"}),
        lambda payload: payload.update({"startAt": "2026-08-19T15:00:00"}),
        lambda payload: payload.update(
            {
                "participants": [
                    {"employeeId": 1001, "displayName": "张三"},
                    {"employeeId": 1001, "displayName": "伪造重名"},
                ]
            }
        ),
        lambda payload: payload.update({"transcript": " "}),
    ],
)
def test_post_meeting_request_rejects_untrusted_or_invalid_shapes(
    mutate: object,
) -> None:
    payload = copy.deepcopy(_request_payload())
    assert callable(mutate)
    mutate(payload)
    provider = QueueProvider([_draft_json()])
    with _client_with_provider(provider) as client:
        response = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_invalid_request", trace_id="trc_invalid_request"),
            json=payload,
        )

    assert response.status_code == 422
    assert provider.requests == []


def test_post_meeting_output_contract_enforces_limits_and_timezone() -> None:
    base = json.loads(_draft_json())
    too_many_decisions = copy.deepcopy(base)
    too_many_decisions["decisions"] = [
        {"content": f"决定 {index}", "rationale": None} for index in range(21)
    ]
    wrong_timezone = copy.deepcopy(base)
    wrong_timezone["actionItems"][0]["dueAt"] = "2026-08-20T19:00:00+09:00"
    too_many_action_items = copy.deepcopy(base)
    too_many_action_items["actionItems"] = [
        {
            "title": f"行动项 {index}",
            "description": None,
            "assigneeEmployeeId": None,
            "dueAt": None,
        }
        for index in range(51)
    ]
    extra_output = copy.deepcopy(base)
    extra_output["unexpected"] = True

    with pytest.raises(ValidationError):
        PostMeetingDraft.model_validate(too_many_decisions)
    with pytest.raises(ValidationError):
        PostMeetingDraft.model_validate(wrong_timezone)
    with pytest.raises(ValidationError):
        PostMeetingDraft.model_validate(too_many_action_items)
    with pytest.raises(ValidationError):
        PostMeetingDraft.model_validate(extra_output)


def test_post_meeting_request_contract_enforces_lengths_and_quantities() -> None:
    too_many_participants = copy.deepcopy(_request_payload())
    too_many_participants["participants"] = [
        {"employeeId": index + 1, "displayName": f"虚构员工{index + 1}"}
        for index in range(101)
    ]
    oversized_transcript = copy.deepcopy(_request_payload())
    oversized_transcript["transcript"] = "字" * 20_001

    with pytest.raises(ValidationError):
        PostMeetingDraftRequest.model_validate(too_many_participants)
    with pytest.raises(ValidationError):
        PostMeetingDraftRequest.model_validate(oversized_transcript)


def test_post_meeting_endpoint_requires_matching_internal_context() -> None:
    provider = QueueProvider([_draft_json()])
    with _client_with_provider(provider) as client:
        missing = client.post(
            "/internal/v1/post-meeting/drafts",
            json=_request_payload(),
        )
        mismatched = client.post(
            "/internal/v1/post-meeting/drafts",
            headers=_headers(run_id="run_actual", trace_id="trc_actual")
            | {"X-Run-Id": "run_other"},
            json=_request_payload(),
        )

    assert missing.status_code == 401
    assert mismatched.status_code == 401
    assert provider.requests == []
