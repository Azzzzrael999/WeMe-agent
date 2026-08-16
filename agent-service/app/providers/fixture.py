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

    @property
    def network_calls(self) -> int:
        """Fixture completions are entirely local and deterministic."""

        return 0

    def complete(self, request: ModelRequest) -> ModelCompletion:
        message = request.user_prompt
        if request.agent_name == "supervisor":
            if request.schema_name == "ClarificationResponse":
                return ModelCompletion(
                    content=self._json(self._clarification(message)), model="fixture"
                )
            return ModelCompletion(content=self._json(self._supervisor(message)), model="fixture")
        if request.agent_name == "requirement" and request.schema_name == "PostMeetingDraft":
            return ModelCompletion(
                content=self._json(self._post_meeting_draft(message)), model="fixture"
            )
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

        observations = [message for message in request.messages if message.role == "tool"]
        successful_tool_names: set[str] = set()
        for observation in observations:
            try:
                observed_value = json.loads(observation.content or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(observed_value.get("data"), dict) and isinstance(
                observed_value.get("toolName"), str
            ):
                successful_tool_names.add(observed_value["toolName"])
        canonical = _fixture_canonical_context(request)
        intent = canonical.get("intent")
        target_meeting_id = canonical.get("targetMeetingId")
        participant_names = canonical.get("participantNames")
        if not isinstance(participant_names, list):
            raise ValueError("fixture participantNames is invalid")
        if participant_names and "resolve_employees" not in successful_tool_names:
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
            and "get_recent_meeting" not in successful_tool_names
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
        if not {
            "get_employee_free_busy",
            "search_available_rooms",
        }.issubset(successful_tool_names):
            canonical_participant_ids = canonical.get("participantIds")
            resolved_ids: list[int] = (
                [item for item in canonical_participant_ids if isinstance(item, int)]
                if isinstance(canonical_participant_ids, list)
                else []
            )
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
                elif value.get("toolName") == "get_recent_meeting" and not resolved_ids:
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
                                "excludeMeetingId": canonical.get("excludeMeetingId"),
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
                                "excludeMeetingId": canonical.get("excludeMeetingId"),
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
            "MODIFY_MEETING" if any(
                term in message
                for term in (
                    "改期",
                    "调整",
                    "修改",
                    "改到",
                    "异常重排",
                    "资源失效",
                    "会议室不可用",
                    "会议室已失效",
                )
            )
            else "CREATE_MEETING"
        )
        evidence = next(
            (
                term
                for term in (
                    "取消",
                    "改期",
                    "调整",
                    "修改",
                    "改到",
                    "异常重排",
                    "资源失效",
                    "会议室不可用",
                    "会议室已失效",
                    "安排",
                    "预约",
                    "帮",
                )
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

    @staticmethod
    def _clarification(message: str) -> dict[str, object]:
        prefix = "CLARIFICATION_CONTRACT="
        raw = message.removeprefix(prefix)
        try:
            contract = json.loads(raw)
        except json.JSONDecodeError:
            return {"message": "我还需要你补充一些会议信息后才能继续。"}
        fallback = contract.get("fallbackMessage") if isinstance(contract, dict) else None
        return {
            "message": fallback
            if isinstance(fallback, str) and fallback
            else "我还需要你补充一些会议信息后才能继续。"
        }

    def _requirement(self, message: str) -> dict[str, object]:
        runtime_message = "USER_MESSAGE=" in message
        if runtime_message:
            message = message.rsplit("USER_MESSAGE=", maxsplit=1)[1]
        intent = "CREATE_MEETING"
        if "取消" in message:
            intent = "CANCEL_MEETING"
        elif any(
            term in message
            for term in (
                "改期",
                "调整",
                "修改",
                "改到",
                "异常重排",
                "资源失效",
                "会议室不可用",
                "会议室已失效",
            )
        ):
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
        if duration is None and not runtime_message:
            duration = 60
        participants = [
            {"name": name, "employeeId": None}
            for name in ("张三", "李四", "王经理")
            if name in message
        ]
        features = []
        if "大屏" in message or "大屏幕" in message:
            features.append("LARGE_SCREEN")
        if "投屏" in message and "LARGE_SCREEN" not in features:
            features.append("LARGE_SCREEN")
        if "白板" in message:
            features.append("WHITEBOARD")
        if "视频会议设备" in message or "视频会议" in message:
            features.append("VIDEO_CONFERENCE")
        mutation_parts = (
            re.split(r"改到|调整到|移到", message, maxsplit=1)
            if intent == "MODIFY_MEETING"
            else [message]
        )
        target_reference = (
            mutation_parts[0][-240:]
            if intent == "MODIFY_MEETING" and len(mutation_parts) == 2
            else next(
                (term for term in ("刚才", "最近", "那个会议") if term in message),
                None,
            )
            if intent in {"MODIFY_MEETING", "CANCEL_MEETING"}
            else None
        )
        pending_start_at = (
            self._mutation_pending_start(mutation_parts[0], mutation_parts[1])
            if intent == "MODIFY_MEETING" and len(mutation_parts) == 2
            else None
        )
        window = self._time_window(mutation_parts[0] if pending_start_at is not None else message)
        if window is None and not runtime_message:
            window = (
                self.now.replace(hour=9, minute=0, second=0, microsecond=0),
                self.now.replace(hour=18, minute=0, second=0, microsecond=0),
            )
        title = "架构评审" if "架构评审" in message else "会议安排"
        preferred_buildings = [
            building
            for building in ("总部楼", "研发楼", "创新楼", "协作楼")
            if building in message
        ]
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
                "timeWindow": (
                    {"start": window[0].isoformat(), "end": window[1].isoformat()}
                    if window is not None
                    else None
                ),
                "pendingStartAt": (
                    pending_start_at.isoformat() if pending_start_at is not None else None
                ),
                "pendingStartAmbiguous": False,
                "requiredParticipantNames": [item["name"] for item in participants],
                "participantScope": (
                    "MY_DEPARTMENT"
                    if any(
                        value in message
                        for value in (
                            "我的小组",
                            "同组人员",
                            "小组会议",
                            "组内人员",
                            "组内的人",
                        )
                    )
                    else "ORGANIZER_ONLY"
                    if any(value in message for value in ("只有我", "我自己参加", "就我一个人"))
                    else None
                ),
                "optionalGroups": [],
                "requiredFeatures": features,
                "minimumCapacity": (
                    self._minimum_capacity(message, len(participants))
                    if runtime_message
                    else self._minimum_capacity(message, len(participants))
                    or max(1, len(participants))
                ),
                "preferredBuildings": preferred_buildings,
                "hardConstraints": [],
                "softConstraints": self._soft_constraints(message),
                "targetMeetingId": target_meeting_id,
                "targetMeetingReference": target_reference,
                "fieldEvidence": evidence,
                "needsPolicy": False,
                "summary": "已提取用户明确表达的会议事实。",
            },
            "missingFields": [],
        }

    def _post_meeting_draft(self, message: str) -> dict[str, object]:
        marker = "POST_MEETING_INPUT="
        if not message.startswith(marker):
            raise ValueError("fixture post-meeting prompt is invalid")
        payload = json.loads(message.removeprefix(marker))
        if not isinstance(payload, dict):
            raise ValueError("fixture post-meeting payload is invalid")
        transcript = payload.get("transcript")
        participants = payload.get("participants")
        if not isinstance(transcript, str) or not isinstance(participants, list):
            raise ValueError("fixture post-meeting payload is invalid")
        participant_ids = {
            item["displayName"]: item["employeeId"]
            for item in participants
            if isinstance(item, dict)
            and isinstance(item.get("displayName"), str)
            and isinstance(item.get("employeeId"), int)
        }
        sentences = [
            item.strip(" \t\r\n，,：:")
            for item in re.split(r"[。；;\r\n]+", transcript)
            if item.strip(" \t\r\n，,：:")
        ]
        decisions: list[dict[str, object]] = []
        action_items: list[dict[str, object]] = []
        for sentence in sentences:
            if any(keyword in sentence for keyword in ("决定", "结论")):
                decisions.append({"content": sentence[:1000], "rationale": None})
            if "负责" not in sentence:
                continue
            assignee_id = next(
                (
                    employee_id
                    for display_name, employee_id in participant_ids.items()
                    if display_name in sentence.split("负责", maxsplit=1)[0]
                ),
                None,
            )
            title = sentence.split("负责", maxsplit=1)[1]
            title = re.split(r"[，,]?\s*截止", title, maxsplit=1)[0].strip(" ，,")
            if not title:
                continue
            due_at: str | None = None
            deadline = re.search(
                r"(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?",
                sentence,
            )
            if deadline is not None:
                due_at = f"{deadline.group(1)}T{deadline.group(2)}:00+08:00"
            action_items.append(
                {
                    "title": title[:200],
                    "description": None,
                    "assigneeEmployeeId": assignee_id,
                    "dueAt": due_at,
                }
            )
        title = payload.get("title")
        meeting_type = payload.get("meetingType")
        start_at = payload.get("startAt")
        end_at = payload.get("endAt")
        background = (
            f"{title}（{meeting_type}）于 {start_at} 至 {end_at} 召开。"
            if all(isinstance(item, str) for item in (title, meeting_type, start_at, end_at))
            else "会议已按提交的事实快照召开。"
        )
        conclusion = (
            "；".join(str(item["content"]) for item in decisions)[:2000]
            if decisions
            else "会议记录未包含明确决策。"
        )
        return {
            "minutes": {
                "background": background[:2000],
                "discussionSummary": transcript.strip()[:10000],
                "conclusion": conclusion,
            },
            "decisions": decisions[:20],
            "actionItems": action_items[:50],
        }

    @staticmethod
    def _duration_minutes(message: str) -> int | None:
        minutes = re.search(r"(30|60|90|120)\s*分钟", message)
        if minutes:
            return int(minutes.group(1))
        hours = re.search(r"([1-4])\s*(?:个)?小时", message)
        if hours:
            return int(hours.group(1)) * 60
        return None

    @staticmethod
    def _minimum_capacity(message: str, participant_count: int) -> int | None:
        explicit = re.search(r"(\d{1,4})\s*人", message)
        if explicit is None:
            return participant_count or None
        return max(1, participant_count, int(explicit.group(1)))

    @staticmethod
    def _target_meeting_id(message: str) -> int | None:
        target = re.search(
            r"(?:会议\s*(?:ID)?|meeting\s*id|meetingId|#)\s*[:：#]?\s*(\d{1,9})",
            message,
            re.IGNORECASE,
        )
        return int(target.group(1)) if target is not None else None

    def _time_window(self, message: str) -> tuple[datetime, datetime] | None:
        base = self.now
        absolute_date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", message)
        if absolute_date:
            date = datetime(
                int(absolute_date.group(1)),
                int(absolute_date.group(2)),
                int(absolute_date.group(3)),
            ).date()
        elif "下周三" in message:
            days_until = ((2 - base.weekday()) % 7) + 7
            date = (base + timedelta(days=days_until)).date()
        elif "明天" in message:
            date = (base + timedelta(days=1)).date()
        elif day_only := re.search(r"(?<!月)(?<!\d)(\d{1,2})号", message):
            date = base.date().replace(day=int(day_only.group(1)))
        else:
            date = base.date()
        has_time_expression = any(
            value in message
            for value in ("上午", "早上", "中午", "下午", "晚上", "点", ":")
        )
        if not has_time_expression:
            return None
        start_hour, start_minute, end_hour, end_minute = (9, 0, 18, 0)
        crosses_midnight = False
        if "下午" in message:
            start_hour, end_hour = 12, 18
        elif "上午" in message or "早上" in message:
            start_hour, end_hour = 6, 12
        elif "中午" in message:
            start_hour, end_hour = 11, 14
        elif "晚上" in message:
            start_hour, end_hour, crosses_midnight = 18, 6, True
        explicit_range = re.search(
            r"(?:下午)?\s*(\d{1,2})(?::(\d{2})|点)\s*(?:到|至|-)\s*"
            r"(\d{1,2})(?::(\d{2})|点)",
            message,
        )
        if explicit_range:
            start_hour = int(explicit_range.group(1))
            start_minute = int(explicit_range.group(2) or 0)
            end_hour = int(explicit_range.group(3))
            end_minute = int(explicit_range.group(4) or 0)
            if "下午" in message and start_hour < 12:
                start_hour += 12
            if "下午" in message and end_hour < 12:
                end_hour += 12
        else:
            if any(value in message for value in ("最好", "尽量", "优先")):
                return None
            explicit = re.search(r"(?:下午)?\s*(1[0-8]|[1-9])点", message)
            if explicit:
                start_hour = int(explicit.group(1))
                if "下午" in message and start_hour < 12:
                    start_hour += 12
                end_hour = min(start_hour + 5, 18)
        start = datetime.combine(date, datetime.min.time(), tzinfo=base.tzinfo).replace(
                hour=start_hour, minute=start_minute
            )
        end = datetime.combine(date, datetime.min.time(), tzinfo=base.tzinfo).replace(
                hour=end_hour, minute=end_minute
            )
        if crosses_midnight:
            end += timedelta(days=1)
        return start, end

    def _mutation_pending_start(self, before: str, after: str) -> datetime | None:
        day = re.search(r"(?<!月)(?<!\d)(\d{1,2})号", after)
        if day is None:
            return None
        clock_source = before if "同一时间" in after else after
        clock = re.search(
            r"(上午|早上|中午|下午|晚上)?\s*"
            r"(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点(半)?",
            clock_source,
        )
        if clock is None:
            return None
        hour = self._hour_value(clock.group(2))
        if hour is None:
            return None
        period = clock.group(1)
        if (period in {"下午", "晚上"} and hour < 12) or (
            period == "中午" and hour < 11
        ):
            hour += 12
        return self.now.replace(
            day=int(day.group(1)),
            hour=hour,
            minute=30 if clock.group(3) else 0,
            second=0,
            microsecond=0,
        )

    @staticmethod
    def _hour_value(value: str) -> int | None:
        if value.isdigit():
            return int(value)
        digits = {
            "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
            "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        }
        if value == "十":
            return 10
        if "十" in value:
            left, right = value.split("十", 1)
            return digits.get(left, 1) * 10 + digits.get(right, 0)
        return digits.get(value)

    @staticmethod
    def _soft_constraints(message: str) -> list[dict[str, object]]:
        preferred = re.search(
            r"(?:最好|尽量|优先)(?:是|在)?(?:下午)?\s*(\d{1,2})(?::(\d{2})|点)",
            message,
        )
        if preferred is None:
            return []
        hour = int(preferred.group(1))
        if "下午" in preferred.group(0) and hour < 12:
            hour += 12
        return [
            {
                "type": "PREFER_START_AT",
                "value": f"{hour:02d}:{int(preferred.group(2) or 0):02d}",
                "weight": 20,
            }
        ]

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
