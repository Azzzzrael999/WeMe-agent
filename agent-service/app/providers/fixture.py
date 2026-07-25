"""Deterministic model fixture used by every automated test and local smoke."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.providers.base import ModelRequest


@dataclass(frozen=True)
class FixtureModelProvider:
    now: datetime

    def complete(self, request: ModelRequest) -> str:
        message = request.user_prompt
        if request.agent_name == "supervisor":
            return self._json(self._supervisor(message))
        if request.agent_name == "requirement":
            return self._json(self._requirement(message))
        if request.agent_name == "policy":
            return self._json(self._policy(message))
        if request.agent_name == "scheduling":
            return self._json(
                {
                    "toolNames": ["resolve_employees"],
                    "summary": "先解析必需参会者，再交由后续确定性调度处理。",
                }
            )
        raise ValueError(f"unsupported fixture agent: {request.agent_name}")

    @staticmethod
    def _json(value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _supervisor(message: str) -> dict[str, object]:
        if any(term in message for term in ("规则", "制度", "VIP", "政策")):
            return {"route": "POLICY", "summary": "识别为会议规则查询。"}
        return {"route": "REQUIREMENT", "summary": "已路由到需求解析。"}

    def _requirement(self, message: str) -> dict[str, object]:
        intent = "CREATE_MEETING"
        if "取消" in message:
            intent = "CANCEL_MEETING"
        elif any(term in message for term in ("改期", "调整", "修改")):
            intent = "MODIFY_MEETING"
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
        window_start, window_end = self._time_window(message)
        title = "架构评审" if "架构评审" in message else "会议安排"
        return {
            "meetingRequest": {
                "intent": intent,
                "title": title,
                "meetingType": "ARCHITECTURE_REVIEW" if title == "架构评审" else "GENERAL",
                "durationMinutes": duration,
                "timeWindow": {
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                },
                "requiredParticipants": participants,
                "optionalGroups": [],
                "requiredFeatures": features,
                "minimumCapacity": max(1, len(participants)),
                "preferredBuildings": [],
                "hardConstraints": [],
                "softConstraints": [],
                "createVideoConference": False,
                "targetMeetingId": None,
            },
            "missingFields": [] if participants else ["requiredParticipants"],
            "needsPolicy": False,
            "summary": "已提取时长、必需参会者和会议室设备约束。",
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
        else:
            chunk_id = "chunk_architecture_review_v1"
            summary = "架构评审展示材料时应选择配备大屏的会议室。"
        return {
            "answerSummary": summary,
            "selectedChunkIds": [chunk_id],
            "confidence": 0.95,
            "constraints": [],
        }
