"""Run persistence, access control, resume messages, and SSE framing payloads."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator

from fastapi import HTTPException, status

from app.persistence import MetadataRepository
from app.schemas.agent import (
    AgentResumeRequest,
    AgentState,
    Participant,
    RunStatus,
)
from app.security import AgentContext
from app.workflow import (
    WorkflowError,
    WorkflowRun,
)

logger = logging.getLogger(__name__)


def _load_checkpoint_or_503(
    *, workflow: WorkflowRun, thread_id: str, run_id: str
) -> AgentState | None:
    try:
        return workflow.load_state(thread_id=thread_id, run_id=run_id)
    except WorkflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_CHECKPOINT_UNAVAILABLE",
        ) from exc


def _finish_stream(
    *,
    repository: MetadataRepository,
    workflow: WorkflowRun,
    fallback_state: AgentState,
    duration_ms: int,
    client_request_id: str,
) -> Iterator[str]:
    final_state = workflow.latest_state or fallback_state
    if final_state.status in {RunStatus.WAITING_CONFIRMATION, RunStatus.WAITING_BUSINESS_RESULT}:
        repository.update_run_progress(
            run_id=final_state.run_id,
            intent=final_state.intent,
            status=final_state.status,
            answer_summary=final_state.answer_summary,
            model_call_count=final_state.model_call_count,
            tool_call_count=final_state.tool_call_count,
            duration_ms=duration_ms,
            error_code=None,
            runtime=_runtime_stats(final_state),
        )
        _record_assistant_message(
            repository=repository,
            state=final_state,
            client_request_id=client_request_id,
        )
        return
    repository.complete_run(
        run_id=final_state.run_id,
        intent=final_state.intent,
        status=final_state.status,
        answer_summary=final_state.answer_summary,
        model_call_count=final_state.model_call_count,
        tool_call_count=final_state.tool_call_count,
        duration_ms=duration_ms,
        error_code=None,
        runtime=_runtime_stats(final_state),
    )
    _record_assistant_message(
        repository=repository,
        state=final_state,
        client_request_id=client_request_id,
    )
    yield _sse(
        "run.completed",
        {
            "runId": final_state.run_id,
            "status": final_state.status.value,
            "answerSummary": final_state.answer_summary or "已完成结构化处理",
            "citations": [
                citation.model_dump(by_alias=True, mode="json")
                for citation in final_state.citations
            ],
        },
    )


def _fail_stream(
    *,
    repository: MetadataRepository,
    workflow: WorkflowRun,
    fallback_state: AgentState,
    duration_ms: int,
    error: WorkflowError,
    client_request_id: str,
) -> Iterator[str]:
    failed_state = workflow.latest_state or fallback_state
    terminal_code = (
        "BUDGET_EXHAUSTED"
        if error.code in {"AGENT_STEP_LIMIT_EXCEEDED", "BUDGET_EXHAUSTED"}
        else error.code
    )
    repository.complete_run(
        run_id=failed_state.run_id,
        intent=failed_state.intent,
        status=RunStatus.FAILED,
        answer_summary=None,
        model_call_count=failed_state.model_call_count,
        tool_call_count=failed_state.tool_call_count,
        duration_ms=duration_ms,
        error_code=terminal_code,
        runtime=_runtime_stats(failed_state),
    )
    _record_assistant_message(
        repository=repository,
        state=failed_state,
        client_request_id=client_request_id,
        content=error.message,
        error_code=terminal_code,
    )
    yield _sse(
        "run.failed",
        {
            "runId": failed_state.run_id,
            "status": RunStatus.FAILED.value,
            "errorCode": terminal_code,
            "message": error.message,
        },
    )


def _runtime_stats(state: AgentState) -> dict[str, object]:
    return {
        "modelProvider": state.model_provider,
        "configuredModel": state.configured_model,
        "responseModels": state.response_models,
        "promptVersion": state.prompt_version,
        "schemaVersion": state.schema_version,
        "inputTokens": state.input_tokens,
        "outputTokens": state.output_tokens,
        "cacheHitTokens": state.cache_hit_tokens,
        "cacheMissTokens": state.cache_miss_tokens,
    }


def _resume_message_key(run_id: str, generation: int, action: object) -> str:
    digest = hashlib.sha256(f"{run_id}:{generation}:{action}".encode()).hexdigest()
    return f"resume_{digest[:48]}"


def _resume_user_message(body: AgentResumeRequest) -> str:
    labels = {
        "ACCEPT": "确认执行会议草案",
        "EDIT": "编辑会议草案并重新校验",
        "REJECT": "拒绝会议草案",
    }
    message = labels[body.action.value]
    if body.feedback is not None and body.feedback.strip():
        return f"{message}：{body.feedback.strip()}"
    return message


def _record_assistant_message(
    *,
    repository: MetadataRepository,
    state: AgentState,
    client_request_id: str,
    content: str | None = None,
    error_code: str | None = None,
) -> None:
    visible_content = content or state.answer_summary or _paused_answer(state.status)
    payload: dict[str, object] = {
        "status": state.status.value,
        "citations": [
            citation.model_dump(by_alias=True, mode="json") for citation in state.citations
        ],
    }
    if state.unsat_analysis is not None:
        payload["unsatAnalysis"] = state.unsat_analysis.model_dump(by_alias=True, mode="json")
    if error_code is not None:
        payload["errorCode"] = error_code
    try:
        repository.record_message(
            thread_id=state.thread_id,
            run_id=state.run_id,
            user_id=state.user_id,
            role="ASSISTANT",
            content=visible_content,
            client_request_id=client_request_id,
            visible_payload=payload,
        )
    except Exception:
        logger.exception("Unable to persist visible Agent reply for run %s", state.run_id)


def _paused_answer(run_status: RunStatus) -> str:
    if run_status is RunStatus.WAITING_CONFIRMATION:
        return "已生成会议草案，等待你的确认。"
    if run_status is RunStatus.WAITING_BUSINESS_RESULT:
        return "预约请求已受理，正在等待业务处理结果。"
    if run_status is RunStatus.WAITING_USER_INPUT:
        return "会议需求已保存，请补充缺失信息后继续。"
    return "本次智能编排已更新。"


def _baseline_resolved_employees(state: AgentState) -> list[Participant]:
    draft = state.requirement_draft
    if draft is None:
        return []
    names = set(draft.required_participant_names)
    return [item for item in state.resolved_employees if item.name in names]


def _trace_for_visible_run(
    *, repository: MetadataRepository, run_id: str, context: AgentContext
) -> dict[str, object]:
    trace = repository.get_trace(run_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AGENT_RUN_NOT_FOUND")
    run = trace["run"]
    if not isinstance(run, dict) or not isinstance(run.get("userId"), int):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AGENT_METADATA_INVALID"
        )
    if run["userId"] != context.user_id and not context.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AGENT_RUN_NOT_FOUND")
    return trace


def _sse(event_name: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n"
