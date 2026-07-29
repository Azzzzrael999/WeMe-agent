"""Process-local serialisation for one Agent run's graph and callback transitions.

Compose runs one ``agent-service`` process.  The lock closes the short window
between a PENDING confirmation and the durable checkpoint/run-status update:
the asynchronous Java business-result callback must not delete or replan that
checkpoint while the resume graph is still flushing it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _LockEntry:
    lock: Lock = field(default_factory=Lock)
    users: int = 0


class RunExecutionLocks:
    """Bounded registry that serialises work for equal run IDs only."""

    def __init__(self) -> None:
        self._registry_lock = Lock()
        self._entries: dict[str, _LockEntry] = {}

    @contextmanager
    def lock(self, run_id: str) -> Iterator[None]:
        with self._registry_lock:
            entry = self._entries.setdefault(run_id, _LockEntry())
            entry.users += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._registry_lock:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(run_id, None)


run_execution_locks = RunExecutionLocks()
