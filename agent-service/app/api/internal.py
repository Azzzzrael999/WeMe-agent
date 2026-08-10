"""Internal endpoints called only by the Java public-API gateway."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import datetime
from functools import wraps
from queue import Empty, Queue
from threading import Thread
from typing import Annotated, Any, ParamSpec, TypeVar
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.checkpoints import RedisCheckpointSaver
from app.config import Settings, get_settings
from app.database.engine import get_engine
from app.database.health import probe_database
from app.models.metadata import AgentRun
from app.persistence import MetadataRepository, question_summary
from app.providers import ModelProvider, build_model_provider
from app.providers.base import (
    ModelOutputError,
    ModelProviderError,
    StructuredModelRunner,
)
from app.rag import PolicyRetriever, build_policy_retriever
from app.run_locks import run_execution_locks
from app.schemas.agent import (
    AgentInputRequest,
    AgentResumeRequest,
    AgentState,
    AgentStreamRequest,
    BookingResultStatus,
    BusinessResultCallback,
    ConflictRepairFeedbackState,
    Participant,
    PostMeetingDraftRequest,
    PostMeetingDraftResponse,
    ProcessedRequirementInput,
    Route,
    RunStatus,
)
from app.schemas.health import ComponentStatus, HealthResponse, ServiceStatus
from app.security import AgentContext, InternalAuthenticationError, authenticate_agent_context
from app.tools import JavaReadToolClient
from app.workflow import (
    POST_MEETING_PROMPT_VERSION,
    POST_MEETING_SCHEMA_VERSION,
    RequirementAgent,
    WorkflowError,
    WorkflowRun,
    _visible_draft,
    build_workflow_run,
)

router = APIRouter(prefix="/internal/v1", tags=["internal"])
logger = logging.getLogger(__name__)

_SSE_STREAM_END = object()
_SSE_PRODUCER_START_TIMEOUT_SECONDS = 5

Params = ParamSpec("Params")
ReturnT = TypeVar("ReturnT")


def _serialize_run_transition(
    endpoint: Callable[Params, ReturnT],
) -> Callable[Params, ReturnT]:
    """Prevent a business callback from racing the same run's resume stream."""

    @wraps(endpoint)
    def wrapped(*args: Params.args, **kwargs: Params.kwargs) -> ReturnT:
        run_id = kwargs.get("run_id")
        if not isinstance(run_id, str):
            return endpoint(*args, **kwargs)
        with run_execution_locks.lock(run_id):
            return endpoint(*args, **kwargs)

    return wrapped


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


def get_checkpoint_saver(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedisCheckpointSaver:
    return RedisCheckpointSaver.from_url(
        settings.agent_checkpoint_redis_url,
        ttl_seconds=settings.agent_checkpoint_ttl_seconds,
    )


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
    checkpoint_saver: Annotated[RedisCheckpointSaver, Depends(get_checkpoint_saver)],
) -> HealthResponse:
    deepseek = (
        ComponentStatus.CONFIGURED
        if settings.deepseek_is_configured
        else ComponentStatus.NOT_CONFIGURED
    )
    redis_checkpoint = ComponentStatus.UP if checkpoint_saver.probe() else ComponentStatus.DOWN
    if database is ComponentStatus.DOWN or redis_checkpoint is ComponentStatus.DOWN:
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
        redis_checkpoint=redis_checkpoint,
        qdrant=ComponentStatus.NOT_CHECKED,
        business_service=ComponentStatus.NOT_CHECKED,
    )


@router.post("/post-meeting/drafts", response_model=PostMeetingDraftResponse)
def create_post_meeting_draft(
    body: PostMeetingDraftRequest,
    response: Response,
    context: Annotated[AgentContext, Depends(get_agent_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
) -> PostMeetingDraftResponse:
    """Generate an unpersisted, review-only draft from Java-authenticated facts."""

    requirement_agent = RequirementAgent(
        provider=provider,
        runner=StructuredModelRunner(),
    )
    try:
        draft, completions = requirement_agent.analyze_post_meeting(body)
    except ModelOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="POST_MEETING_OUTPUT_INVALID",
        ) from exc
    except ModelProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="POST_MEETING_PROVIDER_UNAVAILABLE",
        ) from exc
    except Exception as exc:
        logger.exception("Post-meeting analysis failed for run %s", context.run_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="POST_MEETING_ANALYSIS_UNAVAILABLE",
        ) from exc

    allowed_assignee_ids = {item.employee_id for item in body.participants}
    normalized_items = [
        item
        if item.assignee_employee_id in allowed_assignee_ids
        or item.assignee_employee_id is None
        else item.model_copy(update={"assignee_employee_id": None})
        for item in draft.action_items
    ]
    normalized_draft = draft.model_copy(update={"action_items": normalized_items})
    response_model = next(
        (completion.model for completion in reversed(completions) if completion.model),
        None,
    )
    model = response_model or (
        settings.deepseek_model if settings.agent_model_provider == "deepseek" else "fixture"
    )
    response.headers["Cache-Control"] = "no-store"
    return PostMeetingDraftResponse(
        agent_run_id=context.run_id,
        model=model,
        prompt_version=POST_MEETING_PROMPT_VERSION,
        schema_version=POST_MEETING_SCHEMA_VERSION,
        draft=normalized_draft,
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
    checkpoint_saver: Annotated[BaseCheckpointSaver[Any], Depends(get_checkpoint_saver)],
) -> StreamingResponse:
    thread_id = body.thread_id or f"thread_{uuid.uuid4().hex}"
    workflow = _workflow(
        settings=settings,
        repository=repository,
        provider=provider,
        retriever=retriever,
        tools=tools,
        context=context,
        checkpoint_saver=checkpoint_saver,
    )
    baseline: AgentState | None = None
    try:
        repository.ensure_thread(
            thread_id=thread_id,
            user_id=context.user_id,
            title="会议智能调度会话",
        )
        if body.base_run_id is not None:
            base_run = repository.get_run(body.base_run_id)
            if (
                base_run is None
                or base_run.user_id != context.user_id
                or base_run.thread_id != thread_id
                or base_run.status != RunStatus.FAILED.value
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="REQUIREMENT_BASELINE_NOT_RECOVERABLE",
                )
            baseline = _load_checkpoint_or_503(
                workflow=workflow,
                thread_id=base_run.thread_id,
                run_id=base_run.run_id,
            )
            if baseline is None or baseline.requirement_draft is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="REQUIREMENT_BASELINE_NOT_RECOVERABLE",
                )
        repository.create_run(
            run_id=context.run_id,
            thread_id=thread_id,
            trace_id=context.trace_id,
            user_id=context.user_id,
            question_summary=question_summary(body.message),
        )
    except HTTPException:
        raise
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
        request_time=datetime.now(ZoneInfo(settings.app_timezone)),
        model_provider=settings.agent_model_provider,
        configured_model=settings.deepseek_model or "fixture",
        intent=baseline.intent if baseline is not None else None,
        requirement_draft=(
            baseline.requirement_draft if baseline is not None else None
        ),
        requirement_items=(
            list(baseline.requirement_items) if baseline is not None else []
        ),
        requirement_revision=(
            baseline.requirement_revision if baseline is not None else 0
        ),
        continuation_turn=baseline is not None,
        continued_from_run_id=body.base_run_id if baseline is not None else None,
        optional_requirements_closed=(
            baseline.optional_requirements_closed if baseline is not None else False
        ),
        resolved_employees=(
            _baseline_resolved_employees(baseline) if baseline is not None else []
        ),
    )

    def produce(frames: Queue[object]) -> None:
        # ``StreamingResponse`` advances a synchronous iterator through the
        # AnyIO worker pool.  A LangGraph iterator owns context/exit state
        # across yields, so execute it on this dedicated producer thread and
        # put only completed SSE frames on the response iterator.
        with run_execution_locks.lock(context.run_id):
            started = time.perf_counter()
            frames.put(
                _sse(
                    "run.started",
                    {
                        "runId": context.run_id,
                        "threadId": thread_id,
                        "traceId": context.trace_id,
                        "status": RunStatus.RUNNING.value,
                        **(
                            {"continuedFromRunId": body.base_run_id}
                            if baseline is not None
                            else {}
                        ),
                    },
                )
            )
            _emit_workflow_sse_frames(
                frames=frames,
                repository=repository,
                workflow=workflow,
                fallback_state=initial_state,
                started_at=started,
                operation=lambda: workflow.stream(initial_state),
            )

    return _streaming_response(_start_sse_producer(produce))


@router.post("/agent-runs/{run_id}/resume")
def resume_agent_run(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    body: AgentResumeRequest,
    context: Annotated[AgentContext, Depends(get_agent_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
    retriever: Annotated[PolicyRetriever, Depends(get_policy_retriever)],
    tools: Annotated[JavaReadToolClient, Depends(get_java_tools)],
    checkpoint_saver: Annotated[BaseCheckpointSaver[Any], Depends(get_checkpoint_saver)],
) -> StreamingResponse:
    startup: Queue[HTTPException | None] = Queue(maxsize=1)

    def produce(frames: Queue[object]) -> None:
        # Acquire the transition lock before loading a checkpoint.  This
        # makes duplicate ACCEPT requests observe the first durable transition
        # instead of both resuming an old WAITING_CONFIRMATION state.
        with run_execution_locks.lock(run_id):
            try:
                run = _owned_run(repository=repository, run_id=run_id, context=context)
                _require_context_run(context=context, run_id=run_id)
                if run.status != RunStatus.WAITING_CONFIRMATION.value:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="RUN_NOT_WAITING_CONFIRMATION",
                    )
                workflow = _workflow(
                    settings=settings,
                    repository=repository,
                    provider=provider,
                    retriever=retriever,
                    tools=tools,
                    context=context,
                    checkpoint_saver=checkpoint_saver,
                )
                state = _load_checkpoint_or_503(
                    workflow=workflow,
                    thread_id=run.thread_id,
                    run_id=run_id,
                )
                if state is None or state.status is not RunStatus.WAITING_CONFIRMATION:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="CHECKPOINT_NOT_WAITING_CONFIRMATION",
                    )
                if body.confirmation_token != state.confirmation_token:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="CONFIRMATION_TOKEN_INVALID",
                    )
            except HTTPException as exc:
                startup.put(exc)
                return
            except Exception:
                logger.exception("Unable to initialise resume producer for run %s", run_id)
                startup.put(
                    HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="AGENT_RESUME_UNAVAILABLE",
                    )
                )
                return

            startup.put(None)
            started = time.perf_counter()
            frames.put(_sse("run.resumed", {"runId": run_id, "status": RunStatus.RUNNING.value}))
            _emit_workflow_sse_frames(
                frames=frames,
                repository=repository,
                workflow=workflow,
                fallback_state=state,
                started_at=started,
                operation=lambda: workflow.resume(state, body),
            )

    stream = _start_sse_producer(produce)
    try:
        startup_result = startup.get(timeout=_SSE_PRODUCER_START_TIMEOUT_SECONDS)
    except Empty as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_RESUME_UNAVAILABLE",
        ) from exc
    if startup_result is not None:
        raise startup_result
    return _streaming_response(stream)


@router.post("/agent-runs/{run_id}/input")
def continue_agent_run_input(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    body: AgentInputRequest,
    context: Annotated[AgentContext, Depends(get_agent_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
    retriever: Annotated[PolicyRetriever, Depends(get_policy_retriever)],
    tools: Annotated[JavaReadToolClient, Depends(get_java_tools)],
    checkpoint_saver: Annotated[BaseCheckpointSaver[Any], Depends(get_checkpoint_saver)],
) -> StreamingResponse:
    startup: Queue[HTTPException | None] = Queue(maxsize=1)

    def produce(frames: Queue[object]) -> None:
        with run_execution_locks.lock(run_id):
            try:
                run = _owned_run(repository=repository, run_id=run_id, context=context)
                _require_context_run(context=context, run_id=run_id)
                if run.status != RunStatus.WAITING_USER_INPUT.value:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="RUN_NOT_WAITING_USER_INPUT",
                    )
                workflow = _workflow(
                    settings=settings,
                    repository=repository,
                    provider=provider,
                    retriever=retriever,
                    tools=tools,
                    context=context,
                    checkpoint_saver=checkpoint_saver,
                )
                state = _load_checkpoint_or_503(
                    workflow=workflow, thread_id=run.thread_id, run_id=run_id
                )
                if state is None or state.status is not RunStatus.WAITING_USER_INPUT:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="CHECKPOINT_NOT_WAITING_USER_INPUT",
                    )
                if body.expected_revision != state.requirement_revision:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="REQUIREMENT_REVISION_CONFLICT",
                    )
                content_hash = hashlib.sha256(body.message.strip().encode("utf-8")).hexdigest()
                processed = next(
                    (
                        item
                        for item in state.processed_requirement_inputs
                        if item.client_request_id == body.client_request_id
                    ),
                    None,
                )
                if processed is not None:
                    detail = (
                        "REQUIREMENT_INPUT_ALREADY_PROCESSED"
                        if processed.content_hash == content_hash
                        else "REQUIREMENT_INPUT_ID_REUSED"
                    )
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
                continuation_state = state.model_copy(
                    update={
                        "message": body.message.strip(),
                        "request_time": datetime.now(ZoneInfo(settings.app_timezone)),
                        "continuation_turn": True,
                        "status": RunStatus.RUNNING,
                        "next_route": Route.REQUIREMENT,
                        "missing_fields": [],
                        "answer_summary": None,
                        "error": None,
                        "availability_snapshot": None,
                        "schedule_candidates": [],
                        "selected_candidate_id": None,
                        "draft": None,
                        "loop_iteration": 0,
                        "executed_tool_fingerprints": [],
                        "processed_requirement_inputs": [
                            *state.processed_requirement_inputs,
                            ProcessedRequirementInput(
                                client_request_id=body.client_request_id,
                                content_hash=content_hash,
                            ),
                        ][-20:],
                    }
                )
            except HTTPException as exc:
                startup.put(exc)
                return
            except Exception:
                logger.exception("Unable to initialise input continuation for run %s", run_id)
                startup.put(
                    HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="AGENT_INPUT_UNAVAILABLE",
                    )
                )
                return

            startup.put(None)
            started = time.perf_counter()
            frames.put(
                _sse(
                    "run.resumed",
                    {
                        "runId": run_id,
                        "status": RunStatus.RUNNING.value,
                        "revision": state.requirement_revision,
                    },
                )
            )
            _emit_workflow_sse_frames(
                frames=frames,
                repository=repository,
                workflow=workflow,
                fallback_state=continuation_state,
                started_at=started,
                operation=lambda: workflow.stream(continuation_state),
            )

    stream = _start_sse_producer(produce)
    try:
        startup_result = startup.get(timeout=_SSE_PRODUCER_START_TIMEOUT_SECONDS)
    except Empty as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_INPUT_UNAVAILABLE",
        ) from exc
    if startup_result is not None:
        raise startup_result
    return _streaming_response(stream)


@router.post("/agent-runs/{run_id}/business-result")
@_serialize_run_transition
def receive_business_result(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    body: BusinessResultCallback,
    context: Annotated[AgentContext, Depends(get_agent_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
    retriever: Annotated[PolicyRetriever, Depends(get_policy_retriever)],
    tools: Annotated[JavaReadToolClient, Depends(get_java_tools)],
    checkpoint_saver: Annotated[BaseCheckpointSaver[Any], Depends(get_checkpoint_saver)],
) -> JSONResponse:
    run = _owned_run(repository=repository, run_id=run_id, context=context)
    _require_context_run(context=context, run_id=run_id)
    if _callback_already_recorded(repository=repository, event_id=body.event_id):
        return _safe_callback_response(
            status_value="IGNORED", reason="DUPLICATE", candidate_count=0
        )
    if run.status != RunStatus.WAITING_BUSINESS_RESULT.value:
        if run.status == RunStatus.WAITING_CONFIRMATION.value:
            # A HOT confirmation may publish its result before the resume
            # graph has flushed the PENDING checkpoint and run status. Do not
            # consume the at-least-once event here: Java must retry it.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AGENT_CALLBACK_RETRY",
            )
        _record_callback_event(repository=repository, run_id=run_id, body=body)
        return _safe_callback_response(
            status_value="IGNORED", reason="RUN_NOT_WAITING", candidate_count=0
        )

    workflow = _workflow(
        settings=settings,
        repository=repository,
        provider=provider,
        retriever=retriever,
        tools=tools,
        context=context,
        checkpoint_saver=checkpoint_saver,
    )
    state = _load_checkpoint_or_503(workflow=workflow, thread_id=run.thread_id, run_id=run_id)
    if state is None or state.status is not RunStatus.WAITING_BUSINESS_RESULT:
        # Persisted run status is already waiting, but the LangGraph saver is
        # still catching up. Retrying is safer than recording an event that
        # would otherwise be lost forever.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_CALLBACK_RETRY",
        )
    if state.pending_request_no != body.request_no:
        _record_callback_event(repository=repository, run_id=run_id, body=body)
        return _safe_callback_response(
            status_value="IGNORED", reason="CHECKPOINT_MISMATCH", candidate_count=0
        )
    if body.status is BookingResultStatus.SUCCESS:
        try:
            repository.complete_run(
                run_id=run_id,
                intent=state.intent,
                status=RunStatus.SUCCEEDED,
                answer_summary="异步预约确认成功。",
                model_call_count=state.model_call_count,
                tool_call_count=state.tool_call_count,
                duration_ms=run.duration_ms or 0,
                error_code=None,
            )
            _record_business_result_step(
                repository=repository,
                state=state,
                summary="异步预约确认成功。",
            )
            _record_callback_event(repository=repository, run_id=run_id, body=body)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AGENT_CALLBACK_UNAVAILABLE",
            ) from exc
        # The terminal Run and event id are durable before cleanup. A cleanup
        # failure cannot make a Java retry lose the already-known result.
        with suppress(WorkflowError):
            workflow.delete_checkpoint(thread_id=run.thread_id, run_id=run_id)
        return _safe_callback_response(
            status_value="PROCESSED", reason="SUCCESS", candidate_count=0
        )

    # CONFLICT must discard all old availability/candidates/draft before the
    # new graph run invokes Java READ tools again.  The callback itself has no
    # user-visible token or model/prompt data in its response.
    callback_step_count = state.step_count + 1
    if state.replan_count >= 2:
        repository.complete_run(
            run_id=run_id,
            intent=state.intent,
            status=RunStatus.WAITING_USER_INPUT,
            answer_summary="连续并发冲突已达到重规划上限，请调整时间或会议室后重试。",
            model_call_count=state.model_call_count,
            tool_call_count=state.tool_call_count,
            duration_ms=run.duration_ms or 0,
            error_code="CONFLICT_REPLAN_EXHAUSTED",
        )
        _record_callback_event(repository=repository, run_id=run_id, body=body)
        return _safe_callback_response(
            status_value="PROCESSED", reason="REPLAN_EXHAUSTED", candidate_count=0
        )
    failed_candidate = state.selected_candidate_id
    if failed_candidate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_CALLBACK_RETRY",
        )
    excluded_ids = list(dict.fromkeys([*state.excluded_candidate_ids, failed_candidate]))
    conflict_type = body.conflict.type if body.conflict is not None else "UNKNOWN"
    preserved = _preserved_constraints(state)
    repair_feedback = ConflictRepairFeedbackState(
        conflict_type=conflict_type,
        failed_candidate_id=failed_candidate,
        preserved_constraints=preserved,
        excluded_candidate_ids=excluded_ids,
        replan_count=state.replan_count + 1,
        room_id=body.conflict.room_id if body.conflict is not None else None,
        slots=body.conflict.slots if body.conflict is not None else [],
        reason="并发最终裁决发生冲突，保留硬约束并排除失败候选后重新读取事实。",
    )
    _record_business_result_step(
        repository=repository,
        state=state,
        summary="收到预约冲突结果，正在基于最新可用性重新调度。",
    )
    replanning_state = state.model_copy(
        update={
            "availability_snapshot": None,
            "schedule_candidates": [],
            "selected_candidate_id": None,
            "unsat_analysis": None,
            "draft": None,
            "confirmation_token": None,
            "draft_expires_at": None,
            "draft_tool_call_id": None,
            "confirm_tool_call_id": None,
            "confirm_idempotency_key": None,
            "pending_request_no": None,
            "business_result": body,
            "resume_action": None,
            "edited_draft": None,
            "answer_summary": None,
            "status": RunStatus.RUNNING,
            "next_route": Route.REQUIREMENT,
            "step_count": callback_step_count,
            "replan_count": state.replan_count + 1,
            "excluded_candidate_ids": excluded_ids,
            "conflict_repair_feedback": repair_feedback,
            "loop_iteration": 0,
            "executed_tool_fingerprints": [],
        }
    )
    try:
        workflow.delete_checkpoint(thread_id=run.thread_id, run_id=run_id)
        list(workflow.stream(replanning_state))
        latest = workflow.latest_state or replanning_state
        if latest.status in {RunStatus.WAITING_CONFIRMATION, RunStatus.WAITING_BUSINESS_RESULT}:
            repository.update_run_progress(
                run_id=run_id,
                intent=latest.intent,
                status=latest.status,
                answer_summary=latest.answer_summary,
                model_call_count=latest.model_call_count,
                tool_call_count=latest.tool_call_count,
                duration_ms=run.duration_ms or 0,
                error_code=None,
            )
        else:
            repository.complete_run(
                run_id=run_id,
                intent=latest.intent,
                status=latest.status,
                answer_summary=latest.answer_summary,
                model_call_count=latest.model_call_count,
                tool_call_count=latest.tool_call_count,
                duration_ms=run.duration_ms or 0,
                error_code=None,
            )
        _record_callback_event(repository=repository, run_id=run_id, body=body)
        return _safe_callback_response(
            status_value="REPLANNED",
            reason=latest.status.value,
            candidate_count=len(latest.schedule_candidates),
        )
    except HTTPException:
        raise
    except Exception as exc:
        # The event id is intentionally not recorded on a failed replan. Put
        # the original waiting state back through the real LangGraph saver so
        # the same at-least-once Java callback can safely retry.
        attempted_state = workflow.latest_state or state
        retry_state = state.model_copy(
            update={
                "step_count": max(
                    state.step_count,
                    attempted_state.step_count,
                    repository.highest_step_sequence(run_id),
                ),
                "model_call_count": max(state.model_call_count, attempted_state.model_call_count),
                "tool_call_count": max(state.tool_call_count, attempted_state.tool_call_count),
            }
        )
        with suppress(WorkflowError):
            workflow.restore_checkpoint_state(retry_state)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_CALLBACK_UNAVAILABLE",
        ) from exc


@router.get("/agent-runs/{run_id}")
def get_agent_run(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
    retriever: Annotated[PolicyRetriever, Depends(get_policy_retriever)],
    tools: Annotated[JavaReadToolClient, Depends(get_java_tools)],
    checkpoint_saver: Annotated[BaseCheckpointSaver[Any], Depends(get_checkpoint_saver)],
) -> JSONResponse:
    run = _owned_run(repository=repository, run_id=run_id, context=context)
    _require_context_run(context=context, run_id=run_id)
    trace = _trace_for_visible_run(repository=repository, run_id=run_id, context=context)
    run_view = trace["run"]
    if not isinstance(run_view, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AGENT_METADATA_INVALID"
        )
    view = dict(run_view)
    workflow = _workflow(
        settings=settings,
        repository=repository,
        provider=provider,
        retriever=retriever,
        tools=tools,
        context=context,
        checkpoint_saver=checkpoint_saver,
    )
    state = _load_checkpoint_or_503(workflow=workflow, thread_id=run.thread_id, run_id=run_id)
    if state is not None and state.unsat_analysis is not None:
        view["unsatAnalysis"] = state.unsat_analysis.model_dump(by_alias=True, mode="json")
    if (
        state is not None
        and state.status is RunStatus.WAITING_CONFIRMATION
        and state.draft is not None
        and state.confirmation_token is not None
        and state.draft_expires_at
    ):
        view.update(
            {
                "candidates": [
                    candidate.model_dump(by_alias=True, mode="json")
                    for candidate in state.schedule_candidates
                ],
                "actionType": state.operation_type.value if state.operation_type else "CREATE",
                "draft": _visible_draft(state),
                "confirmationToken": state.confirmation_token,
                "expiresAt": state.draft_expires_at.isoformat(),
            }
        )
    elif (
        state is not None
        and state.requirement_draft is not None
        and run.status in {RunStatus.WAITING_USER_INPUT.value, RunStatus.FAILED.value}
    ):
        view.update(
            {
                "requirementRevision": state.requirement_revision,
                "requirementItems": [
                    item.model_dump(by_alias=True, mode="json")
                    for item in state.requirement_items
                ],
                "requirementBaselineAvailable": run.status == RunStatus.FAILED.value,
            }
        )
    return JSONResponse(view, headers={"Cache-Control": "no-store"})


@router.get("/agent-runs/{run_id}/trace")
def get_agent_trace(
    run_id: Annotated[str, Path(min_length=1, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
) -> JSONResponse:
    _require_context_run(context=context, run_id=run_id)
    return JSONResponse(
        _trace_for_visible_run(repository=repository, run_id=run_id, context=context)
    )


def _workflow(
    *,
    settings: Settings,
    repository: MetadataRepository,
    provider: ModelProvider,
    retriever: PolicyRetriever,
    tools: JavaReadToolClient,
    context: AgentContext,
    checkpoint_saver: BaseCheckpointSaver[Any],
) -> WorkflowRun:
    return build_workflow_run(
        settings=settings,
        repository=repository,
        provider=provider,
        retriever=retriever,
        tools=tools,
        context=context,
        checkpoint_saver=checkpoint_saver,
    )


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
def _baseline_resolved_employees(state: AgentState) -> list[Participant]:
    draft = state.requirement_draft
    if draft is None:
        return []
    names = set(draft.required_participant_names)
    return [item for item in state.resolved_employees if item.name in names]


def _owned_run(*, repository: MetadataRepository, run_id: str, context: AgentContext) -> AgentRun:
    run = repository.get_run(run_id)
    if run is None or (run.user_id != context.user_id and not context.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AGENT_RUN_NOT_FOUND")
    return run


def _require_context_run(*, context: AgentContext, run_id: str) -> None:
    if context.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="AGENT_CONTEXT_INVALID"
        )


def _safe_callback_response(
    *, status_value: str, reason: str, candidate_count: int
) -> JSONResponse:
    return JSONResponse(
        {
            "status": status_value,
            "reason": reason,
            "candidateCount": candidate_count,
        }
    )


def _callback_already_recorded(*, repository: MetadataRepository, event_id: str) -> bool:
    try:
        return repository.has_business_event(event_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_METADATA_UNAVAILABLE",
        ) from exc


def _record_callback_event(
    *, repository: MetadataRepository, run_id: str, body: BusinessResultCallback
) -> None:
    try:
        inserted = repository.record_business_event_once(
            event_id=body.event_id,
            run_id=run_id,
            request_no=body.request_no,
            event_type=body.status.value,
            payload=body.model_dump(by_alias=True, mode="json"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_METADATA_UNAVAILABLE",
        ) from exc
    if not inserted:
        # A concurrent delivery has already completed the same transition.
        # The caller can safely treat the resulting 2xx response as a no-op.
        return


def _record_business_result_step(
    *, repository: MetadataRepository, state: AgentState, summary: str
) -> None:
    repository.record_step(
        step_id=f"step_{uuid.uuid4().hex}",
        run_id=state.run_id,
        sequence_no=state.step_count + 1,
        agent_name="deterministic",
        node_name="business_result",
        status="SUCCEEDED",
        input_summary="Process a validated asynchronous booking result.",
        output_summary=summary,
        duration_ms=0,
    )


def _preserved_constraints(state: AgentState) -> list[str]:
    """Summarize immutable scheduling constraints without sensitive content."""

    request = state.meeting_request
    if request is None:
        return ["ORIGINAL_MEETING_REQUEST"]
    preserved = [
        f"durationMinutes={request.duration_minutes}",
        f"requiredParticipantCount={len(request.required_participants)}",
        f"minimumCapacity={request.minimum_capacity or 1}",
    ]
    if request.time_window is not None:
        preserved.append(
            "timeWindow="
            f"{request.time_window.start.isoformat()}/{request.time_window.end.isoformat()}"
        )
    if request.required_features:
        preserved.append("requiredFeatures=" + ",".join(sorted(request.required_features)))
    preserved.extend(
        f"hard:{constraint.type}={constraint.value}"
        for constraint in request.hard_constraints
    )
    return preserved[:20]


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


def _streaming_response(generate: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        generate,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _start_sse_producer(produce: Callable[[Queue[object]], None]) -> Iterator[str]:
    """Run a LangGraph-producing operation on one stable thread.

    Starlette/AnyIO may advance a synchronous ``StreamingResponse`` iterator
    on different worker threads between yielded frames.  The response iterator
    below only dequeues immutable strings; the graph iterator itself remains
    entirely inside this producer thread, preserving both context affinity and
    progressive SSE delivery.
    """

    frames: Queue[object] = Queue()

    def worker() -> None:
        try:
            produce(frames)
        except Exception:
            # Endpoint-specific code emits a safe terminal event for expected
            # workflow failures.  This guard only ensures a programming or
            # infrastructure failure cannot leave the SSE response hanging.
            logger.exception("Agent SSE producer crashed")
        finally:
            frames.put(_SSE_STREAM_END)

    Thread(target=worker, name="agent-sse-producer", daemon=True).start()
    return _queued_sse_frames(frames)


def _queued_sse_frames(frames: Queue[object]) -> Iterator[str]:
    while True:
        frame = frames.get()
        if frame is _SSE_STREAM_END:
            return
        if not isinstance(frame, str):
            logger.error("Ignoring invalid Agent SSE producer frame")
            continue
        yield frame


def _emit_workflow_sse_frames(
    *,
    frames: Queue[object],
    repository: MetadataRepository,
    workflow: WorkflowRun,
    fallback_state: AgentState,
    started_at: float,
    operation: Callable[[], Iterator[tuple[str, dict[str, object]]]],
) -> None:
    """Execute and frame a graph without letting its iterator leave its thread."""

    try:
        for event_name, payload in operation():
            frames.put(_sse(event_name, payload))
        for frame in _finish_stream(
            repository=repository,
            workflow=workflow,
            fallback_state=fallback_state,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        ):
            frames.put(frame)
    except WorkflowError as exc:
        for frame in _fail_stream(
            repository=repository,
            workflow=workflow,
            fallback_state=fallback_state,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=exc,
        ):
            frames.put(frame)


def _sse(event_name: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n"
