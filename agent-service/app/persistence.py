"""Persistence of safe Run, Step and Tool Call metadata.

Only structured summaries are saved.  Raw prompts, model responses, JWTs,
service tokens, and retrieved document bodies never enter these rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models.metadata import AgentRun, AgentStep, AgentThread, AgentToolCall
from app.schemas.agent import Intent, RunStatus


def safe_summary(value: str, limit: int = 180) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    redacted = re.sub(r"(?i)bearer\s+[\w.\-]+", "Bearer [REDACTED]", collapsed)
    redacted = re.sub(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", redacted)
    return redacted[:limit]


def question_summary(message: str) -> str:
    """Describe request shape without retaining the user-provided body."""

    categories: list[str] = []
    if "架构评审" in message:
        categories.append("架构评审")
    if "VIP" in message.upper() or "规则" in message or "制度" in message:
        categories.append("规则查询")
    if "分钟" in message or "小时" in message:
        categories.append("时长")
    if "大屏" in message or "白板" in message:
        categories.append("设备")
    category_text = "、".join(categories) if categories else "一般会议"
    return f"用户提交{category_text}任务（正文长度={len(message)}）"


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return value.isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class PersistedRun:
    run_id: str
    thread_id: str
    trace_id: str
    user_id: int
    status: str


@dataclass
class MetadataRepository:
    engine: Engine

    def ensure_thread(self, *, thread_id: str, user_id: int, title: str) -> None:
        with Session(self.engine) as session:
            existing = session.get(AgentThread, thread_id)
            if existing is None:
                session.add(
                    AgentThread(
                        thread_id=thread_id, user_id=user_id, title=safe_summary(title, 128)
                    )
                )
            elif existing.user_id != user_id:
                raise PermissionError("thread does not belong to the current user")
            session.commit()

    def create_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        trace_id: str,
        user_id: int,
        question_summary: str,
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                AgentRun(
                    run_id=run_id,
                    thread_id=thread_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    intent="UNKNOWN",
                    status=RunStatus.RUNNING.value,
                    question_summary=safe_summary(question_summary, 500),
                    answer_summary=None,
                    model_call_count=0,
                    tool_call_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    duration_ms=None,
                    error_code=None,
                    finished_at=None,
                )
            )
            session.commit()

    def record_step(
        self,
        *,
        step_id: str,
        run_id: str,
        sequence_no: int,
        agent_name: str,
        node_name: str,
        status: str,
        input_summary: str,
        output_summary: str,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                AgentStep(
                    step_id=step_id,
                    run_id=run_id,
                    sequence_no=sequence_no,
                    agent_name=agent_name,
                    node_name=node_name,
                    status=status,
                    input_summary=safe_summary(input_summary, 500),
                    output_summary=safe_summary(output_summary, 1000),
                    duration_ms=max(0, duration_ms),
                    error_code=error_code,
                )
            )
            session.commit()

    def record_tool_call(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        tool_name: str,
        risk_level: str,
        sanitized_args: dict[str, object],
        result_summary: str,
        status: str,
        duration_ms: int,
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                AgentToolCall(
                    tool_call_id=tool_call_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    risk_level=risk_level,
                    sanitized_args=sanitized_args,
                    result_summary=safe_summary(result_summary, 1000),
                    status=status,
                    duration_ms=max(0, duration_ms),
                )
            )
            session.commit()

    def complete_run(
        self,
        *,
        run_id: str,
        intent: Intent | None,
        status: RunStatus,
        answer_summary: str | None,
        model_call_count: int,
        tool_call_count: int,
        duration_ms: int,
        error_code: str | None,
    ) -> None:
        with Session(self.engine) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise LookupError("agent run was not found")
            run.intent = intent.value if intent is not None else "UNKNOWN"
            run.status = status.value
            run.answer_summary = safe_summary(answer_summary or "", 1000) or None
            run.model_call_count = model_call_count
            run.tool_call_count = tool_call_count
            run.duration_ms = max(0, duration_ms)
            run.error_code = error_code
            run.finished_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            session.commit()

    def get_run(self, run_id: str) -> AgentRun | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(AgentRun, run_id)

    def get_trace(self, run_id: str) -> dict[str, object] | None:
        with Session(self.engine, expire_on_commit=False) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return None
            steps = list(
                session.scalars(
                    select(AgentStep)
                    .where(AgentStep.run_id == run_id)
                    .order_by(AgentStep.sequence_no.asc())
                )
            )
            tool_calls = list(
                session.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == run_id)
                    .order_by(AgentToolCall.created_at.asc())
                )
            )
            return {
                "run": self._run_view(run),
                "steps": [
                    {
                        "stepId": step.step_id,
                        "sequenceNo": step.sequence_no,
                        "agentName": step.agent_name,
                        "nodeName": step.node_name,
                        "status": step.status,
                        "summary": step.output_summary,
                        "durationMs": step.duration_ms,
                        "errorCode": step.error_code,
                        "createdAt": _timestamp(step.created_at),
                    }
                    for step in steps
                ],
                "toolCalls": [
                    {
                        "toolCallId": call.tool_call_id,
                        "toolName": call.tool_name,
                        "riskLevel": call.risk_level,
                        "sanitizedArgs": call.sanitized_args,
                        "resultSummary": call.result_summary,
                        "status": call.status,
                        "durationMs": call.duration_ms,
                        "createdAt": _timestamp(call.created_at),
                    }
                    for call in tool_calls
                ],
            }

    @staticmethod
    def _run_view(run: AgentRun) -> dict[str, object]:
        return {
            "runId": run.run_id,
            "threadId": run.thread_id,
            "traceId": run.trace_id,
            "userId": run.user_id,
            "intent": run.intent,
            "status": run.status,
            "questionSummary": run.question_summary,
            "answerSummary": run.answer_summary,
            "modelCallCount": run.model_call_count,
            "toolCallCount": run.tool_call_count,
            "durationMs": run.duration_ms,
            "errorCode": run.error_code,
            "createdAt": _timestamp(run.created_at),
            "finishedAt": _timestamp(run.finished_at),
        }
