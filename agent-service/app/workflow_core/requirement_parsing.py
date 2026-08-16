"""Deterministic parsing of defaults, dates, times, and participant identity."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.schemas.agent import (
    Constraint,
    Intent,
    RequirementDraft,
    TimeWindow,
)
from app.workflow_core.requirement_source import (
    _explicit_meeting_id,
    _is_exception_replanning,
    _source_has_ambiguous_single_time,
)


def _apply_explicit_meeting_defaults(
    draft: RequirementDraft, source: str, *, request_time: datetime
) -> RequirementDraft:
    updates: dict[str, object] = {}
    if _is_exception_replanning(source):
        updates["intent"] = Intent.MODIFY_MEETING
        if not draft.target_meeting_reference:
            updates["target_meeting_reference"] = source[:240]
    explicit_meeting_id = _explicit_meeting_id(source)
    if explicit_meeting_id is not None and (
        draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        or _is_exception_replanning(source)
    ):
        updates["target_meeting_id"] = explicit_meeting_id
    normalized_source = _normalize_chinese_clock_tokens(source)
    time_source = normalized_source
    if draft.intent is Intent.MODIFY_MEETING:
        mutation = re.search(r"改到|调整到|移到|改为", source)
        if mutation is not None:
            selector_source = source[: mutation.start()].strip()
            destination_source = source[mutation.end() :].strip()
            if selector_source:
                updates["target_meeting_reference"] = selector_source[-240:]
            normalized_selector = _normalize_chinese_clock_tokens(selector_source)
            normalized_destination = _normalize_chinese_clock_tokens(destination_source)
            if any(marker in destination_source for marker in ("同一时间", "原时间", "同样时间")):
                selector_clock = re.search(
                    r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
                    normalized_selector,
                )
                if selector_clock is not None:
                    normalized_destination = normalized_destination + " " + selector_clock.group(0)
            time_source = normalized_destination
    if "架构评审" in source:
        if not draft.title:
            updates["title"] = "架构评审"
        if not draft.meeting_type:
            updates["meeting_type"] = "ARCHITECTURE_REVIEW"
    if (
        draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        and draft.target_meeting_id is None
        and not draft.target_meeting_reference
    ):
        for reference in ("刚才那个架构评审", "刚才那个会议", "刚才那个", "最近的会议"):
            if reference in source:
                updates["target_meeting_reference"] = reference
                break
    if any(
        value in source for value in ("我的小组", "同组人员", "小组会议", "组内人员", "组内的人")
    ):
        updates["participant_scope"] = "MY_DEPARTMENT"
    elif (
        any(value in source for value in ("只有我", "我自己参加", "就我一个人", "我必须参加"))
        and not draft.required_participant_names
    ):
        updates["participant_scope"] = "ORGANIZER_ONLY"

    duration_match = re.search(r"(30|60|90|120|150|180|210|240)\s*分钟", source)
    english_duration = re.search(r"(30|60|90|120|150|180|210|240)[ -]minute", source, re.IGNORECASE)
    if duration_match is not None:
        updates["duration_minutes"] = int(duration_match.group(1))
    elif english_duration is not None:
        updates["duration_minutes"] = int(english_duration.group(1))
    elif "一个半小时" in source:
        updates["duration_minutes"] = 90
    elif "半小时" in source:
        updates["duration_minutes"] = 30
    elif "一小时" in source or "一个小时" in source:
        updates["duration_minutes"] = 60
    elif "两小时" in source or "两个小时" in source:
        updates["duration_minutes"] = 120

    headcount = re.search(r"(\d{1,4})\s*(?:个)?人", source)
    english_headcount = re.search(r"(\d{1,4})\s*people", source, re.IGNORECASE)
    if headcount is not None:
        updates["minimum_capacity"] = int(headcount.group(1))
    elif english_headcount is not None:
        updates["minimum_capacity"] = int(english_headcount.group(1))

    feature_aliases = {
        "白板": "WHITEBOARD",
        "大屏": "LARGE_SCREEN",
        "投屏": "LARGE_SCREEN",
        "视频会议": "VIDEO_CONFERENCE",
        "投影仪": "PROJECTOR",
        "whiteboard": "WHITEBOARD",
        "large screen": "LARGE_SCREEN",
        "video conference": "VIDEO_CONFERENCE",
        "projector": "PROJECTOR",
    }
    normalized_lower = source.lower()
    explicitly_closed = any(
        value in source for value in ("无设备要求", "不需要额外设备", "没有设备要求")
    )
    explicit_features = [
        canonical
        for alias, canonical in feature_aliases.items()
        if alias.lower() in normalized_lower
    ]
    features = (
        []
        if explicitly_closed
        else list(
            dict.fromkeys(
                [
                    *(feature_aliases.get(item.lower(), item) for item in draft.required_features),
                    *explicit_features,
                ]
            )
        )
    )
    if "投屏" in source and "投影仪" not in source:
        # DeepSeek sometimes expands “投屏” to both PROJECTOR and
        # LARGE_SCREEN.  The frozen product vocabulary maps it to the latter;
        # keep an explicitly requested 投影仪 distinct.
        features = [item for item in features if item != "PROJECTOR"]
        if "LARGE_SCREEN" not in features:
            features.append("LARGE_SCREEN")
    if features != draft.required_features:
        updates["required_features"] = features

    preferred = re.search(
        r"(?:最好|尽量|优先)(?:是|在)?(?:下午|晚上|上午|早上|中午)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
        normalized_source,
    )
    if preferred is not None:
        hour = int(preferred.group(1))
        minute = int(preferred.group(2) or 0)
        if any(value in preferred.group(0) for value in ("下午", "晚上")) and hour < 12:
            hour += 12
        soft = [item for item in draft.soft_constraints if item.type != "PREFER_START_AT"]
        soft.append(Constraint(type="PREFER_START_AT", value=f"{hour:02d}:{minute:02d}", weight=20))
        updates["soft_constraints"] = soft

    target_date = _deterministic_target_date(time_source, request_time)
    daypart = _daypart_window(time_source)
    has_explicit_time_context = target_date is not None or daypart is not None
    explicit_range = re.search(
        r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)\s*"
        r"(?:到|至|-)\s*(\d{1,2})(?::(\d{2})|点)",
        time_source,
    )
    explicit_single = re.search(
        r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
        time_source,
    )
    has_preferred_only = preferred is not None and not any(
        marker in source for marker in ("必须", "固定", "就定", "只能")
    )
    if has_preferred_only and not has_explicit_time_context:
        # A soft start preference must not become a new hard time window.  In
        # continuation turns this also removes provider-invented time fields so
        # the merge below retains the already confirmed/defaulted date window.
        updates["time_window"] = None
        updates["pending_start_at"] = None
        updates["pending_start_ambiguous"] = False
    if target_date is None and (
        daypart is not None or explicit_range is not None or explicit_single is not None
    ):
        target_date = request_time.date()
    try:
        if (
            target_date is not None
            and explicit_single is not None
            and _source_has_ambiguous_single_time(time_source)
            and not has_preferred_only
        ):
            start = _at_local_date(
                request_time,
                target_date,
                int(explicit_single.group(1)),
                int(explicit_single.group(2) or 0),
            )
            updates["pending_start_at"] = start
            updates["pending_start_ambiguous"] = True
            updates["time_window"] = None
        elif (
            target_date is not None
            and explicit_range is not None
            and not _source_has_ambiguous_single_time(time_source)
        ):
            start_hour = int(explicit_range.group(1))
            start_minute = int(explicit_range.group(2) or 0)
            end_hour = int(explicit_range.group(3))
            end_minute = int(explicit_range.group(4) or 0)
            marker = explicit_range.group(0)
            if any(value in marker for value in ("下午", "晚上")):
                if start_hour < 12:
                    start_hour += 12
                if end_hour < 12:
                    end_hour += 12
            start = _at_local_date(request_time, target_date, start_hour, start_minute)
            end = _at_local_date(request_time, target_date, end_hour, end_minute)
            if end <= start and "晚上" in marker:
                end += timedelta(days=1)
            updates["time_window"] = TimeWindow(start=start, end=end)
        elif (
            target_date is not None
            and explicit_single is not None
            and not has_preferred_only
            and not _source_has_ambiguous_single_time(time_source)
        ):
            hour = int(explicit_single.group(1))
            minute = int(explicit_single.group(2) or 0)
            marker = explicit_single.group(0)
            if any(value in marker for value in ("下午", "晚上")) and hour < 12:
                hour += 12
            start = _at_local_date(request_time, target_date, hour, minute)
            if draft.duration_minutes is None:
                updates["pending_start_at"] = start
                updates["pending_start_ambiguous"] = False
                updates["time_window"] = None
            else:
                updates["time_window"] = TimeWindow(
                    start=start, end=start + timedelta(minutes=draft.duration_minutes)
                )
                updates["pending_start_at"] = None
                updates["pending_start_ambiguous"] = False
        elif target_date is not None and daypart is not None:
            start_hour, end_hour, crosses_midnight = daypart
            start = _at_local_date(request_time, target_date, start_hour, 0)
            end = _at_local_date(request_time, target_date, end_hour, 0)
            if crosses_midnight:
                end += timedelta(days=1)
            updates["time_window"] = TimeWindow(start=start, end=end)
    except ValueError:
        updates.pop("time_window", None)
    return draft.model_copy(update=updates) if updates else draft


def _apply_current_user_participation(draft: RequirementDraft, source: str) -> RequirementDraft:
    """Resolve first-person attendance from authenticated context, not model-supplied identity."""

    if draft.intent not in {
        Intent.CREATE_MEETING,
        Intent.FIND_COMMON_TIME,
        Intent.RECOMMEND_ROOM,
    }:
        return draft
    normalized = re.sub(r"\s+", "", source)
    attends = bool(
        re.search(r"我(?:和|跟|与)|(?:和|跟|与)我", normalized)
        or re.search(r"(?:包括我|我(?:本人|也)?(?:必须|需要|要)?参加)", normalized)
        or re.search(r"(?:^|[，,、])我(?:[，,、]|$)", normalized)
    )
    return draft.model_copy(update={"includes_current_user": attends})


def _normalize_chinese_clock_tokens(source: str) -> str:
    values = {
        "一": "1",
        "两": "2",
        "二": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
        "十": "10",
        "十一": "11",
        "十二": "12",
    }
    return re.sub(
        r"(十二|十一|十|[一二两三四五六七八九])(?=点)",
        lambda match: values[match.group(1)],
        source,
    )


def _deterministic_target_date(source: str, request_time: datetime) -> Any:
    compact = re.sub(r"\s+", "", source)
    try:
        iso_date = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", compact)
        if iso_date is not None:
            return request_time.date().replace(
                year=int(iso_date.group(1)),
                month=int(iso_date.group(2)),
                day=int(iso_date.group(3)),
            )
        if "今天" in compact or "今日" in compact:
            return request_time.date()
        if "明天" in compact:
            return (request_time + timedelta(days=1)).date()
        if "后天" in compact:
            return (request_time + timedelta(days=2)).date()
        absolute = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", compact)
        if absolute is not None:
            return request_time.date().replace(
                year=int(absolute.group(1)),
                month=int(absolute.group(2)),
                day=int(absolute.group(3)),
            )
        month_day = re.search(r"(\d{1,2})月(\d{1,2})[日号]", compact)
        if month_day is not None:
            return request_time.date().replace(
                month=int(month_day.group(1)), day=int(month_day.group(2))
            )
        day_only = re.search(r"(?<!月)(?<!\d)(\d{1,2})号", compact)
        if day_only is not None:
            return request_time.date().replace(day=int(day_only.group(1)))
    except ValueError:
        return None
    weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    weekday = re.search(
        r"(下周|下星期|本周|这周|本星期|这星期|周|星期)([一二三四五六日天])",
        compact,
    )
    if weekday is not None:
        target = weekdays[weekday.group(2)]
        week_start = request_time.date() - timedelta(days=request_time.weekday())
        if weekday.group(1) in {"下周", "下星期"}:
            week_start += timedelta(days=7)
        return week_start + timedelta(days=target)
    return None


def _daypart_window(source: str) -> tuple[int, int, bool] | None:
    normalized = source.lower()
    if "晚上" in source or "evening" in normalized:
        return 18, 6, True
    if "下午" in source or "afternoon" in normalized:
        return 12, 18, False
    if "中午" in source or "noon" in normalized:
        return 11, 14, False
    if "上午" in source or "早上" in source or "morning" in normalized:
        return 6, 12, False
    return None


def _at_local_date(request_time: datetime, target_date: Any, hour: int, minute: int) -> datetime:
    return request_time.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def _resolve_ambiguous_pending_start(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    source: str,
) -> RequirementDraft:
    if (
        previous is None
        or previous.pending_start_at is None
        or not previous.pending_start_ambiguous
    ):
        return current
    daypart = _daypart_window(source)
    if daypart is None:
        return current
    start = previous.pending_start_at
    hour = start.hour
    if any(marker in source for marker in ("下午", "晚上", "中午")) and hour < 12:
        hour += 12
    resolved_start = start.replace(hour=hour)
    duration = current.duration_minutes or previous.duration_minutes
    if duration is None:
        return current.model_copy(
            update={
                "time_window": None,
                "pending_start_at": resolved_start,
                "pending_start_ambiguous": False,
            }
        )
    return current.model_copy(
        update={
            "time_window": TimeWindow(
                start=resolved_start,
                end=resolved_start + timedelta(minutes=duration),
            ),
            "pending_start_at": None,
            "pending_start_ambiguous": False,
        }
    )
