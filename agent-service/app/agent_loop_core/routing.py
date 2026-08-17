"""Deterministic initial-route validation and fallback."""

from __future__ import annotations

import re

from app.agent_loop_core.feedback import RouteFeedback
from app.schemas.agent import (
    Intent,
    Route,
    SupervisorDecision,
)


class RouteEvaluator:
    """Validate the initial business route using high-confidence Chinese anchors."""

    _POLICY_TOPICS = (
        "规则",
        "制度",
        "规定",
        "限制",
        "使用条件",
        "政策",
        "审批",
        "使用说明",
        "标准",
    )
    _POLICY_QUESTION = (
        "哪些",
        "什么",
        "怎么",
        "为何",
        "为什么",
        "是否",
        "能否",
    )
    _PERMISSION_QUESTION = ("是否允许", "可以吗", "能不能", "可不可以", "能直接")
    _CANCEL = ("取消", "撤销", "撤掉", "不订了")
    _MODIFY = (
        "改期",
        "调整会议",
        "调整安排",
        "调整时间",
        "调整房间",
        "调整到",
        "挪到",
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
        "预留",
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
        "共同空档",
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
    _PREFERENCE = ("以后", "优先安排", "避免", "会议偏好")

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
        normalized = source.lower()
        find_anchor = any(anchor in normalized for anchor in self._FIND) or (
            "推荐" in normalized and any(room in normalized for room in ("会议室", "房间"))
        )
        has_mutation = any(
            anchor in source for anchor in (*self._CANCEL, *self._MODIFY, *self._CREATE)
        )
        has_policy_topic = any(anchor in source for anchor in self._POLICY_TOPICS)
        asks_for_rules = has_policy_topic and (
            any(anchor in source for anchor in self._POLICY_QUESTION)
            or any(marker in source for marker in ("?", "？"))
        )
        asks_permission = (
            any(anchor in source for anchor in self._PERMISSION_QUESTION)
            and not has_mutation
            and not find_anchor
        )
        if asks_for_rules or asks_permission or (has_policy_topic and not has_mutation):
            return Route.POLICY, Intent.QUERY_POLICY
        if any(anchor in source for anchor in self._CANCEL):
            return Route.REQUIREMENT, Intent.CANCEL_MEETING
        if any(anchor in source for anchor in self._MODIFY):
            return Route.REQUIREMENT, Intent.MODIFY_MEETING
        if any(anchor in source for anchor in self._PREFERENCE):
            return Route.REQUIREMENT, Intent.UPDATE_PREFERENCE
        explicitly_read_only = any(
            value in normalized for value in ("不预约", "不要预约", "只查", "只推荐", "read only")
        ) or bool(
            re.search(r"(?:不|不要|先不要).{0,6}(?:创建|预约|预订)", normalized)
        )
        recommendation_without_booking = (
            "推荐" in normalized
            and any(room in normalized for room in ("会议室", "房间"))
            and not any(anchor in normalized for anchor in self._CREATE)
        )
        if find_anchor and (explicitly_read_only or recommendation_without_booking):
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
        if (
            re.search(r"(?:30|60|90|120|150|180|210|240)\s*分钟", source)
            and any(anchor in source for anchor in ("今天", "明天", "后天", "上午", "下午", "周"))
            and any(anchor in source for anchor in ("给", "请", "让", "帮", "协调", "参加"))
            and any(
                anchor in source
                for anchor in ("会", "会议", "评审", "工作坊", "复盘", "讨论")
            )
        ):
            return Route.REQUIREMENT, Intent.CREATE_MEETING
        if find_anchor:
            intent = (
                Intent.RECOMMEND_ROOM
                if any(value in normalized for value in ("推荐", "只推荐"))
                else Intent.FIND_COMMON_TIME
            )
            return Route.REQUIREMENT, intent
        return Route.CLARIFICATION, None
