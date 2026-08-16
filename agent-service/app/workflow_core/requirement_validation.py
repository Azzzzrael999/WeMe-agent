"""Requirement-slot completeness evaluation and clarification formatting."""

from __future__ import annotations

import re

from app.agent_loop import (
    RequirementFeedback,
)
from app.schemas.agent import (
    MeetingRequest,
    RequirementDraft,
    RequirementItem,
    RequirementSlotStatus,
)
from app.workflow_core.requirement_parsing import _daypart_window
from app.workflow_core.requirement_source import (
    _source_changes_feature_constraints,
    _source_changes_meeting_duration,
    _source_changes_time_constraints,
    _source_has_ambiguous_single_time,
    _source_has_participant_mutation,
    _source_has_soft_start_preference_only,
)


def _requirement_feedback(code: str, summary: str) -> RequirementFeedback:
    return RequirementFeedback(codes=[code], summary=summary, repairable=False)


def _previous_requirement_item(
    items: list[RequirementItem], field_name: str
) -> RequirementItem | None:
    return next((item for item in items if item.field == field_name), None)


def _has_requirement_issue(missing_fields: list[str], field_name: str) -> bool:
    prefixes = {
        "timeWindow": ("TIME_", "WINDOW_"),
        "durationMinutes": ("DURATION_",),
        "requiredParticipants": ("PARTICIPANT_", "REQUIRED_PARTICIPANT_"),
    }.get(field_name, ())
    return field_name in missing_fields or any(code.startswith(prefixes) for code in missing_fields)


def _requirement_items(
    *,
    draft: RequirementDraft,
    request: MeetingRequest,
    missing_fields: list[str],
    source: str,
    previous_items: list[RequirementItem],
    optional_closed: bool,
) -> list[RequirementItem]:
    items: list[RequirementItem] = []
    previous_time = _previous_requirement_item(previous_items, "timeWindow")
    time_issue = _has_requirement_issue(missing_fields, "timeWindow")
    if draft.time_window is None and draft.pending_start_at is not None:
        rule = _time_rule_id(source)
        items.append(
            RequirementItem(
                field="timeWindow",
                status=(
                    RequirementSlotStatus.AMBIGUOUS
                    if draft.pending_start_ambiguous
                    else RequirementSlotStatus.DEFAULTED
                    if rule is not None
                    else RequirementSlotStatus.EXPLICIT
                ),
                summary=(
                    f"{draft.pending_start_at.strftime('%Y-%m-%d')} 的"
                    f" {draft.pending_start_at.strftime('%H:%M')}，请确认上午或下午"
                    if draft.pending_start_ambiguous
                    else f"{draft.pending_start_at.strftime('%Y-%m-%d %H:%M')} 开始"
                ),
                source=source,
                rule_id=rule,
                blocking=draft.pending_start_ambiguous,
            )
        )
    elif draft.time_window is None:
        ambiguous_time = _source_has_ambiguous_single_time(source)
        items.append(
            RequirementItem(
                field="timeWindow",
                status=(
                    RequirementSlotStatus.AMBIGUOUS
                    if ambiguous_time
                    else RequirementSlotStatus.MISSING
                ),
                summary=(
                    "请说明是上午还是下午，并确认允许安排的时间段"
                    if ambiguous_time
                    else "待补充日期和允许安排的时间段"
                ),
                blocking=True,
            )
        )
    else:
        soft_preference_only = _source_has_soft_start_preference_only(source)
        rule = None if soft_preference_only else _time_rule_id(source)
        status = (
            RequirementSlotStatus.CONFLICT
            if time_issue
            else RequirementSlotStatus.EXPLICIT
            if _source_changes_time_constraints(source)
            else previous_time.status
            if soft_preference_only and previous_time is not None
            else RequirementSlotStatus.DEFAULTED
            if rule is not None
            else previous_time.status
            if previous_time is not None
            else RequirementSlotStatus.EXPLICIT
        )
        items.append(
            RequirementItem(
                field="timeWindow",
                status=status,
                summary=(
                    f"{draft.time_window.start.strftime('%Y-%m-%d %H:%M')} 至 "
                    f"{draft.time_window.end.strftime('%Y-%m-%d %H:%M')}"
                ),
                source=(
                    source
                    if rule is not None
                    else previous_time.source
                    if previous_time
                    else source
                ),
                rule_id=rule or (previous_time.rule_id if previous_time else None),
                blocking=time_issue,
            )
        )
    previous_duration = _previous_requirement_item(previous_items, "durationMinutes")
    duration_issue = _has_requirement_issue(missing_fields, "durationMinutes")
    if draft.duration_minutes is None:
        items.append(
            RequirementItem(
                field="durationMinutes",
                status=RequirementSlotStatus.MISSING,
                summary="待补充会议时长",
                blocking=True,
            )
        )
    else:
        duration_is_current = _source_changes_meeting_duration(source)
        items.append(
            RequirementItem(
                field="durationMinutes",
                status=(
                    RequirementSlotStatus.CONFLICT
                    if duration_issue
                    else RequirementSlotStatus.EXPLICIT
                    if duration_is_current
                    else previous_duration.status
                    if previous_duration
                    else RequirementSlotStatus.EXPLICIT
                ),
                summary=f"{draft.duration_minutes}分钟",
                source=(
                    source
                    if re.search(r"(?:分钟|小时)", source)
                    else previous_duration.source
                    if previous_duration
                    else source
                ),
                blocking=duration_issue,
            )
        )
    previous_participants = _previous_requirement_item(previous_items, "requiredParticipants")
    if (
        not draft.required_participant_names
        and draft.participant_scope != "ORGANIZER_ONLY"
        and not draft.includes_current_user
    ):
        items.append(
            RequirementItem(
                field="requiredParticipants",
                status=RequirementSlotStatus.MISSING,
                summary="待补充必需参会人员或人员范围",
                blocking=True,
            )
        )
    elif draft.participant_scope == "ORGANIZER_ONLY" or (
        draft.includes_current_user and not draft.required_participant_names
    ):
        items.append(
            RequirementItem(
                field="requiredParticipants",
                status=RequirementSlotStatus.EXPLICIT,
                summary="仅当前登录用户（我）",
                source=source,
            )
        )
    else:
        directory = (
            draft.participant_scope == "MY_DEPARTMENT" and not draft.participant_list_modified
        )
        participant_changed = _source_has_participant_mutation(source)
        labels = [
            *(["当前登录用户（我）"] if draft.includes_current_user else []),
            *draft.required_participant_names,
        ]
        names = "、".join(labels)
        items.append(
            RequirementItem(
                field="requiredParticipants",
                status=(
                    RequirementSlotStatus.DIRECTORY_RESOLVED
                    if directory
                    else RequirementSlotStatus.EXPLICIT
                    if participant_changed
                    else previous_participants.status
                    if previous_participants is not None
                    else RequirementSlotStatus.EXPLICIT
                ),
                summary=f"{len(labels)}人：{names}",
                source=(
                    "我的小组/同组人员"
                    if directory
                    else source
                    if participant_changed
                    else previous_participants.source
                    if previous_participants is not None
                    else source
                ),
                rule_id="CURRENT_USER_DEPARTMENT" if directory else None,
            )
        )
    features = "、".join(draft.required_features)
    feature_constraints_changed = _source_changes_feature_constraints(source)
    items.append(
        RequirementItem(
            field="optionalRequirements",
            status=(
                RequirementSlotStatus.CLOSED
                if optional_closed
                else RequirementSlotStatus.EXPLICIT
                if features or feature_constraints_changed
                else RequirementSlotStatus.UNSPECIFIED
            ),
            summary=(
                f"硬性设备：{features}；其他要求已结束"
                if optional_closed and features
                else "没有其他硬性要求"
                if optional_closed
                else f"硬性设备：{features}；可继续补充其他要求"
                if features
                else "已明确放宽设备要求；可继续补充其他要求"
                if feature_constraints_changed
                else "可选：投屏、白板、视频会议设备、地点等硬性要求"
            ),
            source=source if features or optional_closed else None,
        )
    )
    return items


def _time_rule_id(source: str) -> str | None:
    compact = re.sub(r"\s+", "", source)
    date_default = bool(re.search(r"(?<!月)(?<!\d)\d{1,2}号", compact))
    weekday_default = bool(
        re.search(r"(?:本周|这周|本星期|这星期|周|星期)[一二三四五六日天]", compact)
    )
    daypart = _daypart_window(source)
    if date_default and daypart is not None:
        return "CURRENT_MONTH_AND_DAYPART"
    if weekday_default and daypart is not None:
        return "CURRENT_WEEK_AND_DAYPART"
    explicit_date = bool(
        re.search(r"(?:今天|今日|明天|后天|\d{4}年|\d{1,2}月|\d{1,2}号)", compact)
        or re.search(r"(?:下周|下星期|本周|这周|周|星期)[一二三四五六日天]", compact)
    )
    time_only = bool(re.search(r"(?:\d{1,2}:\d{2}|\d{1,2}点)", source))
    if time_only and not explicit_date:
        return "CURRENT_DAY_FROM_TIME_ONLY"
    if daypart is not None:
        return "CURRENT_DAY_AND_DAYPART"
    return None


def _format_requirement_clarification(items: list[RequirementItem]) -> str:
    labels = {
        "timeWindow": "时间",
        "durationMinutes": "时长",
        "requiredParticipants": "参会人",
        "optionalRequirements": "其他条件",
    }
    status_labels = {
        RequirementSlotStatus.DEFAULTED: "系统补全",
        RequirementSlotStatus.DIRECTORY_RESOLVED: "通讯录推定",
        RequirementSlotStatus.INHERITED: "原会议继承",
        RequirementSlotStatus.MISSING: "还需补充",
        RequirementSlotStatus.EXPLICIT: "已明确",
        RequirementSlotStatus.AMBIGUOUS: "需要确认",
        RequirementSlotStatus.CONFLICT: "存在冲突",
        RequirementSlotStatus.UNSPECIFIED: "未说明",
        RequirementSlotStatus.CLOSED: "已结束",
    }
    lines = ["我先把需求整理如下，你可以一句话补充或纠正："]
    for index, item in enumerate(items, start=1):
        lines.append(
            f"{index}. {labels[item.field]}（{status_labels[item.status]}）：{item.summary}。"
        )
    if any(item.status is RequirementSlotStatus.DIRECTORY_RESOLVED for item in items):
        lines.append("“我的小组”暂按当前所属部门解释；如名单有误，请直接补充或删除人员。")
    blocking = [labels[item.field] for item in items if item.blocking]
    if blocking:
        lines.append("开始查询前还需要：" + "、".join(blocking) + "。")
    return "\n".join(lines)[:500]
