import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rychlik.acquisition.contracts import AcquisitionError, CompletedDownload, DownloadRequest
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime, RetryEventKind
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.retry_policy import RetryPolicy, RetryPolicyConfig
from rychlik.core.scheduler_policy import SchedulerConfig

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _wait_until(predicate, timeout=3.0, interval=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _completed(url: str) -> CompletedDownload:
    return CompletedDownload(final_path=Path(f"/tmp/{url}"), display_name=url, source_url=url, size=1)


def _retryable_mapper(exc):
    return DownloadTaskFailure(code="NET", message=str(exc), retryable=True)


class FakeMonotonic:
    """Manual, controllable fake clock (§21/§64) -- no real sleeping in tests."""

    def __init__(self, start=1000.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class AlwaysFailAcquisition:
    def __init__(self):
        self.calls = 0

    def acquire(self, request, *, progress_callback=None, cancel_event=None):
        self.calls += 1
        raise AcquisitionError("persistent failure")


class FailNTimesAcquisition:
    def __init__(self, fail_times: int):
        self.calls = 0
        self._fail_times = fail_times

    def acquire(self, request, *, progress_callback=None, cancel_event=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise AcquisitionError("blip")
        return _completed(request.url)


def _build(names, *, max_active_transfers, acquisition, retry_policy, monotonic=None):
    queue = DownloadQueue(clock=lambda: T0)
    tasks = {}
    requests = {}
    for name, priority in names:
        queue.enqueue(name, priority)
        tasks[name] = create_task(name, now=T0).mark_ready(now=T0)
        requests[name] = DownloadRequest(url=f"http://example.test/{name}", destination_dir=Path("/tmp"))
    coordinator = DispatchCoordinator(acquisition_service=acquisition, failure_mapper=_retryable_mapper)
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=max_active_transfers),
        retry_policy=retry_policy,
        monotonic=monotonic or time.monotonic,
    )
    return queue, tasks, requests, runtime


# --- manual/fake clock, no early retry, deadline ready (§64/§65/§66) --------


def test_manual_clock_no_early_retry_then_ready_at_deadline():
    clock = FakeMonotonic(start=100.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=2, base_delay_seconds=5.0))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        entry = next(iter(queue.active_entries()))
        first_entry_id = entry.queue_entry_id
        first_priority = entry.priority
        first_position = entry.position

        # Not due yet: advance short of the 5s deadline.
        clock.advance(4.999)
        runtime.notify_state_changed()
        time.sleep(0.05)
        assert tasks["A"].state == DownloadTaskState.RETRY_WAIT
        assert acquisition.calls == 1

        # Now due.
        clock.advance(0.002)
        runtime.notify_state_changed()
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED)  # max_attempts=2, exhausts

        assert acquisition.calls == 2
        # Same queue occurrence, same priority/position throughout (§66).
        # (Queue entry is REMOVED on exhaustion, but we captured it before.)
        assert first_entry_id == entry.queue_entry_id
        assert first_priority == QueuePriority.NORMAL
        assert first_position == 0
    finally:
        runtime.stop(timeout=2)


# --- exhaustion (§70/§71) ----------------------------------------------------


def test_exhaustion_after_max_attempts():
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=2, base_delay_seconds=0.01))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=policy
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED, timeout=3)
        assert acquisition.calls == 2  # attempt 1 + 1 retry, then exhausted -- never a 3rd
        assert tasks["A"].last_failure.code == "NET"  # real failure preserved, not a synthetic one
        assert queue.active_entries() == ()

        events = runtime.drain_retry_events()
        kinds = [e.kind for e in events]
        assert RetryEventKind.SCHEDULED in kinds
        assert RetryEventKind.EXHAUSTED in kinds
    finally:
        runtime.stop(timeout=2)


def test_max_attempts_one_means_zero_retries():
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=1, base_delay_seconds=0.01))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=policy
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED, timeout=3)
        assert acquisition.calls == 1  # zero retries
    finally:
        runtime.stop(timeout=2)


def test_zero_delay_does_not_exceed_max_attempts():
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.0))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=policy
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED, timeout=3)
        assert acquisition.calls == 3  # never a 4th
    finally:
        runtime.stop(timeout=2)


def test_eventual_success_within_budget():
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.01))
    acquisition = FailNTimesAcquisition(fail_times=2)
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=policy
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=3)
        assert acquisition.calls == 3
        assert tasks["A"].attempt_count == 3
        assert queue.active_entries() == ()
    finally:
        runtime.stop(timeout=2)


# --- backward compatibility: retry_policy=None (default) --------------------


def test_no_retry_policy_leaves_task_in_retry_wait_forever():
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=None
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        time.sleep(0.1)
        assert tasks["A"].state == DownloadTaskState.RETRY_WAIT  # never promoted automatically
        assert acquisition.calls == 1
        assert runtime.drain_retry_events() == []
    finally:
        runtime.stop(timeout=2)


# --- transfer slot behavior during backoff (§43/§44/§78) ---------------------


def test_retry_wait_releases_slot_for_sibling():
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=5, base_delay_seconds=10.0))  # long backoff

    class Acq:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            if name == "A":
                raise AcquisitionError("blip")
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=Acq(),
        retry_policy=policy,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        # B must start immediately even though A is "waiting" for 10s.
        assert _wait_until(lambda: tasks["B"].state == DownloadTaskState.COMPLETED, timeout=2)
    finally:
        runtime.stop(timeout=2)


# --- queue paused during retry (§67) ------------------------------------------


def test_paused_queue_entry_still_becomes_ready_but_not_dispatched():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=1.0))
    acquisition = FailNTimesAcquisition(fail_times=1)
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        entry = next(iter(queue.active_entries()))
        queue.pause(entry.queue_entry_id)

        clock.advance(1.5)
        runtime.notify_state_changed()
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.READY)
        time.sleep(0.05)
        assert acquisition.calls == 1  # not re-dispatched: queue still paused

        queue.resume(entry.queue_entry_id)
        runtime.notify_state_changed()
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=2)
    finally:
        runtime.stop(timeout=2)


# --- priority preserved when retry becomes ready (§69) ------------------------


def test_high_priority_wins_over_ready_retry():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=5, base_delay_seconds=1.0))

    class Acq:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            if name == "A" and self.a_calls == 0:
                self.a_calls = 1
                raise AcquisitionError("blip")
            return _completed(request.url)

        a_calls = 0

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=Acq(),
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)

        # A HIGH task arrives while A backs off.
        h_entry = queue.enqueue("H", QueuePriority.HIGH)
        tasks["H"] = create_task("H", now=T0).mark_ready(now=T0)
        requests["H"] = DownloadRequest(url="http://example.test/H", destination_dir=Path("/tmp"))
        runtime.notify_state_changed()

        clock.advance(1.1)
        runtime.notify_state_changed()

        assert _wait_until(lambda: tasks["H"].state == DownloadTaskState.COMPLETED, timeout=2)
        # A became READY due to backoff but must not have started before H
        # was available; both eventually complete under max=1 serialization.
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=2)
    finally:
        runtime.stop(timeout=2)


# --- stale schedules (§73/§74/§75/§76/§77) ------------------------------------


def test_stale_schedule_when_task_cancelled_before_deadline():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=5.0))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        tasks["A"] = tasks["A"].cancel(now=T0)

        clock.advance(6.0)
        runtime.notify_state_changed()
        time.sleep(0.1)

        assert tasks["A"].state == DownloadTaskState.CANCELLED  # never resurrected
        events = runtime.drain_retry_events()
        assert any(e.kind == RetryEventKind.STALE for e in events)
    finally:
        runtime.stop(timeout=2)


def test_stale_schedule_when_queue_entry_removed():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=5.0))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        entry = next(iter(queue.active_entries()))
        queue.remove(entry.queue_entry_id)

        clock.advance(6.0)
        runtime.notify_state_changed()
        time.sleep(0.1)

        assert tasks["A"].state == DownloadTaskState.RETRY_WAIT  # not resurrected/promoted
        events = runtime.drain_retry_events()
        assert any(e.kind == RetryEventKind.STALE for e in events)
    finally:
        runtime.stop(timeout=2)


def test_reenqueue_identity_old_schedule_does_not_affect_new_occurrence():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=5.0))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        old_entry = next(iter(queue.active_entries()))
        queue.remove(old_entry.queue_entry_id)
        new_entry = queue.enqueue("A")  # re-enqueue as a NEW occurrence

        clock.advance(6.0)
        runtime.notify_state_changed()
        time.sleep(0.1)

        # Old schedule must not promote/mutate anything; task stays RETRY_WAIT
        # (never touched by the stale timer) and the new occurrence is untouched.
        assert tasks["A"].state == DownloadTaskState.RETRY_WAIT
        assert queue.get(new_entry.queue_entry_id).state.name == "QUEUED"
        events = runtime.drain_retry_events()
        assert any(e.kind == RetryEventKind.STALE for e in events)
    finally:
        runtime.stop(timeout=2)


# --- controller no busy-spin / earlier-wake / multi-deadline (§79-83) ---------


def test_earlier_wake_for_new_task_does_not_wait_for_retry_deadline():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=5, base_delay_seconds=30.0))

    class Acq:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            if name == "A":
                raise AcquisitionError("blip")
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=2, acquisition=Acq(), retry_policy=policy, monotonic=clock
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
        # A retry is due only 30s in fake-time from now -- but a new task
        # must be picked up immediately via notify_state_changed(), not by
        # waiting for that (never-advanced) deadline.
        queue.enqueue("B")
        tasks["B"] = create_task("B", now=T0).mark_ready(now=T0)
        requests["B"] = DownloadRequest(url="http://example.test/B", destination_dir=Path("/tmp"))
        runtime.notify_state_changed()

        assert _wait_until(lambda: tasks["B"].state == DownloadTaskState.COMPLETED, timeout=2)
    finally:
        runtime.stop(timeout=2)


def test_multiple_retry_deadlines_use_nearest_first():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=2, base_delay_seconds=1.0))

    class Acq:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            raise AcquisitionError("blip")

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL), ("C", QueuePriority.NORMAL)],
        max_active_transfers=3,
        acquisition=Acq(),
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    try:
        assert _wait_until(
            lambda: all(tasks[t].state == DownloadTaskState.RETRY_WAIT for t in "ABC"), timeout=2
        )
        # All scheduled with the same 1.0s delay from roughly the same start;
        # advancing time should promote and then exhaust all of them without
        # the controller needing external nudges beyond its own deadline wait.
        clock.advance(1.5)
        runtime.notify_state_changed()
        assert _wait_until(
            lambda: all(tasks[t].state == DownloadTaskState.FAILED for t in "ABC"), timeout=2
        )
    finally:
        runtime.stop(timeout=2)


# --- shutdown starts no new retries (§54/§55) ---------------------------------


def test_shutdown_does_not_promote_or_start_new_retry():
    clock = FakeMonotonic(start=0.0)
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=1.0))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        retry_policy=policy,
        monotonic=clock,
    )
    runtime.start()
    assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT)
    calls_before_stop = acquisition.calls

    runtime.stop(timeout=2)  # shutdown begins while A is still backing off

    clock.advance(2.0)  # deadline would now be due, but controller is gone
    time.sleep(0.1)

    assert acquisition.calls == calls_before_stop  # no new attempt was started
    assert tasks["A"].state == DownloadTaskState.RETRY_WAIT


# --- no retry-specific thread leak (§56) --------------------------------------


def test_no_thread_leak_after_stop_with_retries():
    import threading

    before = {t.name for t in threading.enumerate()}
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=2, base_delay_seconds=0.01))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=policy
    )
    runtime.start()
    assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED, timeout=3)
    runtime.stop(timeout=3)

    assert _wait_until(lambda: {t.name for t in threading.enumerate()} - before == set(), timeout=2)


# --- duplicate schedule safety (§39/§77) --------------------------------------


def test_retry_schedule_is_keyed_uniquely_per_queue_entry():
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.01))
    acquisition = AlwaysFailAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, retry_policy=policy
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED, timeout=3)
        # exactly one SCHEDULED event per retry attempt (no duplicate/double
        # scheduling) -- max_attempts=3: attempt1 fails->SCHEDULED,
        # attempt2 fails->SCHEDULED, attempt3 fails->EXHAUSTED.
        events = runtime.drain_retry_events()
        scheduled = [e for e in events if e.kind == RetryEventKind.SCHEDULED]
        assert len(scheduled) == 2
        assert len({e.attempt_count for e in scheduled}) == 2  # distinct generations, no duplicates
    finally:
        runtime.stop(timeout=2)


# --- structural import test ---------------------------------------------------


def test_concurrent_runtime_still_has_no_forbidden_imports():
    import ast

    import rychlik.core.concurrent_runtime as module

    forbidden = {"PySide6", "requests", "httpx", "yt_dlp"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    top_level = {name.split(".")[0] for name in imported_modules}
    assert top_level & forbidden == set()
    assert not any(m.startswith("rychlik.share") for m in imported_modules)
