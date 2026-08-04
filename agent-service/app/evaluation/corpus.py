"""Versioned, deterministic Day 7 Agent evaluation corpus (40 cases)."""

from __future__ import annotations

from datetime import datetime

from app.evaluation.models import (
    ConstraintExpectation,
    EvaluationCase,
    EvaluationCategory,
    EvaluationContext,
)
from app.schemas.agent import Intent, RunStatus

_CONTEXT = EvaluationContext(
    now=datetime.fromisoformat("2026-08-11T10:00:00+08:00"),
    user_id=1001,
)
_SCHEDULING_TOOLS = [
    "resolve_employees",
    "get_employee_free_busy",
    "search_available_rooms",
    "create_booking_draft",
]
_NO_DIRECT_WRITE = ["confirm_booking"]
_READ_EMPLOYEES = ["resolve_employees"]

EXPECTED_CATEGORY_COUNTS: dict[EvaluationCategory, int] = {
    EvaluationCategory.NORMAL_BOOKING: 8,
    EvaluationCategory.MULTI_PARTY_COORDINATION: 6,
    EvaluationCategory.COMPLEX_CONSTRAINT: 6,
    EvaluationCategory.RECOMMENDATION_OR_CONFLICT: 5,
    EvaluationCategory.POLICY: 5,
    EvaluationCategory.MODIFY_OR_CANCEL: 6,
    EvaluationCategory.PREFERENCE_OR_CLARIFICATION: 4,
}


def _constraints(
    *,
    duration: int | None = None,
    capacity: int | None = None,
    features: list[str] | None = None,
    participants: list[str] | None = None,
    target_meeting_id: int | None = None,
    missing_fields: list[str] | None = None,
) -> ConstraintExpectation:
    return ConstraintExpectation(
        duration_minutes=duration,
        minimum_capacity=capacity,
        required_features=features,
        required_participant_names=participants,
        target_meeting_id=target_meeting_id,
        missing_fields=missing_fields,
    )


def _case(
    case_id: str,
    category: EvaluationCategory,
    input_text: str,
    expected_intent: Intent,
    constraints: ConstraintExpectation,
    *,
    expected_tools: list[str],
    status: RunStatus,
    citations: list[str] | None = None,
    validate_schedule: bool = False,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        category=category,
        input=input_text,
        context=_CONTEXT,
        expected_intent=expected_intent,
        expected_constraints=constraints,
        expected_tools=expected_tools,
        forbidden_tools=_NO_DIRECT_WRITE,
        expected_terminal_status=status,
        expected_citation_ids=citations or [],
        validate_schedule=validate_schedule,
    )


def _schedule(
    case_id: str,
    category: EvaluationCategory,
    input_text: str,
    constraints: ConstraintExpectation,
) -> EvaluationCase:
    return _case(
        case_id,
        category,
        input_text,
        Intent.CREATE_MEETING,
        constraints,
        expected_tools=_SCHEDULING_TOOLS,
        status=RunStatus.WAITING_CONFIRMATION,
        validate_schedule=True,
    )


CASES: tuple[EvaluationCase, ...] = (
    # 8 normal bookings
    _schedule(
        "normal-001",
        EvaluationCategory.NORMAL_BOOKING,
        "下周三下午帮张三安排一个60分钟架构评审，6人，要大屏。",
        _constraints(
            duration=60,
            capacity=6,
            features=["LARGE_SCREEN"],
            participants=["张三"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-002",
        EvaluationCategory.NORMAL_BOOKING,
        "明天上午帮李四安排30分钟会议，4人，要白板。",
        _constraints(
            duration=30,
            capacity=4,
            features=["WHITEBOARD"],
            participants=["李四"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-003",
        EvaluationCategory.NORMAL_BOOKING,
        "下周三下午帮张三和李四安排90分钟会议，8人，要视频会议设备。",
        _constraints(
            duration=90,
            capacity=8,
            features=["VIDEO_CONFERENCE"],
            participants=["张三", "李四"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-004",
        EvaluationCategory.NORMAL_BOOKING,
        "明天上午帮王经理安排60分钟会议，5人，要白板。",
        _constraints(
            duration=60,
            capacity=5,
            features=["WHITEBOARD"],
            participants=["王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-005",
        EvaluationCategory.NORMAL_BOOKING,
        "下周三下午帮张三安排120分钟架构评审，12人，要大屏和视频会议设备。",
        _constraints(
            duration=120,
            capacity=12,
            features=["LARGE_SCREEN", "VIDEO_CONFERENCE"],
            participants=["张三"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-006",
        EvaluationCategory.NORMAL_BOOKING,
        "明天下午帮张三安排30分钟会议，3人。",
        _constraints(
            duration=30,
            capacity=3,
            features=[],
            participants=["张三"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-007",
        EvaluationCategory.NORMAL_BOOKING,
        "下周三下午帮李四和王经理安排90分钟会议，10人，要大屏。",
        _constraints(
            duration=90,
            capacity=10,
            features=["LARGE_SCREEN"],
            participants=["李四", "王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "normal-008",
        EvaluationCategory.NORMAL_BOOKING,
        "明天上午帮张三和李四安排60分钟会议，7人，要白板。",
        _constraints(
            duration=60,
            capacity=7,
            features=["WHITEBOARD"],
            participants=["张三", "李四"],
            missing_fields=[],
        ),
    ),
    # 6 multi-party coordination cases
    _schedule(
        "coord-001",
        EvaluationCategory.MULTI_PARTY_COORDINATION,
        "下周三下午协调张三、李四和王经理开60分钟会议，6人，要白板。",
        _constraints(
            duration=60,
            capacity=6,
            features=["WHITEBOARD"],
            participants=["张三", "李四", "王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "coord-002",
        EvaluationCategory.MULTI_PARTY_COORDINATION,
        "明天下午协调张三、李四和王经理开90分钟会议，12人，要视频会议设备。",
        _constraints(
            duration=90,
            capacity=12,
            features=["VIDEO_CONFERENCE"],
            participants=["张三", "李四", "王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "coord-003",
        EvaluationCategory.MULTI_PARTY_COORDINATION,
        "下周三上午帮张三和李四协调一个30分钟站会，5人。",
        _constraints(
            duration=30,
            capacity=5,
            features=[],
            participants=["张三", "李四"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "coord-004",
        EvaluationCategory.MULTI_PARTY_COORDINATION,
        "明天上午让张三、李四、王经理参加120分钟架构评审，15人，要大屏。",
        _constraints(
            duration=120,
            capacity=15,
            features=["LARGE_SCREEN"],
            participants=["张三", "李四", "王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "coord-005",
        EvaluationCategory.MULTI_PARTY_COORDINATION,
        "下周三下午帮张三和王经理协调60分钟项目会，8人，要白板和视频会议设备。",
        _constraints(
            duration=60,
            capacity=8,
            features=["WHITEBOARD", "VIDEO_CONFERENCE"],
            participants=["张三", "王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "coord-006",
        EvaluationCategory.MULTI_PARTY_COORDINATION,
        "明天下午帮李四和王经理协调90分钟客户会，9人，要大屏。",
        _constraints(
            duration=90,
            capacity=9,
            features=["LARGE_SCREEN"],
            participants=["李四", "王经理"],
            missing_fields=[],
        ),
    ),
    # 6 complex hard-constraint cases
    _schedule(
        "complex-001",
        EvaluationCategory.COMPLEX_CONSTRAINT,
        "下周三下午帮张三安排90分钟架构评审，20人，要大屏、白板和视频会议设备。",
        _constraints(
            duration=90,
            capacity=20,
            features=["LARGE_SCREEN", "WHITEBOARD", "VIDEO_CONFERENCE"],
            participants=["张三"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "complex-002",
        EvaluationCategory.COMPLEX_CONSTRAINT,
        "明天下午帮李四安排120分钟会议，18人，要视频会议设备和白板。",
        _constraints(
            duration=120,
            capacity=18,
            features=["WHITEBOARD", "VIDEO_CONFERENCE"],
            participants=["李四"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "complex-003",
        EvaluationCategory.COMPLEX_CONSTRAINT,
        "下周三上午帮王经理安排30分钟紧急会议，2人，要大屏。",
        _constraints(
            duration=30,
            capacity=2,
            features=["LARGE_SCREEN"],
            participants=["王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "complex-004",
        EvaluationCategory.COMPLEX_CONSTRAINT,
        "明天下午帮张三和李四安排120分钟设计评审，16人，要大屏和白板。",
        _constraints(
            duration=120,
            capacity=16,
            features=["LARGE_SCREEN", "WHITEBOARD"],
            participants=["张三", "李四"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "complex-005",
        EvaluationCategory.COMPLEX_CONSTRAINT,
        "下周三下午帮张三、李四和王经理安排90分钟委员会会议，30人，要视频会议设备。",
        _constraints(
            duration=90,
            capacity=30,
            features=["VIDEO_CONFERENCE"],
            participants=["张三", "李四", "王经理"],
            missing_fields=[],
        ),
    ),
    _schedule(
        "complex-006",
        EvaluationCategory.COMPLEX_CONSTRAINT,
        "明天上午帮李四安排60分钟演示会，11人，要大屏、白板和视频会议设备。",
        _constraints(
            duration=60,
            capacity=11,
            features=["LARGE_SCREEN", "WHITEBOARD", "VIDEO_CONFERENCE"],
            participants=["李四"],
            missing_fields=[],
        ),
    ),
    # 5 recommendation / conflict cases
    _case(
        "recommend-001",
        EvaluationCategory.RECOMMENDATION_OR_CONFLICT,
        "推荐一个可容纳8人的带大屏会议室，参会人张三。",
        Intent.RECOMMEND_ROOM,
        _constraints(
            duration=60,
            capacity=8,
            features=["LARGE_SCREEN"],
            participants=["张三"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "recommend-002",
        EvaluationCategory.RECOMMENDATION_OR_CONFLICT,
        "帮张三和李四找一个90分钟的共同空闲时间，8人。",
        Intent.FIND_COMMON_TIME,
        _constraints(
            duration=90,
            capacity=8,
            features=[],
            participants=["张三", "李四"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "recommend-003",
        EvaluationCategory.RECOMMENDATION_OR_CONFLICT,
        "如果研发楼没有空房，推荐一间带白板的会议室给李四，6人。",
        Intent.RECOMMEND_ROOM,
        _constraints(
            duration=60,
            capacity=6,
            features=["WHITEBOARD"],
            participants=["李四"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "recommend-004",
        EvaluationCategory.RECOMMENDATION_OR_CONFLICT,
        "张三和王经理明天下午可能冲突，找60分钟共同空闲时间，5人。",
        Intent.FIND_COMMON_TIME,
        _constraints(
            duration=60,
            capacity=5,
            features=[],
            participants=["张三", "王经理"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "recommend-005",
        EvaluationCategory.RECOMMENDATION_OR_CONFLICT,
        "张三的会议室被占用时，推荐可用的大屏房间，6人。",
        Intent.RECOMMEND_ROOM,
        _constraints(
            duration=60,
            capacity=6,
            features=["LARGE_SCREEN"],
            participants=["张三"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    # 5 policy questions with citation targets
    _case(
        "policy-001",
        EvaluationCategory.POLICY,
        "VIP会议室有什么使用规则？",
        Intent.QUERY_POLICY,
        _constraints(),
        expected_tools=[],
        status=RunStatus.SUCCEEDED,
        citations=["chunk_vip_room_v1"],
    ),
    _case(
        "policy-002",
        EvaluationCategory.POLICY,
        "架构评审的会议室设备规则是什么？",
        Intent.QUERY_POLICY,
        _constraints(),
        expected_tools=[],
        status=RunStatus.SUCCEEDED,
        citations=["chunk_architecture_review_v1"],
    ),
    _case(
        "policy-003",
        EvaluationCategory.POLICY,
        "VIP客户会议能直接使用VIP会议室吗？",
        Intent.QUERY_POLICY,
        _constraints(),
        expected_tools=[],
        status=RunStatus.SUCCEEDED,
        citations=["chunk_vip_room_v1"],
    ),
    _case(
        "policy-004",
        EvaluationCategory.POLICY,
        "请查询架构评审的制度和大屏要求。",
        Intent.QUERY_POLICY,
        _constraints(),
        expected_tools=[],
        status=RunStatus.SUCCEEDED,
        citations=["chunk_architecture_review_v1"],
    ),
    _case(
        "policy-005",
        EvaluationCategory.POLICY,
        "VIP会议室预约前有哪些审批规则？",
        Intent.QUERY_POLICY,
        _constraints(),
        expected_tools=[],
        status=RunStatus.SUCCEEDED,
        citations=["chunk_vip_room_v1"],
    ),
    # 6 modify / cancel cases
    _case(
        "change-001",
        EvaluationCategory.MODIFY_OR_CANCEL,
        "将会议101改期到明天下午，张三参加，4人，要白板。",
        Intent.MODIFY_MEETING,
        _constraints(
            duration=60,
            capacity=4,
            features=["WHITEBOARD"],
            participants=["张三"],
            target_meeting_id=101,
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "change-002",
        EvaluationCategory.MODIFY_OR_CANCEL,
        "修改会议202为90分钟，李四参加，8人，要视频会议设备。",
        Intent.MODIFY_MEETING,
        _constraints(
            duration=90,
            capacity=8,
            features=["VIDEO_CONFERENCE"],
            participants=["李四"],
            target_meeting_id=202,
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "change-003",
        EvaluationCategory.MODIFY_OR_CANCEL,
        "调整会议 ID 303 到下周三上午，王经理参加，6人，要大屏。",
        Intent.MODIFY_MEETING,
        _constraints(
            duration=60,
            capacity=6,
            features=["LARGE_SCREEN"],
            participants=["王经理"],
            target_meeting_id=303,
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "change-004",
        EvaluationCategory.MODIFY_OR_CANCEL,
        "取消会议401，张三参加。",
        Intent.CANCEL_MEETING,
        _constraints(
            duration=60,
            capacity=1,
            features=[],
            participants=["张三"],
            target_meeting_id=401,
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "change-005",
        EvaluationCategory.MODIFY_OR_CANCEL,
        "请取消会议 ID 502，李四参加。",
        Intent.CANCEL_MEETING,
        _constraints(
            duration=60,
            capacity=1,
            features=[],
            participants=["李四"],
            target_meeting_id=502,
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "change-006",
        EvaluationCategory.MODIFY_OR_CANCEL,
        "取消会议603，王经理参加。",
        Intent.CANCEL_MEETING,
        _constraints(
            duration=60,
            capacity=1,
            features=[],
            participants=["王经理"],
            target_meeting_id=603,
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    # 4 preference / clarification cases
    _case(
        "preference-001",
        EvaluationCategory.PREFERENCE_OR_CLARIFICATION,
        "以后请优先安排在研发楼，张三参与。",
        Intent.UPDATE_PREFERENCE,
        _constraints(
            duration=60,
            capacity=1,
            features=[],
            participants=["张三"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "preference-002",
        EvaluationCategory.PREFERENCE_OR_CLARIFICATION,
        "以后避免周一上午安排会议，李四参与。",
        Intent.UPDATE_PREFERENCE,
        _constraints(
            duration=60,
            capacity=1,
            features=[],
            participants=["李四"],
            missing_fields=[],
        ),
        expected_tools=_READ_EMPLOYEES,
        status=RunStatus.SUCCEEDED,
    ),
    _case(
        "preference-003",
        EvaluationCategory.PREFERENCE_OR_CLARIFICATION,
        "明天下午安排一个60分钟会议。",
        Intent.CREATE_MEETING,
        _constraints(
            duration=60,
            capacity=1,
            features=[],
            participants=[],
            missing_fields=["requiredParticipants"],
        ),
        expected_tools=[],
        status=RunStatus.WAITING_USER_INPUT,
    ),
    _case(
        "preference-004",
        EvaluationCategory.PREFERENCE_OR_CLARIFICATION,
        "帮我推荐一个8人带白板的会议室。",
        Intent.RECOMMEND_ROOM,
        _constraints(
            duration=60,
            capacity=8,
            features=["WHITEBOARD"],
            participants=[],
            missing_fields=["requiredParticipants"],
        ),
        expected_tools=[],
        status=RunStatus.WAITING_USER_INPUT,
    ),
)


def load_day7_cases() -> tuple[EvaluationCase, ...]:
    """Return the immutable, versioned corpus after invariant checks."""

    validate_day7_corpus(CASES)
    return CASES


def validate_day7_corpus(cases: tuple[EvaluationCase, ...]) -> None:
    """Fail closed when a corpus edit breaks the documented Day 7 shape."""

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Day 7 evaluation corpus has duplicate case IDs")
    if len(cases) != 40:
        raise ValueError(f"Day 7 evaluation corpus must contain 40 cases, got {len(cases)}")
    counts = {category: 0 for category in EXPECTED_CATEGORY_COUNTS}
    for case in cases:
        counts[case.category] = counts.get(case.category, 0) + 1
        if case.expected_intent is Intent.QUERY_POLICY and not case.expected_citation_ids:
            raise ValueError(f"{case.case_id} is a policy case without an expected citation")
        if case.expected_intent is not Intent.QUERY_POLICY and case.expected_citation_ids:
            raise ValueError(f"{case.case_id} has citations outside the policy route")
    if counts != EXPECTED_CATEGORY_COUNTS:
        raise ValueError(f"Day 7 corpus distribution mismatch: {counts}")
