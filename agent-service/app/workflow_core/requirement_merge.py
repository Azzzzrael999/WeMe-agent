"""Continuation-turn deltas, draft merging, and recommendation application."""

from __future__ import annotations

import re
from datetime import timedelta

from app.agent_loop import (
    RequirementFeedback,
    SourceFidelityEvaluator,
)
from app.schemas.agent import (
    AgentState,
    Participant,
    RequirementDraft,
    TimeWindow,
    UnsatAnalysis,
)
from app.tools.java import (
    ToolOutcome,
)
from app.workflow_core.requirement_source import (
    _feature_removal_requested,
    _source_changes_feature_constraints,
    _source_changes_intent,
    _source_changes_meeting_duration,
)


def _apply_participant_delta(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    source: str,
) -> RequirementDraft:
    """Apply explicit roster mutations to the last verified participant list.

    The model extracts only the current utterance, so a removal can legitimately
    contain no replacement list.  Matching removals against the previous names
    keeps the operation deterministic and prevents a negative instruction from
    being misread as a brand-new participant list.
    """

    if previous is None or not previous.required_participant_names:
        return current
    previous_names = list(dict.fromkeys(previous.required_participant_names))
    mentioned_previous = [name for name in previous_names if name in source]
    remove_requested = bool(
        mentioned_previous
        and any(
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
            )
        )
    )
    add_requested = any(
        marker in source for marker in ("加上", "增加", "添加", "邀请", "再叫上", "再加")
    )
    replace_requested = any(
        marker in source for marker in ("改成", "换成", "只有", "就这些人", "参会人是")
    )

    next_names: list[str] | None = None
    if remove_requested:
        removed = set(mentioned_previous)
        next_names = [name for name in previous_names if name not in removed]
        if add_requested:
            deterministic_additions = re.findall(
                r"(?:加上|增加|添加|邀请|再叫上|再加)\s*"
                r"([\u4e00-\u9fff]{2,4}?)(?=必须|参加|、|，|和|与|$)",
                source,
            )
            additions = [
                name
                for name in [*current.required_participant_names, *deterministic_additions]
                if name not in removed
            ]
            next_names = list(dict.fromkeys([*next_names, *additions]))
    elif add_requested and current.required_participant_names:
        next_names = list(dict.fromkeys([*previous_names, *current.required_participant_names]))
    elif replace_requested and current.required_participant_names:
        next_names = list(dict.fromkeys(current.required_participant_names))

    if next_names is None:
        return current
    previous_capacity = previous.minimum_capacity
    explicit_capacity = current.minimum_capacity
    preserved_capacity = (
        previous_capacity
        if previous_capacity is not None and previous_capacity > len(previous_names)
        else None
    )
    next_capacity = max(
        len(next_names),
        explicit_capacity or 0,
        preserved_capacity or 0,
        1,
    )
    return current.model_copy(
        update={
            "required_participant_names": next_names,
            # A user-corrected directory roster is now an explicit list.  Do
            # not resolve MY_DEPARTMENT again and silently re-add removals.
            "participant_scope": previous.participant_scope,
            "participant_list_modified": True,
            "minimum_capacity": next_capacity,
        }
    )


def _apply_constraint_delta(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    source: str,
) -> RequirementDraft:
    """Apply explicit relaxation deltas to the last verified requirement.

    Exception replanning starts from inherited Java facts.  A continuation may
    relax one device or extend the original candidate window, but it must not
    accidentally replace the inherited meeting duration just because the
    relaxation itself contains a number of minutes.
    """

    if previous is None:
        return current
    updates: dict[str, object] = {}
    features = list(previous.required_features)
    removed_features = {
        canonical
        for canonical, aliases in SourceFidelityEvaluator._FEATURE_ALIASES.items()
        if any(_feature_removal_requested(source, alias) for alias in aliases)
    }
    if removed_features:
        features = [item for item in features if item not in removed_features]
        additions = [item for item in current.required_features if item not in removed_features]
        updates["required_features"] = list(dict.fromkeys([*features, *additions]))

    delay = re.search(r"(?:允许)?(?:顺延|延后|推迟)\s*(30|60|90|120)\s*分钟", source)
    if delay is not None and previous.time_window is not None:
        delay_minutes = int(delay.group(1))
        updates.update(
            {
                "duration_minutes": previous.duration_minutes,
                "time_window": TimeWindow(
                    start=previous.time_window.start,
                    end=previous.time_window.end + timedelta(minutes=delay_minutes),
                ),
                "pending_start_at": None,
                "pending_start_ambiguous": False,
            }
        )
    return current.model_copy(update=updates) if updates else current


def _continuation_fidelity_feedback(
    feedback: RequirementFeedback | None, *, state: AgentState
) -> RequirementFeedback | None:
    """Keep a draft's trusted intent when a continuation only edits that draft.

    Phrases such as “那改到明天” and “调整参会人” mutate the pending request;
    they do not turn a CREATE run into a reschedule of an existing meeting.
    Other fidelity failures remain blocking.
    """

    if (
        feedback is None
        or not state.continuation_turn
        or state.intent is None
        or _source_changes_intent(state.message)
        or "INTENT_SOURCE_MISMATCH" not in feedback.codes
    ):
        return feedback
    remaining = [code for code in feedback.codes if code != "INTENT_SOURCE_MISMATCH"]
    if not remaining:
        return None
    return feedback.model_copy(
        update={
            "codes": remaining,
            "summary": "源文本忠实度校验未通过：" + "、".join(remaining),
        }
    )


def _merge_requirement_drafts(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    *,
    source: str,
) -> RequirementDraft:
    if previous is None:
        return current
    explicit_names = bool(current.required_participant_names)
    participant_mutated = current.participant_list_modified
    scope_changed = current.participant_scope is not None
    if current.time_window is not None:
        time_window = current.time_window
        pending_start_at = None
        pending_start_ambiguous = False
    elif current.pending_start_at is not None:
        time_window = None
        pending_start_at = current.pending_start_at
        pending_start_ambiguous = current.pending_start_ambiguous
    else:
        time_window = previous.time_window
        pending_start_at = previous.pending_start_at
        pending_start_ambiguous = previous.pending_start_ambiguous
    soft_constraints = [*previous.soft_constraints]
    for soft_item in current.soft_constraints:
        soft_constraints = [
            existing for existing in soft_constraints if existing.type != soft_item.type
        ]
        value = soft_item.value
        if (
            soft_item.type == "PREFER_START_AT"
            and value.startswith("0")
            and time_window is not None
            and time_window.start.hour >= 12
        ):
            hour, minute = value.split(":", maxsplit=1)
            value = f"{int(hour) + 12:02d}:{minute}"
        soft_constraints.append(soft_item.model_copy(update={"value": value}))
    hard_constraints = list(previous.hard_constraints)
    for hard_item in current.hard_constraints:
        hard_constraints = [
            existing for existing in hard_constraints if existing.type != hard_item.type
        ]
        hard_constraints.append(hard_item)
    evidence = [*previous.field_evidence]
    for evidence_item in current.field_evidence:
        if evidence_item not in evidence:
            evidence.append(evidence_item)
    return previous.model_copy(
        update={
            "intent": (current.intent if _source_changes_intent(source) else previous.intent),
            "title": current.title or previous.title,
            "meeting_type": current.meeting_type or previous.meeting_type,
            "duration_minutes": (
                current.duration_minutes
                if _source_changes_meeting_duration(source)
                else previous.duration_minutes
            ),
            "time_window": time_window,
            "pending_start_at": pending_start_at,
            "pending_start_ambiguous": pending_start_ambiguous,
            "required_participant_names": (
                current.required_participant_names
                if explicit_names or scope_changed or participant_mutated
                else previous.required_participant_names
            ),
            "includes_current_user": (
                current.includes_current_user or previous.includes_current_user
            ),
            "participant_scope": (
                current.participant_scope or previous.participant_scope
                if participant_mutated
                else None
                if explicit_names
                else current.participant_scope or previous.participant_scope
            ),
            "participant_list_modified": (
                participant_mutated or previous.participant_list_modified
            ),
            "optional_groups": list(
                dict.fromkeys([*previous.optional_groups, *current.optional_groups])
            ),
            "required_features": (
                current.required_features
                if _source_changes_feature_constraints(source)
                else list(dict.fromkeys([*previous.required_features, *current.required_features]))
            ),
            "minimum_capacity": current.minimum_capacity or previous.minimum_capacity,
            "preferred_buildings": list(
                dict.fromkeys([*previous.preferred_buildings, *current.preferred_buildings])
            ),
            "hard_constraints": hard_constraints,
            "soft_constraints": soft_constraints,
            "target_meeting_id": current.target_meeting_id or previous.target_meeting_id,
            "target_meeting_reference": (
                current.target_meeting_reference or previous.target_meeting_reference
            ),
            "field_evidence": evidence[-40:],
            "needs_policy": current.needs_policy or previous.needs_policy,
            "summary": current.summary,
        }
    )


def _apply_unsat_recommended_time(
    previous: RequirementDraft | None,
    current: RequirementDraft,
    *,
    source: str,
    analysis: UnsatAnalysis | None,
) -> RequirementDraft:
    """Apply a previously offered time only after the user explicitly accepts it."""

    if previous is None or analysis is None:
        return current
    if not any(
        marker in source
        for marker in (
            "按你推荐",
            "按推荐",
            "用推荐时间",
            "最近可行时间",
            "推荐的时间",
        )
    ):
        return current
    blocker_ends = [item.end_at for item in analysis.blocking_intervals]
    if not blocker_ends or previous.duration_minutes is None:
        return current
    recommended_start = max(blocker_ends)
    local_day_end = recommended_start.replace(hour=18, minute=0, second=0, microsecond=0)
    if recommended_start + timedelta(minutes=previous.duration_minutes) > local_day_end:
        return current
    return current.model_copy(
        update={
            "time_window": None,
            "pending_start_at": recommended_start,
            "pending_start_ambiguous": False,
            "summary": (
                f"用户接受了上一轮建议的最近可重新校验时间 {recommended_start:%Y-%m-%d %H:%M}。"
            ),
        }
    )


def _scope_members(outcome: ToolOutcome) -> list[Participant]:
    raw = outcome.data.get("members")
    if not isinstance(raw, list):
        return []
    members: list[Participant] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        employee_id = item.get("employeeId")
        display_name = item.get("displayName")
        status = item.get("status")
        if isinstance(employee_id, int) and isinstance(display_name, str) and status == "ACTIVE":
            members.append(Participant(name=display_name, employee_id=employee_id))
    return members
