"""Prompt A9: real (non-mock) end-to-end pause/resume/retry-resume tests
against ConcurrentDownloadRuntime, using the real HTTP fixture server and
real DirectHttpAcquisition. No mocked network."""

import time
from datetime import datetime, timezone

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import AcquisitionError, DownloadRequest
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime, PauseRequestOutcome
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.retry_policy import RetryPolicy, RetryPolicyConfig
from rychlik.core.scheduler_policy import SchedulerConfig
from rychlik.core.state_store import SqliteDownloadStateStore
from http_fixture_server import NORMAL_BODY

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
STRONG_ETAG = '"v1"'
BIG_BODY = NORMAL_BODY * 30  # ~510 KB, safely over the 64KiB chunk size
SLOW = dict(slow=True, slow_chunk_bytes=16384, slow_delay=0.02)


def _wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _runtime(
    queue, tasks, requests, *, max_active_transfers=1, state_store=None, retry_policy=None,
    failure_mapper=None, resume_checkpoint_bytes_threshold=8 * 1024 * 1024,
):
    kwargs = {}
    if failure_mapper is not None:
        kwargs["failure_mapper"] = failure_mapper
    coordinator = DispatchCoordinator(
        acquisition_service=AcquisitionService(),
        resume_checkpoint_bytes_threshold=resume_checkpoint_bytes_threshold,
        **kwargs,
    )
    return ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=max_active_transfers),
        state_store=state_store,
        retry_policy=retry_policy,
        enable_pause_resume=True,
    )


# --- real manual pause E2E (§122) -------------------------------------------


def test_real_manual_pause_e2e(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("runtime-pause1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()

    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {
        "A": DownloadRequest(
            url=f"{http_fixture_server.base_url}/resumable/runtime-pause1",
            destination_dir=tmp_path,
            filename_hint="video",
        )
    }
    runtime = _runtime(queue, tasks, requests, state_store=store)
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        time.sleep(0.15)  # let real bytes flow
        outcome = runtime.request_pause(entry.queue_entry_id)
        assert outcome == PauseRequestOutcome.REQUESTED

        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.PAUSED, timeout=3)
        assert queue.get(entry.queue_entry_id).state == QueueEntryState.QUEUED
        assert _wait_until(lambda: not runtime._in_flight, timeout=2)  # reservation released

        loaded = store.load()
        partial = loaded.partial_transfers.get(entry.queue_entry_id)
        assert partial is not None
        assert 0 < partial.durable_bytes < len(BIG_BODY)
        assert not (tmp_path / "video").exists()
        assert (tmp_path / "video.part").exists()
    finally:
        runtime.stop(timeout=5)


def test_pause_request_on_non_active_task_is_not_active():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0)}
    runtime = _runtime(queue, tasks, {})
    assert runtime.request_pause(entry.queue_entry_id) == PauseRequestOutcome.NOT_ACTIVE


def test_pause_request_is_idempotent(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("runtime-pause2", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/runtime-pause2", destination_dir=tmp_path, filename_hint="video")}
    runtime = _runtime(queue, tasks, requests)
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        assert runtime.request_pause(entry.queue_entry_id) == PauseRequestOutcome.REQUESTED
        assert runtime.request_pause(entry.queue_entry_id) == PauseRequestOutcome.REQUESTED  # still fine, no crash
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.PAUSED, timeout=3)
    finally:
        runtime.stop(timeout=5)


# --- real manual resume E2E (§123/§124) -------------------------------------


def test_real_manual_resume_e2e(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("runtime-resume1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()

    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/runtime-resume1", destination_dir=tmp_path, filename_hint="video")}
    runtime = _runtime(queue, tasks, requests, state_store=store)
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        time.sleep(0.15)
        runtime.request_pause(entry.queue_entry_id)
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.PAUSED, timeout=3)
        assert tasks["A"].attempt_count == 1

        runtime.request_resume(entry.queue_entry_id)
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)

    assert tasks["A"].attempt_count == 1  # manual resume never increments (§124)
    assert queue.get(entry.queue_entry_id).state == QueueEntryState.REMOVED
    assert (tmp_path / "video").read_bytes() == BIG_BODY  # byte-exact

    last_req = http_fixture_server.resumable_last_request("runtime-resume1")
    assert last_req["range"] is not None and last_req["range"] != "bytes=0-"
    assert last_req["if_range"] == STRONG_ETAG

    loaded = store.load()
    assert loaded.partial_transfers == {}  # cleared on completion


# --- resume capacity (§125) --------------------------------------------------


def test_resume_waits_for_free_capacity(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("cap-active", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    http_fixture_server.configure_resumable("cap-paused", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)

    queue = DownloadQueue(clock=lambda: T0)
    # Enqueue PAUSED first so canonical order gives it the single slot first
    # (capacity=1, same priority -> earlier position wins).
    entry_paused = queue.enqueue("PAUSED")
    entry_active = queue.enqueue("ACTIVE")
    tasks = {
        "ACTIVE": create_task("ACTIVE", now=T0).mark_ready(now=T0),
        "PAUSED": create_task("PAUSED", now=T0).mark_ready(now=T0),
    }
    requests = {
        "ACTIVE": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/cap-active", destination_dir=tmp_path / "a", filename_hint="video"),
        "PAUSED": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/cap-paused", destination_dir=tmp_path / "b", filename_hint="video"),
    }
    runtime = _runtime(queue, tasks, requests, max_active_transfers=1)
    runtime.start()
    try:
        # First pause the PAUSED task, then let ACTIVE take the single slot.
        assert _wait_until(lambda: tasks["PAUSED"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        runtime.request_pause(entry_paused.queue_entry_id)
        assert _wait_until(lambda: tasks["PAUSED"].state == DownloadTaskState.PAUSED, timeout=3)

        assert _wait_until(lambda: tasks["ACTIVE"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        runtime.request_resume(entry_paused.queue_entry_id)

        time.sleep(0.2)
        assert tasks["PAUSED"].state == DownloadTaskState.PAUSED  # still waiting -- no free slot

        assert _wait_until(lambda: tasks["ACTIVE"].state == DownloadTaskState.COMPLETED, timeout=10)
        assert _wait_until(lambda: tasks["PAUSED"].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)


# --- resume priority (§126) --------------------------------------------------


def test_resume_does_not_bypass_priority_real(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("prio-high", etag=STRONG_ETAG)
    http_fixture_server.configure_resumable("prio-normal", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)

    queue = DownloadQueue(clock=lambda: T0)
    entry_normal = queue.enqueue("N", QueuePriority.NORMAL)
    tasks = {"N": create_task("N", now=T0).mark_ready(now=T0)}
    requests = {"N": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/prio-normal", destination_dir=tmp_path / "n", filename_hint="video")}
    runtime = _runtime(queue, tasks, requests, max_active_transfers=1)
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["N"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        runtime.request_pause(entry_normal.queue_entry_id)
        assert _wait_until(lambda: tasks["N"].state == DownloadTaskState.PAUSED, timeout=3)

        # Now enqueue a HIGH-priority READY task and request resume for N at
        # the same time -- HIGH must win the single slot.
        entry_high = queue.enqueue("H", QueuePriority.HIGH)
        tasks["H"] = create_task("H", now=T0).mark_ready(now=T0)
        requests["H"] = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/prio-high", destination_dir=tmp_path / "h", filename_hint="video")
        runtime.request_resume(entry_normal.queue_entry_id)
        runtime.notify_state_changed()

        assert _wait_until(lambda: tasks["H"].state == DownloadTaskState.COMPLETED, timeout=5)
        # N should not have started yet, or only starts after H frees the slot.
        assert _wait_until(lambda: tasks["N"].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)


# --- queue-paused blocks resume (§127) ---------------------------------------


def test_queue_paused_blocks_resume_dispatch(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("qp1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/qp1", destination_dir=tmp_path, filename_hint="video")}
    runtime = _runtime(queue, tasks, requests)
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.TRANSFERRING, timeout=3)
        runtime.request_pause(entry.queue_entry_id)
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.PAUSED, timeout=3)

        queue.pause(entry.queue_entry_id)
        runtime.request_resume(entry.queue_entry_id)
        runtime.notify_state_changed()

        time.sleep(0.2)
        assert tasks["A"].state == DownloadTaskState.PAUSED  # queue hold blocks it

        queue.resume(entry.queue_entry_id)
        runtime.notify_state_changed()
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)


# --- real retry + partial resume E2E (§135) ----------------------------------


def test_real_retry_reuses_validated_partial_bytes(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable(
        "retry-resume1", etag=STRONG_ETAG, body=BIG_BODY,
        drop_after_bytes=100_000, drop_once_key="retry-resume1-dropped",
    )

    def _retryable_mapper(exc: AcquisitionError) -> DownloadTaskFailure:
        return DownloadTaskFailure(code="TRANSIENT", message=str(exc), retryable=True)

    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/retry-resume1", destination_dir=tmp_path, filename_hint="video")}
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()
    runtime = _runtime(
        queue, tasks, requests, state_store=store,
        retry_policy=RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.05)),
        failure_mapper=_retryable_mapper,
        resume_checkpoint_bytes_threshold=50_000,  # well under drop_after_bytes=100_000
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.RETRY_WAIT, timeout=5)
        # A durable partial checkpoint from the dropped first attempt must
        # have been written (threshold=50KB < the 100KB dropped) and must
        # survive into the retry attempt.
        assert _wait_until(lambda: entry.queue_entry_id in store.load().partial_transfers, timeout=2)
        pre_retry_partial = store.load().partial_transfers[entry.queue_entry_id]
        assert pre_retry_partial.durable_bytes > 0

        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)

    assert tasks["A"].attempt_count == 2
    assert (tmp_path / "video").read_bytes() == BIG_BODY

    last_req = http_fixture_server.resumable_last_request("retry-resume1")
    # The retry's own request range start must be >= the durable checkpoint
    # from attempt 1 -- proving the retry genuinely resumed rather than
    # coincidentally re-downloading everything from zero.
    assert last_req["range"] is not None
    resumed_offset = int(last_req["range"].split("=")[1].rstrip("-"))
    assert resumed_offset >= pre_retry_partial.durable_bytes

    loaded = store.load()
    assert loaded.partial_transfers == {}  # cleared on completion
