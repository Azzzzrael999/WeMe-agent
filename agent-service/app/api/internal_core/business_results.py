"""Idempotent business-result callback persistence helpers."""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from app.models.metadata import AgentRun
from app.persistence import MetadataRepository
from app.schemas.agent import (
    AgentState,
    BusinessResultCallback,
)
from app.security import AgentContext

logger = logging.getLogger(__name__)


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
        f"hard:{constraint.type}={constraint.value}" for constraint in request.hard_constraints
    )
    return preserved[:20]
