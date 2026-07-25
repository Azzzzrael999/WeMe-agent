"""Internal endpoints called only by the Java public-API gateway."""

import json
import time
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import Settings, get_settings
from app.database.engine import get_engine
from app.database.health import probe_database
from app.persistence import MetadataRepository, question_summary
from app.providers import ModelProvider, build_model_provider
from app.rag import PolicyRetriever, build_policy_retriever
from app.schemas.agent import AgentState, AgentStreamRequest, RunStatus
from app.schemas.health import ComponentStatus, HealthResponse, ServiceStatus
from app.security import AgentContext, InternalAuthenticationError, authenticate_agent_context
from app.tools import JavaReadToolClient
from app.workflow import WorkflowError, build_workflow_run

router = APIRouter(prefix="/internal/v1", tags=["internal"])


def get_repository() -> MetadataRepository:
    return MetadataRepository(get_engine())


def get_model_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelProvider:
    return build_model_provider(settings)


def get_policy_retriever(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PolicyRetriever:
    return build_policy_retriever(settings)


def get_java_tools(
    settings: Annotated[Settings, Depends(get_settings)],
) -> JavaReadToolClient:
    return JavaReadToolClient(settings)


def get_agent_context(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    service_token: Annotated[str | None, Header(alias="X-Service-Token")] = None,
    header_trace_id: Annotated[str | None, Header(alias="X-Trace-Id")] = None,
    header_run_id: Annotated[str | None, Header(alias="X-Run-Id")] = None,
) -> AgentContext:
    try:
        return authenticate_agent_context(
            settings=settings,
            authorization=authorization,
            service_token=service_token,
            trace_id=header_trace_id,
            run_id=header_run_id,
        )
    except InternalAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AGENT_CONTEXT_INVALID",
        ) from exc


@router.get("/health", response_model=HealthResponse)
def health(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[ComponentStatus, Depends(probe_database)],
) -> HealthResponse:
    deepseek = (
        ComponentStatus.CONFIGURED
        if settings.deepseek_is_configured
        else ComponentStatus.NOT_CONFIGURED
    )

    if database is ComponentStatus.DOWN:
        service_status = ServiceStatus.DOWN
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif deepseek is ComponentStatus.NOT_CONFIGURED:
        service_status = ServiceStatus.DEGRADED
    else:
        service_status = ServiceStatus.UP

    return HealthResponse(
        status=service_status,
        deepseek=deepseek,
        database=database,
        redis_checkpoint=ComponentStatus.NOT_CHECKED,
        qdrant=ComponentStatus.NOT_CHECKED,
        business_service=ComponentStatus.NOT_CHECKED,
    )


@router.post("/agent-runs/stream")
def stream_agent_run(
    body: AgentStreamRequest,
    context: Annotated[AgentContext, Depends(get_agent_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
    retriever: Annotated[PolicyRetriever, Depends(get_policy_retriever)],
    tools: Annotated[JavaReadToolClient, Depends(get_java_tools)],
) -> StreamingResponse:
    thread_id = body.thread_id or f"thread_{uuid.uuid4().hex}"
    try:
        repository.ensure_thread(
            thread_id=thread_id,
            user_id=context.user_id,
            title="会议智能调度会话",
        )
        repository.create_run(
            run_id=context.run_id,
            thread_id=thread_id,
            trace_id=context.trace_id,
            user_id=context.user_id,
            question_summary=question_summary(body.message),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="THREAD_FORBIDDEN"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_METADATA_UNAVAILABLE",
        ) from exc

    initial_state = AgentState(
        thread_id=thread_id,
        run_id=context.run_id,
        trace_id=context.trace_id,
        user_id=context.user_id,
        roles=list(context.roles),
        message=body.message,
    )
    workflow = build_workflow_run(
        settings=settings,
        repository=repository,
        provider=provider,
        retriever=retriever,
        tools=tools,
        context=context,
    )

    def generate() -> Iterator[str]:
        started = time.perf_counter()
        yield _sse(
            "run.started",
            {
                "runId": context.run_id,
                "threadId": thread_id,
                "traceId": context.trace_id,
                "status": RunStatus.RUNNING.value,
            },
        )
        try:
            yield from (_sse(event, payload) for event, payload in workflow.stream(initial_state))
            final_state = workflow.latest_state or initial_state
            duration_ms = int((time.perf_counter() - started) * 1000)
            repository.complete_run(
                run_id=final_state.run_id,
                intent=final_state.intent,
                status=final_state.status,
                answer_summary=final_state.answer_summary,
                model_call_count=final_state.model_call_count,
                tool_call_count=final_state.tool_call_count,
                duration_ms=duration_ms,
                error_code=None,
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
        except WorkflowError as exc:
            failed_state = workflow.latest_state or initial_state
            duration_ms = int((time.perf_counter() - started) * 1000)
            repository.complete_run(
                run_id=failed_state.run_id,
                intent=failed_state.intent,
                status=RunStatus.FAILED,
                answer_summary=None,
                model_call_count=failed_state.model_call_count,
                tool_call_count=failed_state.tool_call_count,
                duration_ms=duration_ms,
                error_code=exc.code,
            )
            yield _sse(
                "run.failed",
                {
                    "runId": failed_state.run_id,
                    "status": RunStatus.FAILED.value,
                    "errorCode": exc.code,
                    "message": exc.message,
                },
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/agent-runs/{run_id}")
def get_agent_run(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
) -> JSONResponse:
    trace = _trace_for_visible_run(repository=repository, run_id=run_id, context=context)
    return JSONResponse(trace["run"])


@router.get("/agent-runs/{run_id}/trace")
def get_agent_trace(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
) -> JSONResponse:
    return JSONResponse(
        _trace_for_visible_run(repository=repository, run_id=run_id, context=context)
    )


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
