"""Thread-affine LangGraph production and streaming-response transport."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from queue import Queue
from threading import Thread

from fastapi.responses import StreamingResponse

from app.api.internal_core.run_support import _fail_stream, _finish_stream, _sse
from app.persistence import MetadataRepository
from app.schemas.agent import (
    AgentState,
)
from app.workflow import (
    WorkflowError,
    WorkflowRun,
)

_SSE_STREAM_END = object()
_SSE_PRODUCER_START_TIMEOUT_SECONDS = 5

logger = logging.getLogger(__name__)


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
    client_request_id: str,
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
            client_request_id=client_request_id,
        ):
            frames.put(frame)
    except WorkflowError as exc:
        for frame in _fail_stream(
            repository=repository,
            workflow=workflow,
            fallback_state=fallback_state,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=exc,
            client_request_id=client_request_id,
        ):
            frames.put(frame)
