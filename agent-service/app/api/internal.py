"""Internal endpoints called only by the Java public-API gateway."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from functools import lru_cache, wraps
from queue import Empty, Queue
from typing import Annotated, Any, ParamSpec, TypeVar
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.api.internal_core.business_results import (
    _callback_already_recorded,
    _owned_run,
    _preserved_constraints,
    _record_business_result_step,
    _record_callback_event,
    _require_context_run,
    _safe_callback_response,
)
from app.api.internal_core.run_support import (
    _baseline_resolved_employees,
    _load_checkpoint_or_503,
    _resume_message_key,
    _resume_user_message,
    _sse,
    _trace_for_visible_run,
)
from app.api.internal_core.sse import (
    _SSE_PRODUCER_START_TIMEOUT_SECONDS,
    _emit_workflow_sse_frames,
    _start_sse_producer,
    _streaming_response,
)
from app.checkpoints import RedisCheckpointSaver
from app.config import Settings, get_settings
from app.database.engine import get_engine
from app.database.health import probe_database
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


@lru_cache(maxsize=1)
def _shared_model_provider() -> ModelProvider:
    return build_model_provider(get_settings())


def get_model_provider() -> ModelProvider:
    return _shared_model_provider()


@lru_cache(maxsize=1)
def _shared_policy_retriever() -> PolicyRetriever:
    return build_policy_retriever(get_settings())


def get_policy_retriever() -> PolicyRetriever:
    return _shared_policy_retriever()


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
        if item.assignee_employee_id in allowed_assignee_ids or item.assignee_employee_id is None
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
            title=body.message,
        )
        if body.base_run_id is not None:
            base_run = repository.get_run(body.base_run_id)
            if (
                base_run is None
                or base_run.user_id != context.user_id
                or base_run.thread_id != thread_id
                or base_run.status
                not in {
                    RunStatus.FAILED.value,
                    RunStatus.WAITING_CONFIRMATION.value,
                }
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
            expired_confirmation_baseline = (
                base_run.status == RunStatus.WAITING_CONFIRMATION.value
                and baseline is not None
                and baseline.status is RunStatus.WAITING_CONFIRMATION
                and baseline.draft_expires_at is not None
                and baseline.draft_expires_at <= datetime.now(ZoneInfo(settings.app_timezone))
            )
            if (
                baseline is None
                or baseline.requirement_draft is None
                or (base_run.status != RunStatus.FAILED.value and not expired_confirmation_baseline)
            ):
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
        repository.record_message(
            thread_id=thread_id,
            run_id=context.run_id,
            user_id=context.user_id,
            role="USER",
            content=body.message,
            client_request_id=body.client_request_id,
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
        requirement_draft=(baseline.requirement_draft if baseline is not None else None),
        requirement_items=(list(baseline.requirement_items) if baseline is not None else []),
        requirement_revision=(baseline.requirement_revision if baseline is not None else 0),
        continuation_turn=baseline is not None,
        continued_from_run_id=body.base_run_id if baseline is not None else None,
        optional_requirements_closed=(
            baseline.optional_requirements_closed if baseline is not None else False
        ),
        resolved_employees=(_baseline_resolved_employees(baseline) if baseline is not None else []),
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
                            {"continuedFromRunId": body.base_run_id} if baseline is not None else {}
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
                client_request_id=body.client_request_id,
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
                resume_message_key = _resume_message_key(
                    run_id, state.draft_generation, body.action
                )
                repository.record_message(
                    thread_id=run.thread_id,
                    run_id=run_id,
                    user_id=context.user_id,
                    role="USER",
                    content=_resume_user_message(body),
                    client_request_id=resume_message_key,
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
                client_request_id=resume_message_key,
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
                repository.record_message(
                    thread_id=run.thread_id,
                    run_id=run_id,
                    user_id=context.user_id,
                    role="USER",
                    content=body.message,
                    client_request_id=body.client_request_id,
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
                client_request_id=body.client_request_id,
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


@router.get("/agent-threads")
def list_agent_threads(
    context: Annotated[AgentContext, Depends(get_agent_context)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    latest_status: Annotated[str | None, Query(alias="status", max_length=32)] = None,
) -> JSONResponse:
    allowed_statuses = {item.value for item in RunStatus}
    if latest_status is not None and latest_status not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="INVALID_STATUS",
        )
    try:
        result = repository.list_threads(
            user_id=context.user_id,
            page=page,
            size=size,
            latest_status=latest_status,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_METADATA_UNAVAILABLE",
        ) from exc
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/agent-threads/{thread_id}")
def get_agent_thread(
    thread_id: Annotated[str, Path(min_length=1, max_length=64)],
    context: Annotated[AgentContext, Depends(get_agent_context)],
    repository: Annotated[MetadataRepository, Depends(get_repository)],
) -> JSONResponse:
    try:
        result = repository.get_thread(thread_id=thread_id, user_id=context.user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_METADATA_UNAVAILABLE",
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AGENT_THREAD_NOT_FOUND",
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


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
    if state is not None:
        view["citations"] = [
            citation.model_dump(by_alias=True, mode="json") for citation in state.citations
        ]
        if state.unsat_analysis is not None:
            view["unsatAnalysis"] = state.unsat_analysis.model_dump(by_alias=True, mode="json")
        if state.requirement_draft is not None:
            view.update(
                {
                    "requirementRevision": state.requirement_revision,
                    "requirementItems": [
                        item.model_dump(by_alias=True, mode="json")
                        for item in state.requirement_items
                    ],
                }
            )
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
                "requirementBaselineAvailable": (
                    state.requirement_draft is not None
                    and state.draft_expires_at <= datetime.now(ZoneInfo(settings.app_timezone))
                ),
            }
        )
    elif (
        state is not None
        and state.requirement_draft is not None
        and run.status
        in {
            RunStatus.WAITING_USER_INPUT.value,
            RunStatus.FAILED.value,
        }
    ):
        view["requirementBaselineAvailable"] = run.status == RunStatus.FAILED.value
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
