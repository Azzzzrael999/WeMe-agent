"""Source fidelity, normalization, and requirement evaluation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.agent_loop_core.feedback import RequirementFeedback
from app.agent_loop_core.routing import RouteEvaluator
from app.schemas.agent import (
    Intent,
    MeetingRequest,
    NormalizationReport,
    Participant,
    RequirementDraft,
    Route,
)


class SourceFidelityEvaluator:
    """Reject unsupported names, headcount conversion, time and feature hallucinations."""

    _KNOWN_NAME = re.compile(r"[\u4e00-\u9fff]{2,4}(?:经理)?")
    _HEADCOUNT = re.compile(r"(\d{1,4})\s*(?:个)?人")
    _TIME_RANGE = re.compile(r"(\d{1,2})(?::(\d{2})|点)\s*(?:到|至|-)\s*(\d{1,2})(?::(\d{2})|点)")
    _FEATURE_ALIASES = {
        "WHITEBOARD": ("白板", "whiteboard"),
        "LARGE_SCREEN": ("大屏", "大屏幕", "投屏", "large screen"),
        "VIDEO_CONFERENCE": ("视频会议", "视频设备", "video conference"),
        "PROJECTOR": ("投影", "投影仪", "projector"),
    }
    _SEARCH_WINDOW_SUFFIX = re.compile(
        r"^\s*(?:之间|以内|内|这个?范围(?:内)?|范围内|时段内|期间|有没有|能不能|是否有)"
    )

    def evaluate(self, draft: RequirementDraft, source: str) -> RequirementFeedback | None:
        codes: list[str] = []
        for evidence in draft.field_evidence:
            if not _evidence_supported(evidence.field, evidence.source, source, draft):
                codes.append("EVIDENCE_NOT_IN_SOURCE")
        for name in draft.required_participant_names:
            if name not in source or re.fullmatch(r"\d+(?:个)?人", name):
                codes.append("PARTICIPANT_NOT_IN_SOURCE")
        explicit_names = _explicit_participant_names(source)
        if explicit_names.difference(draft.required_participant_names):
            codes.append("EXPLICIT_PARTICIPANT_OMITTED")
        if any(re.search(r"\d", name) for name in draft.required_participant_names):
            codes.append("HEADCOUNT_AS_PARTICIPANT")
        headcount = self._HEADCOUNT.search(source)
        if headcount and draft.minimum_capacity != int(headcount.group(1)):
            codes.append("CAPACITY_SOURCE_MISMATCH")
        normalized_source = source.lower()
        for feature in draft.required_features:
            aliases = self._FEATURE_ALIASES.get(_canonical_feature(feature), ())
            if not aliases or not any(alias.lower() in normalized_source for alias in aliases):
                codes.append("FEATURE_NOT_IN_SOURCE")
        explicit_range = self._TIME_RANGE.search(source)
        if explicit_range and draft.time_window is not None:
            start_minute = int(explicit_range.group(1)) * 60 + int(explicit_range.group(2) or 0)
            end_minute = int(explicit_range.group(3)) * 60 + int(explicit_range.group(4) or 0)
            actual = int((draft.time_window.end - draft.time_window.start).total_seconds() / 60)
            expected = end_minute - start_minute
            if expected > 0 and actual != expected:
                codes.append("EXPLICIT_TIME_CHANGED")
            if (
                draft.duration_minutes is not None
                and expected > 0
                and draft.duration_minutes != expected
                and draft.intent not in {Intent.FIND_COMMON_TIME, Intent.RECOMMEND_ROOM}
                and not self.is_search_window(source, explicit_range)
            ):
                codes.append("DURATION_INTERVAL_MISMATCH")
        expected_route, expected_intent = RouteEvaluator().fallback(source)
        if expected_route is Route.POLICY or (
            expected_intent is not None and draft.intent is not expected_intent
        ):
            codes.append("INTENT_SOURCE_MISMATCH")
        if draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING} and (
            draft.target_meeting_id is None and not draft.target_meeting_reference
        ):
            codes.append("TARGET_REFERENCE_MISSING")
        unique = list(dict.fromkeys(codes))
        return (
            RequirementFeedback(
                codes=unique,
                summary="源文本忠实度校验未通过：" + "、".join(unique),
                repairable=not any(code == "TARGET_REFERENCE_MISSING" for code in unique),
            )
            if unique
            else None
        )

    @classmethod
    def is_search_window(cls, source: str, match: re.Match[str]) -> bool:
        """Distinguish an allowed candidate window from a fixed meeting interval."""

        suffix = source[match.end() : match.end() + 8]
        return cls._SEARCH_WINDOW_SUFFIX.search(suffix) is not None


class RequirementNormalizer:
    """Own safe defaults and derived canonical fields after fidelity validation."""

    def normalize(
        self, draft: RequirementDraft, *, source: str = ""
    ) -> tuple[MeetingRequest, NormalizationReport]:
        defaults: list[str] = []
        derived: list[str] = []
        title = draft.title
        if not title:
            title = "会议安排"
            defaults.append("title")
        meeting_type = draft.meeting_type
        if not meeting_type:
            meeting_type = "GENERAL"
            defaults.append("meetingType")
        duration = draft.duration_minutes
        if (
            duration is None
            and draft.time_window is not None
            and draft.intent not in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
            and not _source_describes_search_window(source)
        ):
            duration = int((draft.time_window.end - draft.time_window.start).total_seconds() / 60)
            derived.append("durationMinutes")
        if duration is None:
            duration = 30
            defaults.append("durationMinutesPlaceholder")
        participant_count = (
            len(set(draft.required_participant_names))
            if draft.participant_scope == "MY_DEPARTMENT"
            else 1 + len(set(draft.required_participant_names))
        )
        minimum_capacity = max(draft.minimum_capacity or 1, participant_count)
        if draft.minimum_capacity != minimum_capacity:
            derived.append("minimumCapacity")
        populated = {
            "intent",
            *(field.field for field in draft.field_evidence),
        }
        supported = {field.field for field in draft.field_evidence}
        coverage = len(supported) / max(1, len(populated))
        return (
            MeetingRequest(
                intent=draft.intent,
                title=title,
                meeting_type=meeting_type,
                duration_minutes=duration,
                time_window=draft.time_window,
                required_participants=[
                    Participant(name=name) for name in draft.required_participant_names
                ],
                includes_current_user=draft.includes_current_user,
                optional_groups=draft.optional_groups,
                required_features=list(
                    dict.fromkeys(_canonical_feature(item) for item in draft.required_features)
                ),
                minimum_capacity=minimum_capacity,
                preferred_buildings=draft.preferred_buildings,
                hard_constraints=draft.hard_constraints,
                soft_constraints=draft.soft_constraints,
                target_meeting_id=draft.target_meeting_id,
                target_meeting_reference=draft.target_meeting_reference,
            ),
            NormalizationReport(
                defaults_applied=defaults,
                derived_fields=derived,
                evidence_coverage=coverage,
            ),
        )


class RequirementEvaluator:
    """Check semantic invariants independently from the extraction model."""

    def evaluate(
        self, request: MeetingRequest, *, request_time: datetime
    ) -> RequirementFeedback | None:
        codes: list[str] = []
        repairable = True
        window = request.time_window
        if request.intent.value in {
            "CREATE_MEETING",
            "MODIFY_MEETING",
            "FIND_COMMON_TIME",
            "RECOMMEND_ROOM",
        }:
            if window is None:
                if request.intent is Intent.MODIFY_MEETING and (
                    request.target_meeting_id is not None or request.target_meeting_reference
                ):
                    window = None
                else:
                    return RequirementFeedback(
                        codes=["TIME_WINDOW_REQUIRED"],
                        summary="缺少可调度时间窗口。",
                        repairable=False,
                    )
            if window is None:
                # MODIFY may preserve the original window; Scheduling merges
                # it from the latest Java snapshot before availability reads.
                pass
            else:
                if window.start.utcoffset() != timedelta(
                    hours=8
                ) or window.end.utcoffset() != timedelta(hours=8):
                    codes.append("TIMEZONE_NOT_ASIA_SHANGHAI")
                if any(
                    value.minute % 30 or value.second or value.microsecond
                    for value in (window.start, window.end)
                ):
                    codes.append("TIME_NOT_ON_30_MINUTE_SLOT")
                if window.end - window.start < timedelta(minutes=request.duration_minutes):
                    codes.append("WINDOW_SHORTER_THAN_DURATION")
                    repairable = False
                if window.end <= request_time:
                    codes.append("TIME_WINDOW_IN_PAST")
                    repairable = False
        participant_count = max(1, len(request.required_participants))
        if request.minimum_capacity is not None and request.minimum_capacity < participant_count:
            codes.append("CAPACITY_BELOW_PARTICIPANTS")
        if request.intent.value in {"MODIFY_MEETING", "CANCEL_MEETING"} and (
            request.target_meeting_id is None and not request.target_meeting_reference
        ):
            codes.append("TARGET_MEETING_REQUIRED")
            repairable = False
        hard = {(item.type, item.value) for item in request.hard_constraints}
        soft = {(item.type, item.value) for item in request.soft_constraints}
        if hard.intersection(soft):
            codes.append("HARD_SOFT_CONSTRAINT_CONFLICT")
        if not codes:
            return None
        return RequirementFeedback(
            codes=codes,
            summary="需求语义校验未通过：" + "、".join(codes),
            repairable=repairable,
        )


def _explicit_participant_names(source: str) -> set[str]:
    """High-precision explicit demo-name anchors; never treats headcount as names."""

    anchors = {name for name in ("张三", "李四", "王经理") if name in source}
    invite = re.search(
        r"(?:安排|邀请|和|让)([\u4e00-\u9fff、，和与]{2,40})(?:参加|开|在|于)",
        source,
    )
    if invite:
        for value in re.split(r"[、，和与]", invite.group(1)):
            value = value.strip()
            if (
                2 <= len(value) <= 4
                and value not in {"我自己", "只有我", "就我一个人"}
                and not any(term in value for term in ("会议", "帮我"))
            ):
                anchors.add(value)
    return anchors


def _evidence_supported(field: str, evidence: str, source: str, draft: RequirementDraft) -> bool:
    """Accept literal evidence or a deterministic canonical rendering."""

    if evidence.lower() in source.lower():
        return True
    if field == "intent":
        _, expected_intent = RouteEvaluator().fallback(source)
        return expected_intent is not None and draft.intent is expected_intent
    if field in {"requiredFeatures", "required_features"}:
        aliases = SourceFidelityEvaluator._FEATURE_ALIASES
        normalized_source = source.lower()
        return all(
            any(
                alias.lower() in normalized_source
                for alias in aliases.get(_canonical_feature(feature), ())
            )
            for feature in draft.required_features
        )
    if field in {"minimumCapacity", "minimum_capacity"}:
        match = SourceFidelityEvaluator._HEADCOUNT.search(source)
        if match is not None:
            return str(draft.minimum_capacity) == match.group(1)
        english_headcount = re.search(r"(\d{1,4})\s*people", source, re.IGNORECASE)
        if english_headcount is not None:
            return draft.minimum_capacity == int(english_headcount.group(1))
        # Capacity may be deterministically derived from the organiser plus
        # explicitly named required participants even though that integer is
        # not a literal source substring.
        named_count = len(set(draft.required_participant_names))
        return draft.minimum_capacity in {max(1, named_count), max(1, named_count + 1)} and all(
            name in source for name in draft.required_participant_names
        )
    if field in {"durationMinutes", "duration_minutes", "timeWindow", "time_window"}:
        explicit_range = SourceFidelityEvaluator._TIME_RANGE.search(source)
        if explicit_range is None:
            if field in {"durationMinutes", "duration_minutes"}:
                english_duration = re.search(
                    r"(30|60|90|120|150|180|210|240)[ -]minutes?",
                    source,
                    re.IGNORECASE,
                )
                if english_duration is not None:
                    return draft.duration_minutes == int(english_duration.group(1))
            if field in {"timeWindow", "time_window"} and draft.time_window is not None:
                iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", source)
                if (
                    iso_date is not None
                    and draft.time_window.start.date().isoformat() == iso_date.group(1)
                ):
                    normalized = source.lower()
                    if "afternoon" in normalized:
                        return (
                            draft.time_window.start.hour == 12 and draft.time_window.end.hour == 18
                        )
                    if "morning" in normalized:
                        return (
                            draft.time_window.start.hour == 6 and draft.time_window.end.hour == 12
                        )
            return any(anchor in source for anchor in ("半小时", "一小时", "两小时"))
        expected = (
            int(explicit_range.group(3)) * 60
            + int(explicit_range.group(4) or 0)
            - int(explicit_range.group(1)) * 60
            - int(explicit_range.group(2) or 0)
        )
        if field in {"timeWindow", "time_window"}:
            if draft.time_window is None:
                return False
            actual = int((draft.time_window.end - draft.time_window.start).total_seconds() / 60)
            return actual == expected
        explicit_duration = re.search(r"(\d{1,3})\s*分钟", source)
        if explicit_duration is not None:
            return draft.duration_minutes == int(explicit_duration.group(1))
        return draft.duration_minutes in {None, expected}
    if field in {"requiredParticipantNames", "required_participant_names"}:
        return all(name in source for name in draft.required_participant_names)
    if field in {"title", "meetingType", "meeting_type"}:
        if evidence in source:
            return True
        # A model may emit the canonical enum for an explicitly named meeting
        # type.  The canonical value is not a literal substring, but accepting
        # it is still deterministic and source-faithful.
        if field in {"meetingType", "meeting_type"}:
            aliases = {
                "ARCHITECTURE_REVIEW": ("架构评审",),
                "REQUIREMENT_REVIEW": ("需求评审",),
                "KICKOFF": ("Kickoff", "启动会"),
                "RETROSPECTIVE": ("复盘",),
                "CUSTOMER_MEETING": ("客户会议",),
            }
            canonical = (draft.meeting_type or evidence).upper()
            return any(alias in source for alias in aliases.get(canonical, ()))
        return False
    return False


def _source_describes_search_window(source: str) -> bool:
    match = SourceFidelityEvaluator._TIME_RANGE.search(source)
    return match is not None and SourceFidelityEvaluator.is_search_window(source, match)


def _canonical_feature(value: str) -> str:
    upper = value.upper()
    if upper in SourceFidelityEvaluator._FEATURE_ALIASES:
        return upper
    for canonical, aliases in SourceFidelityEvaluator._FEATURE_ALIASES.items():
        if value.lower() in {alias.lower() for alias in aliases}:
            return canonical
    return value
