"""Verified clarification contracts and safe user-facing rendering."""

from __future__ import annotations

import json
import re

from app.providers.base import (
    ModelCompletion,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
)
from app.schemas.agent import (
    ClarificationResponse,
    MeetingRequest,
)
from app.workflow_core.prompts import CLARIFICATION_PROMPT

_CLARIFICATION_GUIDANCE: dict[str, tuple[str, str]] = {
    "OBJECTIVE_NOT_UNDERSTOOD": (
        "我还不能确定你希望查询规则、查找时间，还是创建、修改或取消会议。",
        "请直接说明要完成的会议操作。",
    ),
    "TIME_WINDOW_REQUIRED": (
        "还没有可用于排期的日期和时间范围。",
        "请告诉我希望安排在哪一天、哪个时间段。",
    ),
    "TIME_WINDOW_IN_PAST": (
        "给出的时间范围已经过去，无法继续排期。",
        "请提供一个未来的日期和时间范围。",
    ),
    "TIME_NOT_ON_30_MINUTE_SLOT": (
        "会议时间需要落在半小时的时间点上。",
        "请把开始和结束时间调整到整点或半点。",
    ),
    "WINDOW_SHORTER_THAN_DURATION": (
        "可选时间范围短于会议需要的时长。",
        "请延长可选时间范围，或缩短会议时长。",
    ),
    "DURATION_INTERVAL_MISMATCH": (
        "固定起止时间和会议时长不一致。",
        "请确认以起止时间为准，还是以会议时长为准。",
    ),
    "TARGET_REFERENCE_MISSING": (
        "还不能唯一确定要修改或取消哪场会议。",
        "请提供会议编号，或说明会议标题和时间。",
    ),
    "TARGET_MEETING_REQUIRED": (
        "还不能唯一确定要修改或取消哪场会议。",
        "请提供会议编号，或说明会议标题和时间。",
    ),
    "uniqueTargetMeeting": (
        "找到了不止一场可能匹配的会议。",
        "请提供会议编号，或补充会议标题和时间。",
    ),
    "CAPACITY_BELOW_PARTICIPANTS": (
        "会议室容量要求小于必须参加的人数。",
        "请提高最低容量，或确认哪些人不是必需参会者。",
    ),
    "HARD_SOFT_CONSTRAINT_CONFLICT": (
        "同一个条件同时被设为必须满足和尽量满足。",
        "请确认这个条件是硬性要求还是偏好。",
    ),
    "EXPLICIT_PARTICIPANT_OMITTED": (
        "参会者信息没有被可靠识别完整。",
        "请重新列出必须参加的人员姓名。",
    ),
    "PARTICIPANT_NOT_IN_SOURCE": (
        "参会者信息无法从原请求中可靠确认。",
        "请重新列出必须参加的人员姓名。",
    ),
    "HEADCOUNT_AS_PARTICIPANT": (
        "人数被误识别成了人员姓名。",
        "请分别说明必须参加的人员姓名和预计总人数。",
    ),
    "CAPACITY_SOURCE_MISMATCH": (
        "预计人数没有被可靠识别。",
        "请重新确认预计总人数。",
    ),
    "FEATURE_NOT_IN_SOURCE": (
        "所需设备无法从原请求中可靠确认。",
        "请重新说明必须具备的会议室设备。",
    ),
    "EXPLICIT_TIME_CHANGED": (
        "时间范围没有被可靠保留下来。",
        "请重新确认允许安排会议的开始和结束时间。",
    ),
    "INTENT_SOURCE_MISMATCH": (
        "会议操作没有被可靠识别。",
        "请确认要创建、修改还是取消会议。",
    ),
    "EVIDENCE_NOT_IN_SOURCE": (
        "有一项信息无法从原请求中可靠确认。",
        "请用一句话重新说明时间、时长、参会者和必要设备。",
    ),
    "EMPLOYEE_UNRESOLVED": (
        "有一位或多位参会者无法在组织通讯录中唯一匹配。",
        "请核对姓名；如有同名人员，请补充部门信息。",
    ),
}

_CLARIFICATION_FIELD_GUIDANCE: tuple[tuple[str, tuple[str, str]], ...] = (
    (
        "participant",
        (
            "还不知道哪些人必须参加这场会议。",
            "请告诉我必需参会者姓名；如果只有你参加，也请直接说明。",
        ),
    ),
    ("duration", ("还不知道会议需要持续多久。", "请提供会议时长，例如30分钟或60分钟。")),
    ("time", ("还没有可用于排期的时间信息。", "请提供日期和允许安排的时间范围。")),
    (
        "target",
        ("还不能唯一确定要操作哪场会议。", "请提供会议编号，或说明会议标题和时间。"),
    ),
)


def _compose_clarification(
    *,
    provider: ModelProvider,
    issue_codes: list[str],
    request: MeetingRequest | None,
    extra_facts: list[str] | None = None,
) -> tuple[str, list[ModelCompletion]]:
    """Let Supervisor phrase verified issues; fail closed to a deterministic template."""

    contract = _clarification_contract(
        issue_codes=issue_codes,
        request=request,
        extra_facts=extra_facts,
    )
    fallback = str(contract["fallbackMessage"])
    model_request = ModelRequest(
        agent_name="supervisor",
        system_prompt=CLARIFICATION_PROMPT,
        user_prompt="CLARIFICATION_CONTRACT="
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        schema_name=ClarificationResponse.__name__,
        schema=ClarificationResponse.model_json_schema(by_alias=True),
    )
    try:
        completion_value = provider.complete(model_request)
    except ModelProviderError:
        return fallback, []
    completion = (
        completion_value
        if isinstance(completion_value, ModelCompletion)
        else ModelCompletion(content=completion_value)
    )
    completions = [completion]
    if completion.content is None:
        return fallback, completions
    try:
        response = ClarificationResponse.model_validate_json(completion.content)
    except ValueError:
        return fallback, completions
    if not _clarification_message_supported(response.message, contract):
        return fallback, completions
    return response.message, completions


def _clarification_contract(
    *,
    issue_codes: list[str],
    request: MeetingRequest | None,
    extra_facts: list[str] | None = None,
) -> dict[str, object]:
    explanations: list[str] = []
    requested_inputs: list[str] = []
    for code in issue_codes:
        guidance = _CLARIFICATION_GUIDANCE.get(code)
        if guidance is None:
            lowered = code.lower()
            guidance = next(
                (value for marker, value in _CLARIFICATION_FIELD_GUIDANCE if marker in lowered),
                (
                    "这项会议需求还不能被可靠确认。",
                    "请换一种说法补充相关信息。",
                ),
            )
        explanation, requested = guidance
        if explanation not in explanations:
            explanations.append(explanation)
        if requested not in requested_inputs:
            requested_inputs.append(requested)
    if not explanations:
        explanations.append("这项会议需求还不能被可靠确认。")
    if not requested_inputs:
        requested_inputs.append("请换一种说法补充相关信息。")
    facts = _verified_clarification_facts(request)
    facts.extend(item for item in (extra_facts or []) if item not in facts)
    fallback = "我还需要确认一点：" + "；".join(explanations[:3])
    fallback += " " + "；".join(requested_inputs[:3])
    return {
        "verifiedFacts": facts[:6],
        "explanations": explanations[:3],
        "requestedInputs": requested_inputs[:3],
        "fallbackMessage": fallback[:500],
    }


def _verified_clarification_facts(request: MeetingRequest | None) -> list[str]:
    if request is None:
        return []
    facts = [f"会议时长为{request.duration_minutes}分钟"]
    if request.time_window is not None:
        facts.append(
            "允许安排的时间范围为"
            f"{request.time_window.start.isoformat()}至{request.time_window.end.isoformat()}"
        )
    names = [
        *(["当前登录用户（我）"] if request.includes_current_user else []),
        *(item.name for item in request.required_participants),
    ]
    if names:
        facts.append("必需参会者为" + "、".join(names))
    if request.required_features:
        labels = {
            "WHITEBOARD": "白板",
            "LARGE_SCREEN": "大屏",
            "VIDEO_CONFERENCE": "视频会议设备",
            "PROJECTOR": "投影仪",
        }
        facts.append(
            "必需设备为" + "、".join(labels.get(item, item) for item in request.required_features)
        )
    return facts[:6]


def _clarification_message_supported(message: str, contract: dict[str, object]) -> bool:
    """Reject numeric/time details that were not present in the verified contract."""

    contract_text = json.dumps(contract, ensure_ascii=False)
    numeric_tokens = re.findall(r"\d+(?::\d+)?", message)
    if any(token not in contract_text for token in numeric_tokens):
        return False
    return len(message) <= 500
