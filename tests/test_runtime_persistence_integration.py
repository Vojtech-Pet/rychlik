"""Prompt A8: ConcurrentDownloadRuntime <-> SqliteDownloadStateStore wiring,
plus real clean-restart / retry-restart end-to-end tests using a real
SQLite file and the real HTTP fixture server (no mocks)."""

import time
from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import AcquisitionError, DownloadRequest
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.restart_recovery import RestartRecovery
from rychlik.core.retry_policy import RetryPolicy, RetryPolicyConfig
from rychlik.core.scheduler_policy import SchedulerConfig
from rychlik.core.state_store import SqliteDownloadStateStore
from http_fixture_server import NORMAL_BODY

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --- checkpoint_task() public API -------------------------------------------


def test_checkpoint_task_is_noop_without_state_store():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0)}
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
    runtime = ConcurrentDownloadRuntime(
        queue=queue, tasks=tasks, requests={}, coordinator=coordinator, config=SchedulerConfig(max_active_transfers=1)
    )
    runtime.checkpoint_task("A", queue_entry_id=entry.queue_entry_id)  # must not raise


def test_checkpoint_task_persists_current_facts(tmp_path):
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A", QueuePriority.HIGH)
    tasks = {"A": create_task("A", now=T0)}
    requests = {"A": DownloadRequest(url="http://x/y", destination_dir=tmp_path)}
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
    runtime = ConcurrentDownloadRuntime(
        queue=queue, tasks=tasks, requests=requests, coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1), state_store=store,
    )
    runtime.checkpoint_task("A", queue_entry_id=entry.queue_entry_id)

    loaded = store.load()
    assert loaded.tasks["A"].task_id == "A"
    assert loaded.queue_entries[entry.queue_entry_id].priority == QueuePriority.HIGH
    assert loaded.requests["A"].url == "http://x/y"


# --- real end-to-end dispatch checkpoints via the runtime -------------------


def test_real_download_checkpoints_pre_network_and_terminal(http_fixture_server, tmp_path):
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url=f"{http_fixture_server.base_url}/slow", destination_dir=tmp_path)}
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
    runtime = ConcurrentDownloadRuntime(
        queue=queue, tasks=tasks, requests=requests, coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1), state_store=store,
    )
    runtime.start()
    try:
        # Observe the durable pre-network TRANSFERRING checkpoint while the
        # transfer is still genuinely in flight (real HTTP, no mock).
        observed_transferring = _wait_until(
            lambda: store.load().tasks.get("A") is not None
            and store.load().tasks["A"].state == DownloadTaskState.TRANSFERRING
        )
        assert observed_transferring

        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)

    loaded = store.load()
    assert loaded.tasks["A"].state == DownloadTaskState.COMPLETED
    assert loaded.queue_entries[entry.queue_entry_id].state == QueueEntryState.REMOVED


def test_real_retry_persists_schedule_and_clears_it_on_promotion(http_fixture_server, tmp_path):
    http_fixture_server.configure_flaky("a8-retry", fail_until=1)
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()

    def _retryable_mapper(exc: AcquisitionError):
        return DownloadTaskFailure(code="TRANSIENT", message=str(exc), retryable=True)

    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url=f"{http_fixture_server.base_url}/flaky/a8-retry", destination_dir=tmp_path)}
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService(), failure_mapper=_retryable_mapper)
    runtime = ConcurrentDownloadRuntime(
        queue=queue, tasks=tasks, requests=requests, coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        retry_policy=RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.2)),
        state_store=store,
    )
    runtime.start()
    try:
        assert _wait_until(
            lambda: entry.queue_entry_id in store.load().retry_schedules, timeout=3
        )
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=5)
    finally:
        runtime.stop(timeout=5)

    loaded = store.load()
    assert loaded.retry_schedules == {}  # cleared on promotion to READY
    assert loaded.tasks["A"].attempt_count == 2


# --- real clean restart E2E (§98) -------------------------------------------


def test_real_clean_restart_preserves_identity_order_and_pause(tmp_path):
    db_path = tmp_path / "state.db"
    store = SqliteDownloadStateStore(db_path)
    store.initialize()

    queue = DownloadQueue(clock=lambda: T0)
    h1 = queue.enqueue("H1", QueuePriority.HIGH)
    n1 = queue.enqueue("N1", QueuePriority.NORMAL)
    n2 = queue.enqueue("N2", QueuePriority.NORMAL)
    queue.pause(n2.queue_entry_id)
    tasks = {
        "H1": create_task("H1", now=T0).mark_ready(now=T0),
        "N1": create_task("N1", now=T0),
        "N2": create_task("N2", now=T0).mark_ready(now=T0),
    }
    requests = {tid: DownloadRequest(url=f"http://x/{tid}", destination_dir=tmp_path) for tid in tasks}

    for tid, task in tasks.items():
        qe = next(e for e in queue.active_entries() if e.task_id == tid)
        store.checkpoint_task_state(task=task, request=requests[tid], queue_entry=qe)
    store.mark_clean_shutdown()
    store.close()

    # --- fresh process/runtime instance ---
    store2 = SqliteDownloadStateStore(db_path)
    store2.initialize()
    clean = store2.get_previous_shutdown_clean()
    store2.mark_session_dirty()
    persisted = store2.load()
    result = RestartRecovery().recover(persisted, now=T0, monotonic_now=0.0, previous_shutdown_clean=clean)

    assert clean is True
    new_queue = DownloadQueue.restore(list(result.state.queue_entries.values()))
    assert [e.task_id for e in new_queue.active_entries()] == ["H1", "N1", "N2"]
    assert new_queue.get(n2.queue_entry_id).state == QueueEntryState.PAUSED
    assert {e.queue_entry_id for e in new_queue.active_entries()} == {
        h1.queue_entry_id, n1.queue_entry_id, n2.queue_entry_id
    }
    assert result.state.tasks["N1"].state == DownloadTaskState.CREATED  # unchanged, was never transient


# --- stale .part safety (§93) -----------------------------------------------


def test_stale_part_file_does_not_corrupt_fresh_download(http_fixture_server, tmp_path):
    from rychlik.acquisition.direct_http import DirectHttpAcquisition

    request = DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path)
    stale_part = tmp_path / "normal.mp4.part"
    stale_part.write_bytes(b"GARBAGE" * 10000)  # much larger than the real body

    completed = DirectHttpAcquisition().acquire(request)
    assert completed.final_path.read_bytes() == NORMAL_BODY
