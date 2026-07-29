"""Regression guards for the Day 5 fixture-backed Golden Path inputs."""

from __future__ import annotations

from datetime import datetime

from app.providers.base import ModelRequest, StructuredModelRunner
from app.providers.fixture import FixtureModelProvider
from app.schemas.agent import Intent, RequirementExtraction, Route, SupervisorDecision

DAY5_NORMAL_MESSAGE = "下周三下午帮张三安排一个90分钟架构评审，要大屏"
DAY5_HOT_MESSAGE = "下周三下午帮张三安排一个90分钟架构评审，10人，要大屏"


def _invoke(message: str) -> SupervisorDecision:
    return StructuredModelRunner().invoke(
        provider=FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00")),
        request=ModelRequest(
            agent_name="supervisor",
            system_prompt="fixture regression",
            user_prompt=message,
            schema_name="SupervisorDecision",
            schema=SupervisorDecision.model_json_schema(by_alias=True),
        ),
        output_type=SupervisorDecision,
    )


def _extract(message: str) -> RequirementExtraction:
    return StructuredModelRunner().invoke(
        provider=FixtureModelProvider(datetime.fromisoformat("2026-08-11T10:00:00+08:00")),
        request=ModelRequest(
            agent_name="requirement",
            system_prompt="fixture regression",
            user_prompt=message,
            schema_name="RequirementExtraction",
            schema=RequirementExtraction.model_json_schema(by_alias=True),
        ),
        output_type=RequirementExtraction,
    )


def test_day5_normal_and_hot_fixture_inputs_remain_create_paths() -> None:
    normal_route = _invoke(DAY5_NORMAL_MESSAGE)
    hot_route = _invoke(DAY5_HOT_MESSAGE)
    normal = _extract(DAY5_NORMAL_MESSAGE)
    hot = _extract(DAY5_HOT_MESSAGE)

    assert normal_route.route is Route.REQUIREMENT
    assert hot_route.route is Route.REQUIREMENT
    assert normal.meeting_request.intent is Intent.CREATE_MEETING
    assert hot.meeting_request.intent is Intent.CREATE_MEETING
    assert normal.meeting_request.target_meeting_id is None
    assert hot.meeting_request.target_meeting_id is None
    assert normal.meeting_request.required_features == ["LARGE_SCREEN"]
    assert hot.meeting_request.required_features == ["LARGE_SCREEN"]
    assert normal.meeting_request.duration_minutes == hot.meeting_request.duration_minutes == 90
    assert normal.missing_fields == hot.missing_fields == []
    assert hot.meeting_request.minimum_capacity == 10
