"""Source-text predicates used across requirement and scheduling stages."""

from __future__ import annotations

import re

from app.agent_loop import (
    SourceFidelityEvaluator,
)
from app.schemas.agent import (
    MeetingView,
    TimeWindow,
)


def _source_changes_intent(source: str) -> bool:
    if re.search(r"(?:取消|撤销|新建会议|预约会议|找个空的会议室)", source):
        return True
    explicit_target = bool(
        _explicit_meeting_id(source)
        or re.search(r"(?:把|将).{0,40}(?:会议|评审|周会|例会|那场|那个)", source)
    )
    return explicit_target and bool(re.search(r"(?:改期|改到|调整到|重新安排)", source))


def _source_has_ambiguous_single_time(source: str) -> bool:
    match = re.search(
        r"(?:上午|早上|中午|下午|晚上)?\s*(\d{1,2})(?::(\d{2})|点)(?:开始)?",
        source,
    )
    if match is None or "点" not in match.group(0) or int(match.group(1)) > 12:
        return False
    return not any(marker in match.group(0) for marker in ("上午", "早上", "中午", "下午", "晚上"))


def _source_describes_fixed_interval(source: str) -> bool:
    match = SourceFidelityEvaluator._TIME_RANGE.search(source)
    return match is not None and not SourceFidelityEvaluator.is_search_window(source, match)


def _closes_optional_requirements(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "没别的要求",
            "没有其他要求",
            "其他没有",
            "没其他要求",
            "无其他要求",
            "没有设备要求",
            "无设备要求",
            "不需要额外设备",
        )
    )


def _source_has_soft_start_preference_only(source: str) -> bool:
    pattern = (
        r"(?:最好|尽量|优先)(?:是|在)?(?:下午|晚上|上午|早上|中午)?"
        r"\s*\d{1,2}(?::\d{2}|点)"
    )
    if not re.search(pattern, source):
        return False
    if any(marker in source for marker in ("必须", "固定", "就定", "只能")):
        return False
    return not bool(
        re.search(
            r"(?:今天|今日|明天|后天|\d{1,2}月\d{1,2}[日号]|(?<!月)(?<!\d)\d{1,2}号|"
            r"(?:下周|本周|这周|周|星期)[一二三四五六日天])",
            source,
        )
    )


def _source_has_participant_mutation(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "去掉",
            "删除",
            "移除",
            "排除",
            "不参加",
            "不会来",
            "不来",
            "请假",
            "加上",
            "增加",
            "添加",
            "邀请",
            "再叫上",
            "再加",
            "改成",
            "换成",
            "只有",
            "就这些人",
            "参会人是",
        )
    )


def _window_only_selects_target(
    window: TimeWindow, meeting: MeetingView, reference: str | None
) -> bool:
    if not reference:
        return False
    return window.start <= meeting.start_at < window.end


def _preserves_existing_requirements(message: str) -> bool:
    return any(
        marker in message
        for marker in (
            "其他都不变",
            "其它都不变",
            "要求不变",
            "设备不变",
            "保持不变",
            "默认保留",
        )
    )


def _explicit_meeting_id(source: str) -> int | None:
    match = re.search(
        r"(?:会议\s*(?:ID)?|meeting\s*id|meetingId|#)\s*[:：#]?\s*(\d{1,9})",
        source,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match is not None else None


def _is_exception_replanning(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "异常重排",
            "资源失效",
            "会议室不可用",
            "会议室已失效",
            "原会议室已失效",
            "房间不可用",
        )
    )


def _feature_removal_requested(source: str, alias: str) -> bool:
    escaped = re.escape(alias)
    return bool(
        re.search(
            rf"(?:不再要求|不需要|不要|去掉|取消)(?:使用)?\s*{escaped}|"
            rf"{escaped}\s*(?:不再要求|不需要|不要)",
            source,
        )
    )


def _source_changes_feature_constraints(source: str) -> bool:
    return any(
        alias in source
        and (
            _feature_removal_requested(source, alias)
            or not any(
                _feature_removal_requested(source, other)
                for aliases in SourceFidelityEvaluator._FEATURE_ALIASES.values()
                for other in aliases
            )
        )
        for aliases in SourceFidelityEvaluator._FEATURE_ALIASES.values()
        for alias in aliases
    )


def _source_changes_time_constraints(source: str) -> bool:
    return bool(
        re.search(
            r"(?:顺延|延后|推迟)\s*(?:30|60|90|120)\s*分钟|"
            r"(?:改到|调整到|移到|改为)",
            source,
        )
    )


def _source_changes_meeting_duration(source: str) -> bool:
    scrubbed = re.sub(
        r"(?:允许)?(?:顺延|延后|推迟)\s*(?:30|60|90|120)\s*分钟",
        "",
        source,
    )
    return bool(re.search(r"\d+\s*(?:分钟|个?小时)", scrubbed))
