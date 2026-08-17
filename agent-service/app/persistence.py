"""Persistence of safe Run, Step and Tool Call metadata.

Only structured summaries are saved.  Raw prompts, model responses, JWTs,
service tokens, and retrieved document bodies never enter these rows.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.metadata import (
    AgentBusinessEvent,
    AgentLoopEvent,
    AgentMessage,
    AgentRun,
    AgentStep,
    AgentThread,
    AgentToolCall,
)
from app.schemas.agent import Intent, RunStatus


def safe_summary(value: str, limit: int = 180) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    redacted = re.sub(r"(?i)bearer\s+[\w.\-]+", "Bearer [REDACTED]", collapsed)
    redacted = re.sub(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", redacted)
    return redacted[:limit]


def safe_message_content(value: str, limit: int = 4000) -> str:
    """Retain user-visible text while removing obvious credentials."""

    normalized = value.strip()
    redacted = re.sub(r"(?i)bearer\s+[\w.\-]+", "Bearer [REDACTED]", normalized)
    redacted = re.sub(
        r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        redacted,
    )
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

    def record_message(
        self,
        *,
        thread_id: str,
        run_id: str,
        user_id: int,
        role: str,
        content: str,
        client_request_id: str,
        visible_payload: dict[str, object] | None = None,
    ) -> str:
        normalized_role = role.upper()
        if normalized_role not in {"USER", "ASSISTANT"}:
            raise ValueError("message role is invalid")
        safe_content = safe_message_content(content)
        if not safe_content:
            raise ValueError("message content is empty")
        raw_payload = _safe_visible_payload(visible_payload or {})
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        with Session(self.engine) as session:
            existing = session.scalar(
                select(AgentMessage).where(
                    AgentMessage.user_id == user_id,
                    AgentMessage.client_request_id == client_request_id,
                    AgentMessage.role == normalized_role,
                )
            )
            if existing is not None:
                if (
                    existing.thread_id != thread_id
                    or existing.run_id != run_id
                    or existing.content_text != safe_content
                ):
                    raise ValueError("clientRequestId was reused with different message content")
                return existing.message_id
            thread = session.get(AgentThread, thread_id, with_for_update=True)
            run = session.get(AgentRun, run_id)
            if thread is None or run is None:
                raise LookupError("message thread or run was not found")
            if (
                thread.user_id != user_id
                or run.user_id != user_id
                or run.thread_id != thread_id
            ):
                raise PermissionError("message does not belong to the current user")
            sequence_no = int(
                session.scalar(
                    select(func.max(AgentMessage.sequence_no)).where(
                        AgentMessage.thread_id == thread_id
                    )
                )
                or 0
            ) + 1
            message_id = f"msg_{uuid.uuid4().hex}"
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            session.add(
                AgentMessage(
                    message_id=message_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    user_id=user_id,
                    sequence_no=sequence_no,
                    role=normalized_role,
                    content_text=safe_content,
                    visible_payload=payload,
                    client_request_id=client_request_id,
                    created_at=now,
                )
            )
            thread.updated_at = now
            session.commit()
            return message_id

    def list_threads(
        self,
        *,
        user_id: int,
        page: int,
        size: int,
        latest_status: str | None = None,
    ) -> dict[str, object]:
        with Session(self.engine, expire_on_commit=False) as session:
            statement = select(AgentRun).where(AgentRun.user_id == user_id)
            runs = list(session.scalars(statement.order_by(AgentRun.created_at.desc())))
            latest_by_thread: dict[str, AgentRun] = {}
            for run in runs:
                latest_by_thread.setdefault(run.thread_id, run)
            pairs: list[tuple[AgentThread, AgentRun]] = []
            for thread_id, run in latest_by_thread.items():
                if latest_status is not None and run.status != latest_status:
                    continue
                thread = session.get(AgentThread, thread_id)
                if thread is not None and thread.user_id == user_id:
                    pairs.append((thread, run))
            pairs.sort(
                key=lambda pair: max(pair[0].updated_at, pair[1].created_at), reverse=True
            )
            total = len(pairs)
            selected = pairs[(page - 1) * size : page * size]
            return {
                "items": [self._thread_view(session, thread, run) for thread, run in selected],
                "total": total,
            }

    def get_thread(self, *, thread_id: str, user_id: int) -> dict[str, object] | None:
        with Session(self.engine, expire_on_commit=False) as session:
            thread = session.get(AgentThread, thread_id)
            if thread is None or thread.user_id != user_id:
                return None
            latest_run = session.scalar(
                select(AgentRun)
                .where(AgentRun.thread_id == thread_id, AgentRun.user_id == user_id)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
            if latest_run is None:
                return None
            messages = list(
                session.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.thread_id == thread_id,
                        AgentMessage.user_id == user_id,
                    )
                    .order_by(AgentMessage.sequence_no.asc())
                )
            )
            message_views: list[dict[str, object]]
            if messages:
                message_views = [
                    {
                        "messageId": message.message_id,
                        "runId": message.run_id,
                        "role": message.role,
                        "content": message.content_text,
                        "visiblePayload": message.visible_payload,
                        "createdAt": _timestamp(message.created_at),
                        "legacySummary": False,
                    }
                    for message in messages
                ]
            else:
                message_views = self._legacy_message_views(session, thread_id, user_id)
            return {
                "thread": self._thread_view(session, thread, latest_run),
                "messages": message_views,
            }

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
                    cache_hit_tokens=0,
                    cache_miss_tokens=0,
                    model_provider=None,
                    configured_model=None,
                    response_models=[],
                    prompt_version="meeting-agent-prompts-v12",
                    schema_version="meeting-agent-state-v7",
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
            existing = session.get(AgentToolCall, tool_call_id)
            if existing is not None:
                if (
                    existing.run_id != run_id
                    or existing.tool_name != tool_name
                    or existing.risk_level != risk_level
                    or existing.sanitized_args != sanitized_args
                    or existing.status != status
                    or existing.result_summary != safe_summary(result_summary, 1000)
                ):
                    raise ValueError("toolCallId was reused with different audit semantics")
                return
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

    def record_loop_event(
        self,
        *,
        run_id: str,
        sequence_no: int,
        phase: str,
        iteration: int,
        decision: str,
        feedback_codes: list[str],
        replan_count: int,
        remaining_model_calls: int,
        remaining_tool_calls: int,
        stop_reason: str | None,
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                AgentLoopEvent(
                    run_id=run_id,
                    sequence_no=sequence_no,
                    phase=safe_summary(phase, 16),
                    iteration=max(0, iteration),
                    decision=safe_summary(decision, 128),
                    feedback_codes=[safe_summary(item, 64) for item in feedback_codes[:16]],
                    replan_count=max(0, replan_count),
                    remaining_model_calls=max(0, remaining_model_calls),
                    remaining_tool_calls=max(0, remaining_tool_calls),
                    stop_reason=safe_summary(stop_reason or "", 64) or None,
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
        runtime: dict[str, object] | None = None,
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
            _apply_runtime(run, runtime)
            run.finished_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            session.commit()

    def update_run_progress(
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
        runtime: dict[str, object] | None = None,
    ) -> None:
        """Persist a paused/non-terminal Run without changing its immutable trace.

        ``finished_at`` intentionally remains untouched here.  A paused HITL
        or HOT request is a durable in-progress state, not a completed run.
        """

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
            _apply_runtime(run, runtime)
            session.commit()

    def record_business_event_once(
        self,
        *,
        event_id: str,
        run_id: str,
        request_no: str,
        event_type: str,
        payload: dict[str, object],
    ) -> bool:
        """Store an MQ callback exactly once at the local metadata boundary.

        Java delivery is at-least-once.  The event primary key makes a repeat
        callback a harmless successful no-op without claiming end-to-end
        exactly-once processing.
        """

        with Session(self.engine) as session:
            session.add(
                AgentBusinessEvent(
                    event_id=event_id,
                    run_id=run_id,
                    request_no=request_no,
                    event_type=safe_summary(event_type, 64),
                    payload_json=payload,
                    processed_at=datetime.now(ZoneInfo("Asia/Shanghai")),
                )
            )
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False

    def has_business_event(self, event_id: str) -> bool:
        with Session(self.engine) as session:
            return session.get(AgentBusinessEvent, event_id) is not None

    def highest_step_sequence(self, run_id: str) -> int:
        with Session(self.engine) as session:
            value = session.scalar(
                select(func.max(AgentStep.sequence_no)).where(AgentStep.run_id == run_id)
            )
            return int(value or 0)

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
            loop_events = list(
                session.scalars(
                    select(AgentLoopEvent)
                    .where(AgentLoopEvent.run_id == run_id)
                    .order_by(AgentLoopEvent.sequence_no.asc())
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
                "loopEvents": [
                    {
                        "phase": event.phase,
                        "iteration": event.iteration,
                        "decision": event.decision,
                        "feedbackCodes": event.feedback_codes,
                        "replanCount": event.replan_count,
                        "remainingBudget": {
                            "modelCalls": event.remaining_model_calls,
                            "toolCalls": event.remaining_tool_calls,
                        },
                        "stopReason": event.stop_reason,
                        "createdAt": _timestamp(event.created_at),
                    }
                    for event in loop_events
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
            "modelProvider": run.model_provider,
            "configuredModel": run.configured_model,
            "responseModels": run.response_models,
            "promptVersion": run.prompt_version,
            "schemaVersion": run.schema_version,
            "tokenUsage": {
                "inputTokens": run.input_tokens,
                "outputTokens": run.output_tokens,
                "cacheHitTokens": run.cache_hit_tokens,
                "cacheMissTokens": run.cache_miss_tokens,
            },
            "durationMs": run.duration_ms,
            "errorCode": run.error_code,
            "createdAt": _timestamp(run.created_at),
            "finishedAt": _timestamp(run.finished_at),
        }

    @staticmethod
    def _thread_view(
        session: Session, thread: AgentThread, run: AgentRun
    ) -> dict[str, object]:
        recent_messages = list(
            session.scalars(
                select(AgentMessage)
                .where(AgentMessage.thread_id == thread.thread_id)
                .order_by(AgentMessage.sequence_no.desc())
                .limit(20)
            )
        )
        latest_user = next(
            (item.content_text for item in recent_messages if item.role == "USER"),
            None,
        )
        latest_assistant = next(
            (item.content_text for item in recent_messages if item.role == "ASSISTANT"), None
        )
        return {
            "threadId": thread.thread_id,
            "title": thread.title,
            "latestRunId": run.run_id,
            "latestStatus": run.status,
            "intent": run.intent,
            "questionPreview": latest_user or run.question_summary,
            "answerPreview": latest_assistant or run.answer_summary,
            "actionRequired": run.status == RunStatus.WAITING_CONFIRMATION.value,
            "updatedAt": _timestamp(max(thread.updated_at, run.created_at)),
        }

    @staticmethod
    def _legacy_message_views(
        session: Session, thread_id: str, user_id: int
    ) -> list[dict[str, object]]:
        runs = list(
            session.scalars(
                select(AgentRun)
                .where(AgentRun.thread_id == thread_id, AgentRun.user_id == user_id)
                .order_by(AgentRun.created_at.asc())
            )
        )
        result: list[dict[str, object]] = []
        for run in runs:
            result.append(
                {
                    "messageId": f"legacy_user_{run.run_id}",
                    "runId": run.run_id,
                    "role": "USER",
                    "content": run.question_summary,
                    "visiblePayload": {"status": run.status},
                    "createdAt": _timestamp(run.created_at),
                    "legacySummary": True,
                }
            )
            if run.answer_summary:
                result.append(
                    {
                        "messageId": f"legacy_assistant_{run.run_id}",
                        "runId": run.run_id,
                        "role": "ASSISTANT",
                        "content": run.answer_summary,
                        "visiblePayload": {"status": run.status},
                        "createdAt": _timestamp(run.finished_at or run.created_at),
                        "legacySummary": True,
                    }
                )
        return result


def _apply_runtime(run: AgentRun, runtime: dict[str, object] | None) -> None:
    if runtime is None:
        return
    provider = runtime.get("modelProvider")
    model = runtime.get("configuredModel")
    run.model_provider = provider if isinstance(provider, str) else None
    run.configured_model = model if isinstance(model, str) else None
    response_models = runtime.get("responseModels")
    run.response_models = (
        [item for item in response_models if isinstance(item, str)][:12]
        if isinstance(response_models, list)
        else []
    )
    run.prompt_version = str(runtime.get("promptVersion") or "meeting-agent-prompts-v12")[:64]
    run.schema_version = str(runtime.get("schemaVersion") or "meeting-agent-state-v7")[:64]
    run.input_tokens = _runtime_int(runtime.get("inputTokens"))
    run.output_tokens = _runtime_int(runtime.get("outputTokens"))
    run.cache_hit_tokens = _runtime_int(runtime.get("cacheHitTokens"))
    run.cache_miss_tokens = _runtime_int(runtime.get("cacheMissTokens"))


def _runtime_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


_FORBIDDEN_VISIBLE_KEYS = {
    "authorization",
    "confirmationtoken",
    "jwt",
    "password",
    "servicetoken",
    "token",
}


def _safe_visible_payload(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _safe_visible_payload(item)
            for key, item in value.items()
            if str(key).replace("_", "").lower() not in _FORBIDDEN_VISIBLE_KEYS
        }
    if isinstance(value, list):
        return [_safe_visible_payload(item) for item in value[:100]]
    if isinstance(value, str):
        return safe_message_content(value, 1000)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return safe_summary(str(value), 200)
