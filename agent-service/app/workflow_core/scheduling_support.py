"""Scheduling read-plan, target hydration, and solver input helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from app.providers.base import (
    ModelToolCall,
)
from app.schemas.agent import (
    AgentState,
    AvailabilitySnapshot,
    BusyInterval,
    EmployeeBusySlots,
    Intent,
    MeetingRequest,
    MeetingView,
    Participant,
    RequirementSlotStatus,
    RoomAvailability,
    SchedulingPreferences,
    SchedulingProblem,
    TimeWindow,
    UnsatAnalysis,
)
from app.security import AgentContext
from app.tools.java import (
    FreeBusyInput,
    RecentMeetingInput,
    SearchRoomsInput,
)
from app.workflow_core.common import WorkflowError
from app.workflow_core.requirement_source import (
    _is_exception_replanning,
    _preserves_existing_requirements,
    _source_changes_feature_constraints,
    _source_changes_meeting_duration,
    _source_changes_time_constraints,
    _source_has_participant_mutation,
    _window_only_selects_target,
)
from app.workflow_core.requirement_validation import _requirement_items


def _participants_from_java(data: dict[str, Any]) -> list[Participant]:
    raw = data.get("employees", [])
    if not isinstance(raw, list):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
    participants: list[Participant] = []
    for employee in raw:
        if not isinstance(employee, dict):
            raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
        employee_id = employee.get("employeeId")
        display_name = employee.get("displayName")
        if (
            not isinstance(employee_id, int)
            or not isinstance(display_name, str)
            or not display_name
        ):
            raise WorkflowError("TOOL_RESPONSE_INVALID", "员工查询响应格式无效")
        participants.append(Participant(name=display_name, employee_id=employee_id))
    return participants


def _scheduling_system_prompt(*, state: AgentState, context: AgentContext) -> str:
    request = state.meeting_request
    if request is None:
        raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
    window = request.time_window
    canonical: dict[str, object] = {
        "organizerId": context.user_id,
        "intent": request.intent.value,
        "targetMeetingId": request.target_meeting_id,
        "targetMeetingReference": request.target_meeting_reference,
        "includesCurrentUser": request.includes_current_user,
        "participantNames": [item.name for item in request.required_participants],
        "participantIds": [
            item.employee_id
            for item in request.required_participants
            if item.employee_id is not None
        ],
        "from": window.start.isoformat() if window is not None else None,
        "to": window.end.isoformat() if window is not None else None,
        "requestedMinimumCapacity": request.minimum_capacity or 1,
        "requiredFeatures": request.required_features,
        "excludeMeetingId": (
            request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None
        ),
        "excludedCandidateIds": state.excluded_candidate_ids,
    }
    return (
        "You are the Scheduling Agent. Use only the supplied READ functions. Never call DRAFT "
        "or WRITE operations, never provide userId/runId/roles, and never expose reasoning. "
        "For MODIFY_MEETING or CANCEL_MEETING, call get_recent_meeting before any availability "
        "tool. After the target is uniquely hydrated, availability calls use the refreshed "
        "destination window and excludeMeetingId from CANONICAL_CONTEXT. "
        "After employee resolution, room minimumCapacity must be the maximum of "
        "requestedMinimumCapacity and the unique organizer plus resolved employee IDs.\n"
        "CANONICAL_CONTEXT=" + json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    )


def _read_facts_ready(
    request: MeetingRequest,
    free_busy_data: dict[str, Any] | None,
    rooms_data: dict[str, Any] | None,
    recent_data: dict[str, Any] | None,
) -> bool:
    if request.intent in {
        Intent.CREATE_MEETING,
        Intent.FIND_COMMON_TIME,
        Intent.RECOMMEND_ROOM,
    }:
        return free_busy_data is not None and rooms_data is not None
    if request.intent is Intent.MODIFY_MEETING:
        return recent_data is not None and free_busy_data is not None and rooms_data is not None
    if request.intent is Intent.CANCEL_MEETING:
        return request.target_meeting_id is not None or recent_data is not None
    return True


def _canonical_fact_read_call(
    *,
    name: str,
    request: MeetingRequest,
    resolved: list[Participant],
    context: AgentContext,
    index: int,
) -> ModelToolCall:
    if name == "get_recent_meeting":
        payload: FreeBusyInput | SearchRoomsInput | RecentMeetingInput = RecentMeetingInput(limit=5)
        return ModelToolCall(
            id=f"deterministic-fact-{index}-{name}",
            name=name,
            arguments=payload.model_dump_json(by_alias=True),
        )
    window = request.time_window
    if window is None:
        raise WorkflowError("TIME_WINDOW_REQUIRED", "缺少可调度时间窗口")
    employee_ids = sorted(
        {
            context.user_id,
            *(item.employee_id for item in resolved if item.employee_id is not None),
        }
    )
    exclude_meeting_id = (
        request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None
    )
    if name == "get_employee_free_busy":
        payload = FreeBusyInput(
            employee_ids=employee_ids,
            from_=window.start,
            to=window.end,
            exclude_meeting_id=exclude_meeting_id,
        )
    elif name == "search_available_rooms":
        payload = SearchRoomsInput(
            from_=window.start,
            to=window.end,
            minimum_capacity=max(request.minimum_capacity or 1, len(employee_ids)),
            required_features=request.required_features,
            limit=50,
            exclude_meeting_id=exclude_meeting_id,
        )
    else:
        raise WorkflowError("TOOL_NOT_ALLOWED", "确定性事实补全工具不在白名单")
    return ModelToolCall(
        id=f"deterministic-fact-{index}-{name}",
        name=name,
        arguments=payload.model_dump_json(by_alias=True),
    )


def _missing_read_tools(
    *,
    request: MeetingRequest,
    resolved: list[Participant],
    free_busy_data: dict[str, Any] | None,
    rooms_data: dict[str, Any] | None,
    recent_data: dict[str, Any] | None,
) -> list[str]:
    missing: list[str] = []
    expected_names = {item.name for item in request.required_participants}
    resolved_names = {item.name for item in resolved if item.employee_id is not None}
    if expected_names and not expected_names.issubset(resolved_names):
        missing.append("resolve_employees")
    if request.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING} and recent_data is None:
        missing.append("get_recent_meeting")
    if request.intent in {
        Intent.CREATE_MEETING,
        Intent.FIND_COMMON_TIME,
        Intent.RECOMMEND_ROOM,
        Intent.MODIFY_MEETING,
    }:
        if free_busy_data is None:
            missing.append("get_employee_free_busy")
        if rooms_data is None:
            missing.append("search_available_rooms")
    return missing


def _recent_meeting_id(data: dict[str, Any] | None) -> int | None:
    meetings = _recent_meetings(data)
    return meetings[0].id if len(meetings) == 1 else None


def _recent_meeting(
    data: dict[str, Any] | None, target_meeting_id: int | None
) -> MeetingView | None:
    meetings = _recent_meetings(data)
    if target_meeting_id is not None:
        return next((item for item in meetings if item.id == target_meeting_id), None)
    return meetings[0] if len(meetings) == 1 else None


def _recent_meetings(data: dict[str, Any] | None) -> list[MeetingView]:
    if data is None:
        return []
    raw = data.get("meetings", [])
    if not isinstance(raw, list):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议响应格式无效")
    try:
        meetings = [
            meeting
            for item in raw
            if (meeting := MeetingView.model_validate(item)).status == "CONFIRMED"
        ]
    except ValueError as exc:
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议响应格式无效") from exc
    return meetings


def _resolve_target_meeting(
    data: dict[str, Any] | None,
    *,
    request: MeetingRequest,
    message: str,
    request_time: datetime,
) -> tuple[list[MeetingView], list[MeetingView]]:
    meetings = _recent_meetings(data)
    if request.target_meeting_id is not None:
        return (
            [item for item in meetings if item.id == request.target_meeting_id],
            meetings,
        )
    reference = (request.target_meeting_reference or "").strip()
    selector = reference or message
    selected = list(meetings)
    target_date = _target_reference_date(selector, request_time=request_time)
    target_clock = _target_reference_clock(selector)
    if target_date is not None:
        selected = [item for item in selected if item.start_at.date() == target_date.date()]
    if target_clock is not None:
        selected = [
            item for item in selected if (item.start_at.hour, item.start_at.minute) == target_clock
        ]
    if target_date is not None or target_clock is not None:
        return selected, meetings
    title_matches = [
        item
        for item in selected
        if item.title in selector or (item.title != "会议安排" and item.title in message)
    ]
    if title_matches:
        return title_matches, meetings
    if any(marker in selector or marker in message for marker in ("刚才", "刚刚", "最近")):
        return meetings[:1], meetings
    return (meetings if len(meetings) == 1 else []), meetings


def _target_reference_date(value: str, *, request_time: datetime) -> datetime | None:
    match = re.search(
        r"(?:(?P<year>20\d{2})年)?(?:(?P<month>1[0-2]|0?[1-9])月)?"
        r"(?P<day>3[01]|[12]?\d)\s*[号日]",
        value,
    )
    if match is None:
        return None
    try:
        return request_time.replace(
            year=int(match.group("year") or request_time.year),
            month=int(match.group("month") or request_time.month),
            day=int(match.group("day")),
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None


def _target_reference_clock(value: str) -> tuple[int, int] | None:
    match = re.search(
        r"(?P<period>上午|早上|中午|下午|晚上)?\s*"
        r"(?P<hour>2[0-3]|[01]?\d|[零〇一二两三四五六七八九十]{1,3})\s*点"
        r"(?P<half>半)?",
        value,
    )
    if match is None:
        return None
    hour = _chinese_hour(match.group("hour"))
    if hour is None or hour > 23:
        return None
    period = match.group("period")
    if (period in {"下午", "晚上"} and hour < 12) or (period == "中午" and hour < 11):
        hour += 12
    elif period in {"上午", "早上"} and hour == 12:
        hour = 0
    return hour, 30 if match.group("half") else 0


def _chinese_hour(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(value) == 1:
        return digits.get(value)
    return None


def _target_meeting_clarification(
    *, matches: list[MeetingView], visible_meetings: list[MeetingView]
) -> str:
    choices = matches if len(matches) > 1 else visible_meetings
    if not choices:
        return "没有找到与该日期、时间或标题匹配且你可管理的会议，请补充会议日期、开始时间或标题。"
    lines = ["目标会议还不能唯一确定，请从以下会议中说明要操作哪一场："]
    for item in choices[:5]:
        lines.append(
            f"- 会议 {item.id}：{item.title}，"
            f"{item.start_at:%Y-%m-%d %H:%M}-{item.end_at:%H:%M}，{item.room_name}"
        )
    return "\n".join(lines)[:500]


def _hydrate_mutation_target(
    *,
    state: AgentState,
    request: MeetingRequest,
    meeting: MeetingView,
    recent_data: dict[str, Any],
) -> tuple[AgentState, MeetingRequest]:
    draft = state.requirement_draft
    original_duration = int((meeting.end_at - meeting.start_at).total_seconds() / 60)
    explicit_duration = draft.duration_minutes if draft is not None else None
    duration = explicit_duration or original_duration
    destination = request.time_window
    if draft is not None and draft.pending_start_at is not None:
        destination = TimeWindow(
            start=draft.pending_start_at,
            end=draft.pending_start_at + timedelta(minutes=duration),
        )
    elif destination is None or (
        _window_only_selects_target(destination, meeting, request.target_meeting_reference)
        and not _source_changes_time_constraints(state.message)
    ):
        destination = TimeWindow(start=meeting.start_at, end=meeting.end_at)
    required = [
        Participant(name=item.display_name, employee_id=item.employee_id)
        for item in meeting.participants
        if item.participant_type == "REQUIRED"
    ]
    preserve_existing = _preserves_existing_requirements(state.message) or (
        draft is not None and _is_exception_replanning(draft.target_meeting_reference or "")
    )
    required_features = request.required_features
    if (
        not required_features
        and preserve_existing
        and not _source_changes_feature_constraints(state.message)
    ):
        required_features = _meeting_room_features(recent_data, meeting.id)
    hydrated = request.model_copy(
        update={
            "title": meeting.title,
            "meeting_type": meeting.meeting_type,
            "duration_minutes": duration,
            "time_window": destination,
            "required_participants": required,
            "required_features": required_features,
            "minimum_capacity": max(request.minimum_capacity or 1, len(required)),
            "target_meeting_id": meeting.id,
        }
    )
    draft_update: dict[str, object] = {"target_meeting_id": meeting.id}
    if draft is not None:
        draft_update.update(
            {
                "title": meeting.title,
                "meeting_type": meeting.meeting_type,
                "duration_minutes": duration,
                "time_window": destination,
                "required_participant_names": [item.name for item in required],
                "required_features": required_features,
                "minimum_capacity": hydrated.minimum_capacity,
            }
        )
    updated_draft = draft.model_copy(update=draft_update) if draft is not None else None
    hydrated_items = state.requirement_items
    if updated_draft is not None:
        hydrated_items = _requirement_items(
            draft=updated_draft,
            request=hydrated,
            missing_fields=[],
            source=state.message,
            previous_items=state.requirement_items,
            optional_closed=state.optional_requirements_closed,
        )
        inherited_fields: set[str] = set()
        if not _source_changes_meeting_duration(state.message):
            inherited_fields.add("durationMinutes")
        if not _source_has_participant_mutation(state.message):
            inherited_fields.add("requiredParticipants")
        if (
            destination.start == meeting.start_at
            and destination.end == meeting.end_at
            and not _source_changes_time_constraints(state.message)
        ):
            inherited_fields.add("timeWindow")
        if (
            required_features
            and preserve_existing
            and not _source_changes_feature_constraints(state.message)
        ):
            inherited_fields.add("optionalRequirements")
        hydrated_items = [
            item.model_copy(
                update={
                    "status": RequirementSlotStatus.INHERITED,
                    "source": f"目标会议 {meeting.id}",
                    "blocking": False,
                }
            )
            if item.field in inherited_fields
            else item
            for item in hydrated_items
        ]
    return (
        state.model_copy(
            update={
                "meeting_request": hydrated,
                "requirement_draft": updated_draft,
                "requirement_items": hydrated_items,
            }
        ),
        hydrated,
    )


def _is_exception_replanning_context(state: AgentState) -> bool:
    if _is_exception_replanning(state.message):
        return True
    draft = state.requirement_draft
    return draft is not None and _is_exception_replanning(draft.target_meeting_reference or "")


def _meeting_room_features(data: dict[str, Any], meeting_id: int) -> list[str]:
    raw = data.get("roomFeaturesByMeetingId", {})
    if not isinstance(raw, dict):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议房间设备响应格式无效")
    features = raw.get(str(meeting_id), raw.get(meeting_id, []))
    if not isinstance(features, list) or any(not isinstance(item, str) for item in features):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "最近会议房间设备响应格式无效")
    return list(dict.fromkeys(features))


def _snapshot_from_java(
    free_busy_data: dict[str, Any], rooms_data: dict[str, Any]
) -> AvailabilitySnapshot:
    raw_busy = free_busy_data.get("employees", [])
    raw_rooms = rooms_data.get("rooms", [])
    if not isinstance(raw_busy, list) or not isinstance(raw_rooms, list):
        raise WorkflowError("TOOL_RESPONSE_INVALID", "可用性查询响应格式无效")
    try:
        employees = [
            EmployeeBusySlots(
                employee_id=item["employeeId"],
                busy_intervals=[
                    BusyInterval(
                        meeting_id=slot.get("meetingId"),
                        start_at=slot["startAt"],
                        end_at=slot["endAt"],
                    )
                    for slot in item.get("busySlots", [])
                ],
            )
            for item in raw_busy
            if isinstance(item, dict)
        ]
        if len(employees) != len(raw_busy):
            raise ValueError("busy employee item is invalid")
        rooms = [
            RoomAvailability(
                room_id=item["roomId"],
                room_name=item["roomName"],
                building=item["building"],
                capacity=item["capacity"],
                room_type=item["roomType"],
                features=item.get("features", []),
                busy_intervals=[
                    BusyInterval(
                        meeting_id=slot.get("meetingId"),
                        start_at=slot["startAt"],
                        end_at=slot["endAt"],
                    )
                    for slot in item.get("busySlots", [])
                    if isinstance(slot, dict)
                ],
            )
            for item in raw_rooms
            if isinstance(item, dict)
        ]
        if len(rooms) != len(raw_rooms):
            raise ValueError("room item is invalid")
        return AvailabilitySnapshot(rooms=rooms, employee_busy_slots=employees)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("TOOL_RESPONSE_INVALID", "可用性查询响应格式无效") from exc


def _enrich_unsat_analysis(
    analysis: UnsatAnalysis,
    *,
    resolved: list[Participant],
    organizer_id: int,
) -> UnsatAnalysis:
    names: dict[int, str] = {}
    for item in resolved:
        if item.employee_id is not None:
            names[item.employee_id] = item.name
    names.setdefault(organizer_id, "当前登录用户（我）")
    blockers = [
        blocker.model_copy(
            update={
                "resource_name": (
                    names.get(blocker.resource_id) or blocker.resource_name
                    if blocker.resource_id is not None
                    else blocker.resource_name
                )
            }
        )
        for blocker in analysis.blocking_intervals
    ]
    if not blockers:
        return analysis
    window = analysis.requested_window
    request_window = _visible_conflict_time_range(window.start, window.end)
    visible = []
    for blocker in blockers[:5]:
        label = blocker.resource_name or f"员工 {blocker.resource_id}"
        existing = f"{label}的已有安排"
        if blocker.meeting_id is not None:
            existing += f"（会议 {blocker.meeting_id}）"
        overlap_start = max(window.start, blocker.start_at)
        overlap_end = min(window.end, blocker.end_at)
        overlap = (
            _visible_conflict_time_range(overlap_start, overlap_end)
            if overlap_start < overlap_end
            else "请求窗口内无直接重叠"
        )
        visible.append(
            "本次待排请求"
            f"（{request_window}，连续 {analysis.duration_minutes} 分钟）与{existing}冲突："
            f"已有安排为 {_visible_conflict_time_range(blocker.start_at, blocker.end_at)}，"
            f"重叠时段为 {overlap}；原因：{blocker.reason}"
        )
    summary = "未找到满足全部硬约束的方案。" + "；".join(visible) + "。"
    suggestions = list(analysis.relaxation_suggestions)
    latest_end = max(blocker.end_at for blocker in blockers)
    local_day_end = latest_end.replace(hour=18, minute=0, second=0, microsecond=0)
    if latest_end + timedelta(minutes=analysis.duration_minutes) <= local_day_end:
        suggestions.insert(
            0,
            (
                f"可尝试 {latest_end:%Y-%m-%d %H:%M} 开始，"
                "回复“按你推荐的最近可行时间”后系统会重新校验全部约束。"
            ),
        )
    return analysis.model_copy(
        update={
            "summary": summary[:500],
            "blocking_intervals": blockers,
            "relaxation_suggestions": suggestions[:3],
        }
    )


def _visible_conflict_time_range(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M}-{end:%Y-%m-%d %H:%M}"


def _scheduling_problem(
    *, state: AgentState, request_required_ids: list[int], snapshot: AvailabilitySnapshot
) -> SchedulingProblem:
    request = state.meeting_request
    if request is None:
        raise WorkflowError("REQUIREMENT_MISSING", "缺少结构化会议需求")
    restricted_snapshot = snapshot
    if state.edited_draft is not None and state.edited_draft.room_id is not None:
        restricted_snapshot = AvailabilitySnapshot(
            rooms=[room for room in snapshot.rooms if room.room_id == state.edited_draft.room_id],
            employee_busy_slots=snapshot.employee_busy_slots,
        )
    try:
        return SchedulingProblem(
            meeting_request=request,
            availability_snapshot=restricted_snapshot,
            organizer_id=state.user_id,
            required_participant_ids=request_required_ids,
            optional_participant_ids=[],
            policy_constraints=state.policy_result.constraints
            if state.policy_result is not None
            else [],
            user_preferences=state.user_preferences or SchedulingPreferences(),
        )
    except ValueError as exc:
        raise WorkflowError("SCHEDULE_INPUT_INVALID", "调度输入不满足结构化约束") from exc
