from __future__ import annotations

from threading import Event, Thread

from app.run_locks import RunExecutionLocks


def test_same_run_transitions_are_serialized_without_blocking_other_work() -> None:
    locks = RunExecutionLocks()
    entered_same_run = Event()
    entered_other_run = Event()

    def enter_same_run() -> None:
        with locks.lock("run_same"):
            entered_same_run.set()

    def enter_other_run() -> None:
        with locks.lock("run_other"):
            entered_other_run.set()

    with locks.lock("run_same"):
        same_thread = Thread(target=enter_same_run)
        other_thread = Thread(target=enter_other_run)
        same_thread.start()
        other_thread.start()
        assert entered_other_run.wait(timeout=1)
        assert not entered_same_run.wait(timeout=0.05)

    assert entered_same_run.wait(timeout=1)
    same_thread.join(timeout=1)
    other_thread.join(timeout=1)
    assert not same_thread.is_alive()
    assert not other_thread.is_alive()
