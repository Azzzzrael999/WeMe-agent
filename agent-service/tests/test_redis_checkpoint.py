"""Regression coverage for the real Redis-backed LangGraph checkpointer."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.checkpoints import CHECKPOINT_TTL_SECONDS, RedisCheckpointSaver, checkpoint_thread_id


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expirations: dict[str, int] = {}

    def get(self, name: str) -> bytes | None:
        return self.values.get(name)

    def set(self, name: str, value: str, ex: int) -> bool:
        self.values[name] = value.encode("utf-8")
        self.expirations[name] = ex
        return True

    def delete(self, *names: str) -> int:
        for name in names:
            self.values.pop(name, None)
            self.expirations.pop(name, None)
        return len(names)

    def ping(self) -> bool:
        return True

    def scan_iter(self, match: str) -> Iterator[bytes]:
        prefix = match.removesuffix("*")
        for name in self.values:
            if name.startswith(prefix):
                yield name.encode("utf-8")


class OverlapDetectingRedis(FakeRedis):
    """Make LangGraph's concurrent checkpoint writes fail without saver locking."""

    def __init__(self) -> None:
        super().__init__()
        self._write_lock = threading.Lock()
        self.overlap_detected = False

    def set(self, name: str, value: str, ex: int) -> bool:
        if not self._write_lock.acquire(blocking=False):
            self.overlap_detected = True
            raise RuntimeError("overlapping Redis checkpoint writes")
        try:
            # LangGraph schedules checkpoint ``put`` and ``put_writes`` work
            # through an executor.  The short pause makes that contract
            # deterministic enough to catch a non-thread-safe saver.
            time.sleep(0.02)
            return super().set(name, value, ex)
        finally:
            self._write_lock.release()


class InterruptState(TypedDict, total=False):
    prompt: str
    answer: str


class CounterState(TypedDict):
    value: int


def _interrupting_graph(
    saver: RedisCheckpointSaver,
) -> StateGraph[InterruptState, None, InterruptState, InterruptState]:
    def wait_for_human(_: InterruptState) -> dict[str, str]:
        response = interrupt({"kind": "confirmation"})
        return {"answer": str(response)}

    graph: StateGraph[InterruptState, None, InterruptState, InterruptState] = StateGraph(
        InterruptState
    )
    graph.add_node("wait_for_human", wait_for_human)
    graph.add_edge(START, "wait_for_human")
    return graph.compile(checkpointer=saver)


def test_interrupt_checkpoint_survives_a_fresh_saver_and_uses_ttl_key() -> None:
    client = FakeRedis()
    graph_thread_id = checkpoint_thread_id("thread_checkpoint", "run_checkpoint")
    config = {"configurable": {"thread_id": graph_thread_id}}

    first_saver = RedisCheckpointSaver(client=client)
    first_graph = _interrupting_graph(first_saver)
    first_events = list(first_graph.stream({"prompt": "确认预约"}, config))

    assert any("__interrupt__" in event for event in first_events)
    key = first_saver.key_for_thread(graph_thread_id)
    assert graph_thread_id in key
    assert client.expirations[key] == CHECKPOINT_TTL_SECONDS

    # A fresh saver has no process memory from the first graph. It must load the
    # same Redis checkpoint and let LangGraph resume the original interrupt.
    fresh_saver = RedisCheckpointSaver(client=client)
    resumed_graph = _interrupting_graph(fresh_saver)
    resumed_events = list(resumed_graph.stream(Command(resume="ACCEPT"), config))
    snapshot = resumed_graph.get_state(config)

    assert resumed_events
    assert snapshot.values["answer"] == "ACCEPT"
    assert fresh_saver.probe() is True


def test_checkpoint_saver_serializes_langgraph_executor_mutations() -> None:
    """Concurrent internal checkpoint tasks must not corrupt one Redis snapshot."""

    client = OverlapDetectingRedis()
    saver = RedisCheckpointSaver(client=client)
    config = {"configurable": {"thread_id": "thread_checkpoint_concurrent"}}

    def increment(state: CounterState) -> dict[str, int]:
        return {"value": state["value"] + 1}

    graph: StateGraph[CounterState, None, CounterState, CounterState] = StateGraph(CounterState)
    graph.add_node("first", increment)
    graph.add_node("second", increment)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    compiled = graph.compile(checkpointer=saver)

    events = list(compiled.stream({"value": 0}, config, durability="sync"))
    snapshot = compiled.get_state(config)

    assert events
    assert snapshot.values["value"] == 2
    assert client.overlap_detected is False
