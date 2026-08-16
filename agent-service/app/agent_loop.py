"""Deterministic safety boundary for the Scheduling Agent's bounded READ loop."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import Field, ValidationError

from app.providers.base import ModelToolCall, ToolDefinition
from app.schemas.agent import (
    AgentSchema,
    AgentState,
    Intent,
    MeetingRequest,
    NormalizationReport,
    Participant,
    RequirementDraft,
    Route,
    SupervisorDecision,
)
from app.security import AgentContext
from app.tools.java import (
    FreeBusyInput,
    JavaReadToolClient,
    JavaToolError,
    RecentMeetingInput,
    ResolveEmployeesInput,
    SearchRoomsInput,
    ToolInput,
    ToolOutcome,
    stable_tool_identity,
)


class LoopStopReason(StrEnum):
    READY_FOR_CONFIRMATION = "READY_FOR_CONFIRMATION"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    NO_SOLUTION = "NO_SOLUTION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    WAITING_BUSINESS_RESULT = "WAITING_BUSINESS_RESULT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RequirementFeedback(AgentSchema):
    codes: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=500)
    repairable: bool


class ConflictRepairFeedback(AgentSchema):
    conflict_type: str = Field(min_length=1, max_length=64)
    failed_candidate_id: str = Field(min_length=1, max_length=64)
    preserved_constraints: list[str] = Field(min_length=1, max_length=20)
    excluded_candidate_ids: list[str] = Field(min_length=1, max_length=3)
    replan_count: int = Field(ge=1, le=2)
    room_id: int | None = Field(default=None, ge=1)
    slots: list[int] = Field(default_factory=list, max_length=48)
    reason: str = Field(min_length=1, max_length=240)


class RouteFeedback(AgentSchema):
    codes: list[str] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=1, max_length=300)


class RouteEvaluator:
    """Validate the initial business route using high-confidence Chinese anchors."""

    _POLICY = (
        "规则",
        "制度",
        "规定",
        "限制",
        "能否",
        "是否允许",
        "使用条件",
        "政策",
        "可以吗",
        "能不能",
        "可不可以",
        "能直接",
    )
    _CANCEL = ("取消", "撤销", "不订了")
    _MODIFY = (
        "改期",
        "调整会议",
        "调整安排",
        "调整时间",
        "调整房间",
        "调整到",
        "换会议室",
        "改到",
        "修改",
        "异常重排",
        "资源失效",
        "会议室不可用",
        "会议室已失效",
        "房间不可用",
    )
    _CREATE = (
        "预约",
        "预订",
        "安排",
        "创建会议",
        "约个会",
        "开个会",
        "碰一下",
        "碰个头",
        "book",
        "schedule",
    )
    _FIND = (
        "共同时间",
        "共同空闲",
        "什么时候有空",
        "一起空",
        "同时有空",
        "只查时间",
        "找时间",
        "推荐会议室",
        "推荐房间",
        "只推荐",
        "find time",
    )
    _PREFERENCE = ("以后", "优先安排", "避免")

    def evaluate(self, decision: SupervisorDecision, source: str) -> RouteFeedback | None:
        codes: list[str] = []
        if decision.route not in {Route.POLICY, Route.REQUIREMENT, Route.CLARIFICATION}:
            codes.append("INITIAL_ROUTE_NOT_ALLOWED")
        if decision.evidence and decision.evidence not in source:
            codes.append("ROUTE_EVIDENCE_NOT_IN_SOURCE")
        expected_route, expected_intent = self.fallback(source)
        if expected_route is not Route.CLARIFICATION and decision.route is not expected_route:
            codes.append("ROUTE_ANCHOR_MISMATCH")
        if expected_intent is not None and decision.intent_hint not in {None, expected_intent}:
            codes.append("ROUTE_INTENT_MISMATCH")
        if not 0 <= decision.confidence <= 1:
            codes.append("ROUTE_CONFIDENCE_INVALID")
        return (
            RouteFeedback(codes=codes, summary="初始路由校验未通过：" + "、".join(codes))
            if codes
            else None
        )

    def fallback(self, source: str) -> tuple[Route, Intent | None]:
        has_mutation = any(
            anchor in source for anchor in (*self._CANCEL, *self._MODIFY, *self._CREATE)
        )
        asks_for_rules = any(anchor in source for anchor in self._POLICY) and any(
            anchor in source
            for anchor in ("哪些", "什么", "查询", "根据制度", "能否", "是否允许", "使用条件")
        )
        if asks_for_rules or (
            any(anchor in source for anchor in self._POLICY) and not has_mutation
        ):
            return Route.POLICY, Intent.QUERY_POLICY
        if any(anchor in source for anchor in self._CANCEL):
            return Route.REQUIREMENT, Intent.CANCEL_MEETING
        if any(anchor in source for anchor in self._MODIFY):
            return Route.REQUIREMENT, Intent.MODIFY_MEETING
        if any(anchor in source for anchor in self._PREFERENCE):
            return Route.REQUIREMENT, Intent.UPDATE_PREFERENCE
        normalized = source.lower()
        find_anchor = any(anchor in normalized for anchor in self._FIND)
        explicitly_read_only = any(
            value in normalized for value in ("不预约", "不要预约", "只查", "只推荐", "read only")
        )
        if find_anchor and explicitly_read_only:
            intent = (
                Intent.RECOMMEND_ROOM
                if any(value in normalized for value in ("推荐", "只推荐"))
                else Intent.FIND_COMMON_TIME
            )
            return Route.REQUIREMENT, intent
        if any(anchor in normalized for anchor in self._CREATE):
            return Route.REQUIREMENT, Intent.CREATE_MEETING
        if re.search(r"开(?:一场|个)?(?:\d+分钟|[一二两三四五六七八九十]+小时)?会", source):
            return Route.REQUIREMENT, Intent.CREATE_MEETING
        if "协调" in source and any(anchor in source for anchor in ("会议", "站会", "评审")):
            return Route.REQUIREMENT, Intent.CREATE_MEETING
        if find_anchor:
            intent = (
                Intent.RECOMMEND_ROOM
                if any(value in normalized for value in ("推荐", "只推荐"))
                else Intent.FIND_COMMON_TIME
            )
            return Route.REQUIREMENT, intent
        return Route.CLARIFICATION, None


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
                            draft.time_window.start.hour == 12
                            and draft.time_window.end.hour == 18
                        )
                    if "morning" in normalized:
                        return (
                            draft.time_window.start.hour == 6
                            and draft.time_window.end.hour == 12
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


READ_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="resolve_employees",
        description="Resolve participant names against the authenticated user's organisation.",
        parameters=ResolveEmployeesInput.model_json_schema(by_alias=True),
    ),
    ToolDefinition(
        name="get_employee_free_busy",
        description="Read required participants' busy intervals in the canonical time window.",
        parameters=FreeBusyInput.model_json_schema(by_alias=True),
    ),
    ToolDefinition(
        name="search_available_rooms",
        description="Read rooms satisfying canonical time, capacity and feature constraints.",
        parameters=SearchRoomsInput.model_json_schema(by_alias=True),
    ),
    ToolDefinition(
        name="get_recent_meeting",
        description="Resolve an explicit recent-meeting reference for modify/cancel requests.",
        parameters=RecentMeetingInput.model_json_schema(by_alias=True),
    ),
)


class ToolGateError(RuntimeError):
    def __init__(self, code: str, *, recoverable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.recoverable = recoverable


@dataclass(frozen=True)
class GatedToolResult:
    outcome: ToolOutcome
    fingerprint: str
    observation: str


@dataclass
class ReadToolGate:
    tools: JavaReadToolClient

    def execute(
        self,
        *,
        call: ModelToolCall,
        state: AgentState,
        context: AgentContext,
        resolved_employees: list[Participant],
        fingerprints: set[str],
    ) -> GatedToolResult:
        request = state.meeting_request
        if request is None:
            raise ToolGateError("REQUIREMENT_MISSING", recoverable=False)
        input_type = _INPUT_TYPES.get(call.name)
        if input_type is None:
            raise ToolGateError("TOOL_NOT_ALLOWED", recoverable=False)
        try:
            payload = input_type.model_validate_json(call.arguments)
        except ValidationError as exc:
            raise ToolGateError("TOOL_ARGUMENTS_INVALID") from exc
        payload = self._canonicalize_mutation_exclusion(
            name=call.name,
            payload=payload,
            state=state,
        )
        self._validate_business_context(
            name=call.name,
            payload=payload,
            state=state,
            context=context,
            resolved_employees=resolved_employees,
        )
        canonical = payload.model_dump(by_alias=True, mode="json")
        fingerprint = _fingerprint(call.name, canonical)
        if fingerprint in fingerprints:
            raise ToolGateError("DUPLICATE_TOOL_FINGERPRINT")
        # EDIT and conflict recovery intentionally start a fresh fact epoch.
        # Keep retries idempotent inside an epoch without replaying stale Java
        # audit results or colliding in Python Trace across epochs.
        execution_epoch = (
            f"draft-{state.draft_generation}:replan-{state.replan_count}:"
            f"fact-{state.loop_iteration}:trace-{context.trace_id}"
        )
        business_tool_id = stable_tool_identity(
            context.run_id, call.name, f"{execution_epoch}:{fingerprint}"
        )
        try:
            outcome = self._invoke(
                name=call.name,
                payload=payload,
                context=context,
                tool_call_id=business_tool_id,
            )
        except JavaToolError as exc:
            raise ToolGateError(exc.code, recoverable=exc.code not in {"TOOL_FORBIDDEN"}) from exc
        observation = json.dumps(
            {"toolName": call.name, "data": outcome.data},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(observation.encode("utf-8")) > 32 * 1024:
            raise ToolGateError("TOOL_RESULT_TOO_LARGE", recoverable=False)
        return GatedToolResult(outcome=outcome, fingerprint=fingerprint, observation=observation)

    @staticmethod
    def _canonicalize_mutation_exclusion(
        *, name: str, payload: ToolInput, state: AgentState
    ) -> ToolInput:
        request = state.meeting_request
        assert request is not None
        if (
            request.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
            and request.target_meeting_id is None
            and name != "get_recent_meeting"
        ):
            raise ToolGateError("TARGET_MEETING_REQUIRED")
        if not isinstance(payload, FreeBusyInput | SearchRoomsInput):
            return payload
        expected = request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None
        if payload.exclude_meeting_id not in {None, expected}:
            raise ToolGateError("TOOL_CONTEXT_MISMATCH")
        return payload.model_copy(update={"exclude_meeting_id": expected})

    @staticmethod
    def _validate_business_context(
        *,
        name: str,
        payload: ToolInput,
        state: AgentState,
        context: AgentContext,
        resolved_employees: list[Participant],
    ) -> None:
        request = state.meeting_request
        assert request is not None
        window = request.time_window
        expected_names = list(dict.fromkeys(item.name for item in request.required_participants))
        if name == "resolve_employees":
            assert isinstance(payload, ResolveEmployeesInput)
            if not expected_names:
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
            if payload.names != expected_names or payload.department_names:
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
            return
        if name == "get_recent_meeting":
            if request.intent.value not in {"MODIFY_MEETING", "CANCEL_MEETING"}:
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
            return
        if window is None:
            raise ToolGateError("TIME_WINDOW_REQUIRED", recoverable=False)
        if name == "get_employee_free_busy":
            assert isinstance(payload, FreeBusyInput)
            resolved_ids = {
                item.employee_id for item in resolved_employees if item.employee_id is not None
            }
            expected_ids = sorted({context.user_id, *resolved_ids})
            if (
                payload.employee_ids != expected_ids
                or payload.from_ != window.start
                or payload.to != window.end
                or payload.exclude_meeting_id
                != (request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None)
            ):
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")
        elif name == "search_available_rooms":
            assert isinstance(payload, SearchRoomsInput)
            resolved_ids = {
                item.employee_id for item in resolved_employees if item.employee_id is not None
            }
            expected_capacity = max(
                request.minimum_capacity or 1,
                len({context.user_id, *resolved_ids}),
            )
            if (
                payload.from_ != window.start
                or payload.to != window.end
                or payload.minimum_capacity != expected_capacity
                or payload.required_features != request.required_features
                or payload.limit != 50
                or payload.exclude_meeting_id
                != (request.target_meeting_id if request.intent is Intent.MODIFY_MEETING else None)
            ):
                raise ToolGateError("TOOL_CONTEXT_MISMATCH")

    def _invoke(
        self,
        *,
        name: str,
        payload: ToolInput,
        context: AgentContext,
        tool_call_id: str,
    ) -> ToolOutcome:
        if isinstance(payload, ResolveEmployeesInput):
            return self.tools.resolve_employees(
                context=context,
                names=payload.names,
                department_names=payload.department_names,
                tool_call_id=tool_call_id,
            )
        if isinstance(payload, FreeBusyInput):
            return self.tools.get_employee_free_busy(
                context=context,
                employee_ids=payload.employee_ids,
                from_=payload.from_,
                to=payload.to,
                exclude_meeting_id=payload.exclude_meeting_id,
                tool_call_id=tool_call_id,
            )
        if isinstance(payload, SearchRoomsInput):
            return self.tools.search_available_rooms(
                context=context,
                from_=payload.from_,
                to=payload.to,
                minimum_capacity=payload.minimum_capacity,
                required_features=payload.required_features,
                exclude_meeting_id=payload.exclude_meeting_id,
                tool_call_id=tool_call_id,
            )
        assert isinstance(payload, RecentMeetingInput)
        return self.tools.get_recent_meeting(
            context=context, limit=payload.limit, tool_call_id=tool_call_id
        )


_INPUT_TYPES: dict[str, type[ToolInput]] = {
    "resolve_employees": ResolveEmployeesInput,
    "get_employee_free_busy": FreeBusyInput,
    "search_available_rooms": SearchRoomsInput,
    "get_recent_meeting": RecentMeetingInput,
}


def _fingerprint(name: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()


def tool_fingerprint(name: str, payload: dict[str, Any]) -> str:
    """Public test/evaluation boundary for canonical Tool-call deduplication."""

    return _fingerprint(name, payload)
