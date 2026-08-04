"""Deterministic model fixture used by every automated test and local smoke."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.providers.base import (
    ModelCompletion,
    ModelRequest,
    ModelToolCall,
    ToolModelRequest,
    ToolModelResponse,
)


@dataclass(frozen=True)
class FixtureModelProvider:
    now: datetime

    def complete(self, request: ModelRequest) -> ModelCompletion:
        message = request.user_prompt
        if request.agent_name == "supervisor":
            return ModelCompletion(content=self._json(self._supervisor(message)), model="fixture")
        if request.agent_name == "requirement":
            return ModelCompletion(content=self._json(self._requirement(message)), model="fixture")
        if request.agent_name == "policy":
            return ModelCompletion(content=self._json(self._policy(message)), model="fixture")
        if request.agent_name == "scheduling":
            return ModelCompletion(content=self._json(
                {
                    "toolNames": ["resolve_employees"],
                    "summary": "先解析必需参会者，再交由后续确定性调度处理。",
                }
            ), model="fixture")
        raise ValueError(f"unsupported fixture agent: {request.agent_name}")

    def complete_tools(self, request: ToolModelRequest) -> ToolModelResponse:
        """Reproduce a native two-turn READ trajectory without network calls."""

        tool_names = {
            call.name
            for message in request.messages
            for call in message.tool_calls
            if message.role == "assistant"
        }
        observations = [message for message in request.messages if message.role == "tool"]
        canonical = _fixture_canonical_context(request)
        intent = canonical.get("intent")
        target_meeting_id = canonical.get("targetMeetingId")
        participant_names = canonical.get("participantNames")
        if not isinstance(participant_names, list):
            raise ValueError("fixture participantNames is invalid")
        if participant_names and "resolve_employees" not in tool_names:
            return ToolModelResponse(
                content=None,
                tool_calls=(
                    ModelToolCall(
                        id=f"call_fixture_resolve_{request.iteration}",
                        name="resolve_employees",
                        arguments=self._json({"names": participant_names, "departmentNames": []}),
                    ),
                ),
            )
        if (
            intent in {"MODIFY_MEETING", "CANCEL_MEETING"}
            and (intent == "MODIFY_MEETING" or target_meeting_id is None)
            and "get_recent_meeting" not in tool_names
        ):
            return ToolModelResponse(
                content=None,
                tool_calls=(
                    ModelToolCall(
                        id=f"call_fixture_recent_{request.iteration}",
                        name="get_recent_meeting",
                        arguments=self._json({"limit": 5}),
                    ),
                ),
            )
        if intent == "CANCEL_MEETING":
            return ToolModelResponse(content="取消目标已核验。", tool_calls=())
        if not {"get_employee_free_busy", "search_available_rooms"}.intersection(tool_names):
            resolved_ids: list[int] = []
            for observation in observations:
                try:
                    value = json.loads(observation.content or "{}")
                except json.JSONDecodeError:
                    continue
                if value.get("toolName") == "resolve_employees":
                    resolved_ids = [
                        item["employeeId"]
                        for item in value.get("data", {}).get("employees", [])
                        if isinstance(item, dict) and isinstance(item.get("employeeId"), int)
                    ]
                elif value.get("toolName") == "get_recent_meeting":
                    meetings = value.get("data", {}).get("meetings", [])
                    first = meetings[0] if isinstance(meetings, list) and meetings else {}
                    if isinstance(first, dict):
                        resolved_ids = [
                            item["employeeId"]
                            for item in first.get("participants", [])
                            if isinstance(item, dict)
                            and item.get("participantType") == "REQUIRED"
                            and isinstance(item.get("employeeId"), int)
                        ]
            organizer_id = canonical.get("organizerId")
            if not isinstance(organizer_id, int):
                raise ValueError("fixture canonical organizerId is invalid")
            employee_ids = sorted({organizer_id, *resolved_ids})
            requested_capacity = canonical.get("requestedMinimumCapacity")
            if not isinstance(requested_capacity, int):
                raise ValueError("fixture requestedMinimumCapacity is invalid")
            return ToolModelResponse(
                content=None,
                tool_calls=(
                    ModelToolCall(
                        id=f"call_fixture_busy_{request.iteration}",
                        name="get_employee_free_busy",
                        arguments=self._json(
                            {
                                "employeeIds": employee_ids,
                                "from": canonical["from"],
                                "to": canonical["to"],
                            }
                        ),
                    ),
                    ModelToolCall(
                        id=f"call_fixture_rooms_{request.iteration}",
                        name="search_available_rooms",
                        arguments=self._json(
                            {
                                "from": canonical["from"],
                                "to": canonical["to"],
                                "minimumCapacity": max(
                                    requested_capacity, len(employee_ids)
                                ),
                                "requiredFeatures": canonical["requiredFeatures"],
                                "limit": 50,
                            }
                        ),
                    ),
                ),
            )
        return ToolModelResponse(
            content="只读事实已经齐备，请执行确定性验证与求解。", tool_calls=()
        )

    @staticmethod
    def _json(value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _supervisor(message: str) -> dict[str, object]:
        if any(term in message for term in ("规则", "制度", "VIP", "政策")):
            return {
                "route": "POLICY",
                "intentHint": "QUERY_POLICY",
                "confidence": 0.99,
                "evidence": next(
                    term for term in ("规则", "制度", "VIP", "政策") if term in message
                ),
                "summary": "识别为会议规则查询。",
            }
        intent = "CANCEL_MEETING" if "取消" in message else (
            "MODIFY_MEETING" if any(term in message for term in ("改期", "调整", "修改", "改到"))
            else "CREATE_MEETING"
        )
        evidence = next(
            (
                term
                for term in ("取消", "改期", "调整", "修改", "改到", "安排", "预约", "帮")
                if term in message
            ),
            message[:1],
        )
        return {
            "route": "REQUIREMENT",
            "intentHint": intent,
            "confidence": 0.99,
            "evidence": evidence,
            "summary": "已路由到需求解析。",
        }

    def _requirement(self, message: str) -> dict[str, object]:
        intent = "CREATE_MEETING"
        if "取消" in message:
            intent = "CANCEL_MEETING"
        elif any(term in message for term in ("改期", "调整", "修改")):
            intent = "MODIFY_MEETING"
        elif any(term in message for term in ("共同空闲", "共同时间", "大家有空")):
            intent = "FIND_COMMON_TIME"
        elif "推荐" in message:
            intent = "RECOMMEND_ROOM"
        elif any(term in message for term in ("偏好", "以后", "避免")):
            intent = "UPDATE_PREFERENCE"
        target_meeting_id = (
            self._target_meeting_id(message)
            if intent in {"MODIFY_MEETING", "CANCEL_MEETING"}
            else None
        )
        duration = self._duration_minutes(message)
        participants = [
            {"name": name, "employeeId": None}
            for name in ("张三", "李四", "王经理")
            if name in message
        ]
        features = []
        if "大屏" in message or "大屏幕" in message:
            features.append("LARGE_SCREEN")
        if "白板" in message:
            features.append("WHITEBOARD")
        if "视频会议设备" in message or "视频会议" in message:
            features.append("VIDEO_CONFERENCE")
        window_start, window_end = self._time_window(message)
        title = "架构评审" if "架构评审" in message else "会议安排"
        evidence: list[dict[str, str]] = []
        for name in ("张三", "李四", "王经理"):
            if name in message:
                evidence.append(
                    {
                        "field": "requiredParticipantNames",
                        "source": name,
                        "provenance": "USER_EXPLICIT",
                    }
                )
        headcount = re.search(r"\d{1,4}\s*人", message)
        if headcount:
            evidence.append(
                {
                    "field": "minimumCapacity",
                    "source": headcount.group(0),
                    "provenance": "USER_EXPLICIT",
                }
            )
        for alias in ("大屏", "白板", "视频会议设备", "视频会议"):
            if alias in message:
                evidence.append(
                    {
                        "field": "requiredFeatures",
                        "source": alias,
                        "provenance": "USER_EXPLICIT",
                    }
                )
        return {
            "requirementDraft": {
                "intent": intent,
                "title": title if "架构评审" in message else None,
                "meetingType": "ARCHITECTURE_REVIEW" if title == "架构评审" else None,
                "durationMinutes": duration,
                "timeWindow": {
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                },
                "requiredParticipantNames": [item["name"] for item in participants],
                "optionalGroups": [],
                "requiredFeatures": features,
                "minimumCapacity": self._minimum_capacity(message, len(participants)),
                "preferredBuildings": [],
                "hardConstraints": [],
                "softConstraints": [],
                "targetMeetingId": target_meeting_id,
                "targetMeetingReference": (
                    next((term for term in ("刚才", "最近", "那个会议") if term in message), None)
                    if intent in {"MODIFY_MEETING", "CANCEL_MEETING"}
                    else None
                ),
                "fieldEvidence": evidence,
                "needsPolicy": False,
                "summary": "已提取用户明确表达的会议事实。",
            },
            "missingFields": [],
        }

    @staticmethod
    def _duration_minutes(message: str) -> int:
        minutes = re.search(r"(30|60|90|120)\s*分钟", message)
        if minutes:
            return int(minutes.group(1))
        hours = re.search(r"([1-4])\s*(?:个)?小时", message)
        if hours:
            return int(hours.group(1)) * 60
        return 60

    @staticmethod
    def _minimum_capacity(message: str, participant_count: int) -> int:
        explicit = re.search(r"(\d{1,4})\s*人", message)
        if explicit is None:
            return max(1, participant_count)
        return max(1, participant_count, int(explicit.group(1)))

    @staticmethod
    def _target_meeting_id(message: str) -> int | None:
        target = re.search(r"(?:会议\s*(?:ID)?\s*|#)(\d{1,9})", message, re.IGNORECASE)
        return int(target.group(1)) if target is not None else None

    def _time_window(self, message: str) -> tuple[datetime, datetime]:
        base = self.now
        if "下周三" in message:
            days_until = ((2 - base.weekday()) % 7) + 7
            date = (base + timedelta(days=days_until)).date()
        elif "明天" in message:
            date = (base + timedelta(days=1)).date()
        else:
            date = base.date()
        start_hour, end_hour = (13, 18) if "下午" in message else (9, 18)
        explicit = re.search(r"(?:下午)?\s*(1[0-8]|[1-9])点", message)
        if explicit:
            start_hour = int(explicit.group(1))
            if "下午" in message and start_hour < 12:
                start_hour += 12
            end_hour = min(start_hour + 5, 18)
        return (
            datetime.combine(date, datetime.min.time(), tzinfo=base.tzinfo).replace(
                hour=start_hour
            ),
            datetime.combine(date, datetime.min.time(), tzinfo=base.tzinfo).replace(hour=end_hour),
        )

    @staticmethod
    def _policy(message: str) -> dict[str, object]:
        if "VIP" in message:
            chunk_id = "chunk_vip_room_v1"
            summary = "VIP会议室仅用于重要客户或公司级会议，并应遵循审批要求。"
        elif "取消" in message or "改期" in message:
            chunk_id = "chunk_meeting_mutation_v1"
            summary = "会议改期或取消必须先展示草案并经过用户确认。"
        else:
            chunk_id = "chunk_architecture_review_v1"
            summary = "架构评审展示材料时应选择配备大屏的会议室。"
        return {
            "answerSummary": summary,
            "selectedChunkIds": [chunk_id],
            "confidence": 0.95,
            "constraints": [],
        }


def _fixture_canonical_context(request: ToolModelRequest) -> dict[str, object]:
    system = next(
        (message.content or "" for message in request.messages if message.role == "system"), ""
    )
    marker = "CANONICAL_CONTEXT="
    if marker not in system:
        raise ValueError("fixture scheduling prompt is missing canonical context")
    raw = system.split(marker, maxsplit=1)[1].split("\n", maxsplit=1)[0]
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("fixture canonical context is invalid")
    return value
