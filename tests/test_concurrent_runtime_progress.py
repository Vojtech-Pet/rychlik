import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.contracts import CompletedDownload, DownloadRequest
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import DownloadTaskState, create_task
from rychlik.core.progress import ProgressRegistry
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


def _build(names, *, max_active_transfers, acquisition, progress_registry):
    queue = DownloadQueue(clock=lambda: T0)
    tasks = {}
    requests = {}
    for name, priority in names:
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
        progress_registry=progress_registry,
    )
    return queue, tasks, requests, runtime


class SteppedAcquisition:
    """Reports progress in steps, blocking briefly between them, so a test
    can observe intermediate manager_snapshot() states."""

    def __init__(self, *, steps=5, delay=0.03, total=1000):
        self.steps = steps
        self.delay = delay
        self.total = total

    def acquire(self, request, *, progress_callback=None, cancel_event=None):
        for i in range(1, self.steps + 1):
            time.sleep(self.delay)
            if progress_callback is not None:
                progress_callback(i * (self.total // self.steps), self.total)
        return _completed(request.url)


# --- backward compatible: no registry -----------------------------------------


def test_manager_snapshot_without_progress_registry():
    acquisition = SteppedAcquisition(steps=1, delay=0.0)
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, progress_registry=None
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=3)
        snap = runtime.manager_snapshot()  # queue empty now (removed on completion), just must not error
        assert snap.items == ()
    finally:
        runtime.stop(timeout=2)


# --- manager_snapshot reflects live progress -----------------------------------


def test_manager_snapshot_reflects_progress_during_transfer():
    registry = ProgressRegistry()
    acquisition = SteppedAcquisition(steps=6, delay=0.05, total=600)
    queue, tasks, requests, runtime = _build(
        [("A", QueuePriority.NORMAL)], max_active_transfers=1, acquisition=acquisition, progress_registry=registry
    )
    runtime.start()
    try:
        observed_nonzero = _wait_until(
            lambda: any(
                item.task_id == "A" and item.bytes_downloaded > 0
                for item in runtime.manager_snapshot().items
            ),
            timeout=3,
        )
        assert observed_nonzero
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=3)
    finally:
        runtime.stop(timeout=2)


# --- concurrent snapshotting stress / no deadlock (§93) ------------------------


def test_repeated_snapshotting_under_concurrent_workers_no_deadlock():
    registry = ProgressRegistry()
    acquisition = SteppedAcquisition(steps=10, delay=0.005, total=1000)
    queue, tasks, requests, runtime = _build(
        [(f"T{i}", QueuePriority.NORMAL) for i in range(6)],
        max_active_transfers=3,
        acquisition=acquisition,
        progress_registry=registry,
    )
    runtime.start()
    try:
        stop_polling = threading.Event()
        errors = []

        def _poll():
            while not stop_polling.is_set():
                try:
                    runtime.manager_snapshot()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                time.sleep(0.002)

        poller = threading.Thread(target=_poll)
        poller.start()

        finished = _wait_until(
            lambda: all(tasks[f"T{i}"].state == DownloadTaskState.COMPLETED for i in range(6)), timeout=5
        )
        stop_polling.set()
        poller.join(timeout=3)

        assert finished
        assert errors == []
        assert not poller.is_alive()  # proves no hang/deadlock
    finally:
        runtime.stop(timeout=3)


# --- structural: state lock and progress lock are never nested (§90-92) --------


def test_manager_snapshot_does_not_hold_state_lock_during_progress_query():
    """While manager_snapshot() is composing (which queries the progress
    registry AFTER releasing the state lock), another thread must be able
    to acquire the state lock immediately -- proving the two locks are
    never held simultaneously."""
    registry = ProgressRegistry()

    class BlockingRegistrySnapshot(ProgressRegistry):
        def snapshot(self, queue_entry_id):
            # While this runs (simulating slow telemetry composition), the
            # runtime's OWN state lock must already be free.
            acquired = runtime._state_lock.acquire(timeout=1)
            if acquired:
                runtime._state_lock.release()
                acquired_holder["ok"] = True
            return super().snapshot(queue_entry_id)

    acquired_holder = {"ok": False}
    acquisition = SteppedAcquisition(steps=1, delay=0.0)
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url="http://example.test/A", destination_dir=Path("/tmp"))}
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    blocking_registry = BlockingRegistrySnapshot()
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        progress_registry=blocking_registry,
    )
    runtime.manager_snapshot()  # exercises the composition path directly (no start() needed)
    assert acquired_holder["ok"] is True
