import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rychlik.acquisition.contracts import AcquisitionError, CompletedDownload, DownloadRequest
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime, RuntimeAlreadyRunningError
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
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


def _build(task_ids, *, max_active_transfers, acquisition, executor_factory=None):
    queue = DownloadQueue(clock=lambda: T0)
    tasks = {}
    requests = {}
    for name, priority in task_ids:
        queue.enqueue(name, priority)
        tasks[name] = create_task(name, now=T0).mark_ready(now=T0)
        requests[name] = DownloadRequest(url=f"http://example.test/{name}", destination_dir=Path("/tmp"))
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=max_active_transfers),
        executor_factory=executor_factory,
    )
    return queue, tasks, requests, runtime


class ManualExecutor:
    """Captures submissions without running them until told to (§49/§50)."""

    def __init__(self):
        self.pending: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        self.pending.append((fn, args, kwargs, future))
        return future

    def run_one(self):
        fn, args, kwargs, future = self.pending.pop(0)
        try:
            result = fn(*args, **kwargs)
            future.set_result(result)
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)

    def run_all(self):
        while self.pending:
            self.run_one()

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class ImmediateAcquisition:
    def __init__(self):
        self.calls: list[str] = []

    def acquire(self, request, *, progress_callback=None, cancel_event=None):
        self.calls.append(request.url)
        return _completed(request.url)


# --- reservation race (§50) -------------------------------------------------


def test_reservation_closes_plan_worker_race():
    manual = ManualExecutor()
    acquisition = ImmediateAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL), ("C", QueuePriority.NORMAL)],
        max_active_transfers=2,
        acquisition=acquisition,
        executor_factory=lambda n: manual,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: len(manual.pending) == 2)

        runtime.notify_state_changed()
        time.sleep(0.05)  # give the controller a chance to (wrongly) resubmit

        assert len(manual.pending) == 2  # still just A and B, not resubmitted, C not added
        assert tasks["A"].state == DownloadTaskState.READY  # worker fn never ran yet
        assert tasks["B"].state == DownloadTaskState.READY
        assert acquisition.calls == []
    finally:
        runtime.stop(timeout=2)


# --- reservation release + refill (§51) --------------------------------------


def test_reservation_release_and_refill_manual():
    manual = ManualExecutor()
    acquisition = ImmediateAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL), ("C", QueuePriority.NORMAL)],
        max_active_transfers=2,
        acquisition=acquisition,
        executor_factory=lambda n: manual,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: len(manual.pending) == 2)
        manual.run_one()  # completes A synchronously, freeing a slot
        # After running A, pending drops to 1 (B) then the controller refills
        # it back to 2 (B + newly-submitted C) once it processes the freed slot.
        assert _wait_until(lambda: len(manual.pending) == 2)
        manual.run_all()
        assert _wait_until(lambda: runtime.wait_for_idle(2))
        assert {tasks[t].state for t in "ABC"} == {DownloadTaskState.COMPLETED}
    finally:
        runtime.stop(timeout=2)


# --- max concurrency + real overlap (§52/§53) ---------------------------------


def test_real_overlap_with_barrier():
    parties = 3
    barrier = threading.Barrier(parties, timeout=5)
    active = {"count": 0, "max": 0}
    lock = threading.Lock()

    class BarrierAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            barrier.wait(timeout=5)
            with lock:
                active["count"] -= 1
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [(f"T{i}", QueuePriority.NORMAL) for i in range(parties)],
        max_active_transfers=parties,
        acquisition=BarrierAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(5))
        assert active["max"] == parties
        assert all(tasks[f"T{i}"].state == DownloadTaskState.COMPLETED for i in range(parties))
    finally:
        runtime.stop(timeout=5)


def test_max_concurrency_never_exceeded():
    active = {"count": 0, "max": 0}
    lock = threading.Lock()

    class SlowAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            time.sleep(0.05)
            with lock:
                active["count"] -= 1
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [(f"T{i}", QueuePriority.NORMAL) for i in range(6)],
        max_active_transfers=2,
        acquisition=SlowAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(5), timeout=5)
        assert active["max"] == 2
        assert all(tasks[f"T{i}"].state == DownloadTaskState.COMPLETED for i in range(6))
    finally:
        runtime.stop(timeout=5)


# --- max=1 serialization (§54) -----------------------------------------------


def test_max_one_serialization():
    order = []
    order_lock = threading.Lock()
    release_a = threading.Event()

    class SerializingAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            with order_lock:
                order.append(("start", name))
            if name == "A":
                release_a.wait(timeout=5)
            with order_lock:
                order.append(("end", name))
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=SerializingAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: ("start", "A") in order)
        time.sleep(0.05)
        with order_lock:
            assert ("start", "B") not in order  # only one slot -> B not even attempted

        release_a.set()
        assert _wait_until(lambda: runtime.wait_for_idle(3))

        with order_lock:
            a_end_index = order.index(("end", "A"))
            b_start_index = order.index(("start", "B"))
            assert a_end_index < b_start_index
    finally:
        runtime.stop(timeout=3)


# --- automatic refill (§55) ---------------------------------------------------


def test_automatic_refill_while_sibling_still_active():
    release_a = threading.Event()
    started = set()
    lock = threading.Lock()

    class RefillAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            with lock:
                started.add(name)
            if name == "A":
                release_a.wait(timeout=5)
            elif name == "B":
                time.sleep(0.3)
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL), ("C", QueuePriority.NORMAL)],
        max_active_transfers=2,
        acquisition=RefillAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: {"A", "B"} <= started)
        with lock:
            assert "C" not in started
        release_a.set()
        assert _wait_until(lambda: "C" in started)  # C starts while B may still be active
        assert _wait_until(lambda: runtime.wait_for_idle(3))
        assert all(tasks[t].state == DownloadTaskState.COMPLETED for t in "ABC")
    finally:
        runtime.stop(timeout=3)


# --- priority / manual ordering under concurrency (§56/§57) -------------------


def test_priority_ordering_under_concurrency():
    order = []
    order_lock = threading.Lock()

    class OrderRecordingAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            with order_lock:
                order.append(name)
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [
            ("H1", QueuePriority.HIGH),
            ("H2", QueuePriority.HIGH),
            ("N1", QueuePriority.NORMAL),
            ("N2", QueuePriority.NORMAL),
            ("L1", QueuePriority.LOW),
        ],
        max_active_transfers=1,
        acquisition=OrderRecordingAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(5))
        assert order == ["H1", "H2", "N1", "N2", "L1"]
    finally:
        runtime.stop(timeout=5)


def test_manual_order_under_concurrency():
    order = []
    order_lock = threading.Lock()

    class OrderRecordingAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            with order_lock:
                order.append(name)
            return _completed(request.url)

    queue = DownloadQueue(clock=lambda: T0)
    n3 = queue.enqueue("N3")
    n1 = queue.enqueue("N1")
    queue.enqueue("N2")
    queue.move_before(n3.queue_entry_id, n1.queue_entry_id)
    tasks = {t: create_task(t, now=T0).mark_ready(now=T0) for t in ["N1", "N2", "N3"]}
    requests = {
        t: DownloadRequest(url=f"http://example.test/{t}", destination_dir=Path("/tmp")) for t in tasks
    }
    coordinator = DispatchCoordinator(acquisition_service=OrderRecordingAcquisition())
    runtime = ConcurrentDownloadRuntime(
        queue=queue, tasks=tasks, requests=requests, coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(5))
        assert order == ["N3", "N1", "N2"]
    finally:
        runtime.stop(timeout=5)


# --- RETRY_WAIT (§58/§59) -----------------------------------------------------


def test_retry_wait_releases_reservation_and_lets_sibling_through():
    class RetryOnceAcquisition:
        def __init__(self):
            self.calls = 0
            self._a_calls = 0

        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            self.calls += 1
            if name == "A":
                self._a_calls += 1
                if self._a_calls == 1:
                    raise AcquisitionError("network blip")  # fails only on the first attempt
            return _completed(request.url)

    def mapper(exc):
        return DownloadTaskFailure(code="NET", message=str(exc), retryable=True)

    acquisition = RetryOnceAcquisition()
    queue = DownloadQueue(clock=lambda: T0)
    entry_a = queue.enqueue("A")
    queue.enqueue("B")
    tasks = {
        "A": create_task("A", now=T0).mark_ready(now=T0),
        "B": create_task("B", now=T0).mark_ready(now=T0),
    }
    requests = {
        t: DownloadRequest(url=f"http://example.test/{t}", destination_dir=Path("/tmp")) for t in tasks
    }
    coordinator = DispatchCoordinator(acquisition_service=acquisition, failure_mapper=mapper)
    runtime = ConcurrentDownloadRuntime(
        queue=queue, tasks=tasks, requests=requests, coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(3))
        assert tasks["A"].state == DownloadTaskState.RETRY_WAIT
        assert tasks["B"].state == DownloadTaskState.COMPLETED
        assert queue.get(entry_a.queue_entry_id).state.name == "QUEUED"

        # retry-ready wake (§59): no timer needed, becomes candidate again
        tasks["A"] = tasks["A"].mark_retry_ready(now=T0)
        runtime.notify_state_changed()
        assert _wait_until(lambda: runtime.wait_for_idle(3))
        assert tasks["A"].state == DownloadTaskState.COMPLETED
        assert tasks["A"].attempt_count == 2
    finally:
        runtime.stop(timeout=3)


# --- stale reserved candidate (§60) -------------------------------------------


def test_stale_reserved_candidate_when_queue_paused_before_worker_starts():
    manual = ManualExecutor()
    acquisition = ImmediateAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        executor_factory=lambda n: manual,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: len(manual.pending) == 1)
        entry = next(iter(queue.active_entries()))
        queue.pause(entry.queue_entry_id)

        manual.run_one()  # now actually invokes the worker

        assert acquisition.calls == []
        assert tasks["A"].state == DownloadTaskState.READY  # not resurrected/started
    finally:
        runtime.stop(timeout=2)


# --- re-enqueue identity (§61 style, reuse A4 semantics under runtime) --------


def test_old_reservation_cannot_dispatch_reenqueued_occurrence():
    manual = ManualExecutor()
    acquisition = ImmediateAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        executor_factory=lambda n: manual,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: len(manual.pending) == 1)
        old_entry = next(iter(queue.active_entries()))
        queue.remove(old_entry.queue_entry_id)
        new_entry = queue.enqueue("A")

        manual.run_one()  # runs the worker bound to the OLD candidate

        assert acquisition.calls == []
        assert queue.get(new_entry.queue_entry_id).state.name == "QUEUED"
        assert tasks["A"].state == DownloadTaskState.READY
    finally:
        runtime.stop(timeout=2)


# --- worker runtime exception continuation (§62) ------------------------------


def test_worker_exception_does_not_kill_controller():
    class BuggyAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            name = request.url.rsplit("/", 1)[-1]
            if name == "A":
                raise RuntimeError("boom")
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=BuggyAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(3))
        assert runtime.fatal_error is None  # controller itself is healthy
        assert tasks["A"].state == DownloadTaskState.FAILED
        assert tasks["B"].state == DownloadTaskState.COMPLETED

        completions = runtime.drain_completions()
        a_completion = next(c for c in completions if c.task_id == "A")
        assert a_completion.error is not None
        # DispatchCoordinator wraps the unexpected RuntimeError in a typed
        # DispatchExecutionError (with the original chained via __cause__,
        # per A4) -- the worker records that typed wrapper's repr, by design.
        assert "DispatchExecutionError" in a_completion.error
    finally:
        runtime.stop(timeout=3)


# --- queue integrity under concurrent cleanup (§64) ---------------------------


def test_queue_integrity_under_concurrent_terminal_cleanup():
    queue, tasks, requests, runtime = _build(
        [(f"N{i}", QueuePriority.NORMAL) for i in range(8)],
        max_active_transfers=4,
        acquisition=ImmediateAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(5))
        assert all(tasks[f"N{i}"].state == DownloadTaskState.COMPLETED for i in range(8))

        active = queue.active_entries()
        assert active == ()  # all removed, none stuck/duplicated

        # sanity: internal position invariants still hold (no corruption) --
        # enqueue one more and confirm ordering/positions are still valid.
        queue.enqueue("N-new")
        entries = queue.eligible_entries()
        assert [e.position for e in entries] == list(range(len(entries)))
    finally:
        runtime.stop(timeout=5)


# --- completion collection thread-safety (§65) --------------------------------


def test_completion_collection_no_loss_or_duplication():
    queue, tasks, requests, runtime = _build(
        [(f"N{i}", QueuePriority.NORMAL) for i in range(10)],
        max_active_transfers=5,
        acquisition=ImmediateAcquisition(),
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(5))
        completions = runtime.drain_completions()
        assert len(completions) == 10
        assert len({c.task_id for c in completions}) == 10
    finally:
        runtime.stop(timeout=5)


# --- start/stop lifecycle (§45/§73/§74) ---------------------------------------


def test_start_twice_raises():
    queue, tasks, requests, runtime = _build(
        [], max_active_transfers=1, acquisition=ImmediateAcquisition()
    )
    runtime.start()
    try:
        with pytest.raises(RuntimeAlreadyRunningError):
            runtime.start()
    finally:
        runtime.stop(timeout=2)


def test_stop_while_stopped_is_safe():
    queue, tasks, requests, runtime = _build(
        [], max_active_transfers=1, acquisition=ImmediateAcquisition()
    )
    runtime.stop(timeout=1)  # never started
    runtime.stop(timeout=1)  # idempotent


def test_stop_while_idle_is_fast():
    queue, tasks, requests, runtime = _build(
        [], max_active_transfers=1, acquisition=ImmediateAcquisition()
    )
    runtime.start()
    start_time = time.monotonic()
    runtime.stop(timeout=5)
    assert time.monotonic() - start_time < 1.0


def test_stop_lets_in_flight_work_finish():
    release = threading.Event()

    class BlockingAcquisition:
        def acquire(self, request, *, progress_callback=None, cancel_event=None):
            release.wait(timeout=5)
            return _completed(request.url)

    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=BlockingAcquisition()
    )
    runtime.start()
    assert _wait_until(lambda: len(runtime._in_flight) == 1)

    def _release_soon():
        time.sleep(0.1)
        release.set()

    threading.Thread(target=_release_soon).start()
    runtime.stop(timeout=5)  # must not return before the in-flight download finishes gracefully

    assert tasks["A"].state == DownloadTaskState.COMPLETED


def test_no_new_dispatch_after_stop_requested():
    manual = ManualExecutor()
    acquisition = ImmediateAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL), ("B", QueuePriority.NORMAL)],
        max_active_transfers=1,
        acquisition=acquisition,
        executor_factory=lambda n: manual,
    )
    runtime.start()
    assert _wait_until(lambda: len(manual.pending) == 1)
    manual.run_one()  # A completes, frees the slot
    runtime.stop(timeout=2)  # shutdown begins; must not submit B even though a slot just freed

    assert manual.pending == []
    assert tasks["B"].state == DownloadTaskState.READY


def test_zero_capacity_runtime_never_submits():
    acquisition = ImmediateAcquisition()
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=0, acquisition=acquisition
    )
    runtime.start()
    try:
        time.sleep(0.1)
        assert acquisition.calls == []
        assert tasks["A"].state == DownloadTaskState.READY
    finally:
        runtime.stop(timeout=2)


# --- no thread leak (§75) ------------------------------------------------------


def test_no_thread_leak_after_stop():
    before = {t.name for t in threading.enumerate()}

    queue, tasks, requests, runtime = _build(
        [(f"N{i}", QueuePriority.NORMAL) for i in range(4)],
        max_active_transfers=2,
        acquisition=ImmediateAcquisition(),
    )
    runtime.start()
    assert _wait_until(lambda: runtime.wait_for_idle(3))
    runtime.stop(timeout=3)

    assert _wait_until(
        lambda: {t.name for t in threading.enumerate()} - before == set(), timeout=2
    )


# --- structural import test ---------------------------------------------------


def test_module_has_no_forbidden_imports():
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
