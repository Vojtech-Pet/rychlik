"""Prompt A10: real (non-mock) HTTP end-to-end tests treating
DownloadManagerService as the system boundary -- no lower-layer object
(DownloadQueue, SchedulerPolicy, ConcurrentDownloadRuntime, ...) is ever
touched directly by these tests except where noted for fixture setup."""

import time

from rychlik.acquisition.contracts import AcquisitionError, DownloadRequest
from rychlik.core.download_manager_service import (
    CommandStatus,
    DownloadManagerConfig,
    DownloadManagerService,
)
from rychlik.core.download_queue import QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState
from rychlik.core.retry_policy import RetryPolicyConfig
from http_fixture_server import NORMAL_BODY

STRONG_ETAG = '"v1"'
BIG_BODY = NORMAL_BODY * 30  # ~510 KB
SLOW = dict(slow=True, slow_chunk_bytes=16384, slow_delay=0.02)


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _manager(tmp_path, **overrides) -> DownloadManagerService:
    config = DownloadManagerConfig(database_path=tmp_path / "state.db", **overrides)
    return DownloadManagerService(config=config)


# --- real concurrent downloads through the facade (§91/§131) ----------------


def test_real_concurrent_downloads_through_facade(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=2)
    manager.start()
    try:
        a = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path / "a", filename_hint="video")
        )
        b = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/with-content-disposition", destination_dir=tmp_path / "b")
        )

        def _both_done():
            snap = manager.snapshot()
            states = {item.queue_entry_id: item.task_state for item in snap.items}
            # Completed items are removed from the active snapshot (A7
            # convention) -- absence from `states` also counts as done.
            return all(
                states.get(qid, DownloadTaskState.COMPLETED) == DownloadTaskState.COMPLETED
                for qid in (a.queue_entry_id, b.queue_entry_id)
            )

        assert _wait_until(_both_done, timeout=10)
        assert (tmp_path / "a" / "video").exists()
        assert (tmp_path / "b" / "named-file.mp4").exists()
    finally:
        manager.stop()


def test_facade_snapshot_shows_real_progress_during_transfer(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("svc-progress", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/svc-progress", destination_dir=tmp_path, filename_hint="video")
        )
        observed_progress = _wait_until(
            lambda: any(
                item.queue_entry_id == result.queue_entry_id and item.bytes_downloaded > 0
                for item in manager.snapshot().items
            ),
            timeout=5,
        )
        assert observed_progress
        assert _wait_until(
            lambda: not any(item.queue_entry_id == result.queue_entry_id for item in manager.snapshot().items),
            timeout=10,
        )
    finally:
        manager.stop()


# --- real priority ordering through the facade (§93) -------------------------


def test_facade_priority_ordering(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        low = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path / "low", filename_hint="video"),
            priority=QueuePriority.LOW,
        )
        manager.hold(low.queue_entry_id)  # keep it from racing the HIGH one
        high = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path / "high", filename_hint="video"),
            priority=QueuePriority.HIGH,
        )
        assert _wait_until(
            lambda: not any(item.queue_entry_id == high.queue_entry_id for item in manager.snapshot().items), timeout=5
        )
        manager.release_hold(low.queue_entry_id)
        assert _wait_until(
            lambda: not any(item.queue_entry_id == low.queue_entry_id for item in manager.snapshot().items), timeout=5
        )
    finally:
        manager.stop()


# --- real hold E2E (§94) ------------------------------------------------------


def test_facade_hold_prevents_dispatch_until_released(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
        )
        manager.hold(result.queue_entry_id)
        time.sleep(0.2)
        item = manager.item_snapshot(result.queue_entry_id)
        assert item.task_state == DownloadTaskState.READY
        assert item.queue_state.name == "PAUSED"

        manager.release_hold(result.queue_entry_id)
        assert _wait_until(
            lambda: not any(i.queue_entry_id == result.queue_entry_id for i in manager.snapshot().items), timeout=5
        )
    finally:
        manager.stop()


# --- real pause/resume E2E (§95/§132) -----------------------------------------


def test_facade_real_pause_resume(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("svc-pause1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/svc-pause1", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.TRANSFERRING, timeout=3
        )
        time.sleep(0.15)
        outcome = manager.pause_transfer(result.queue_entry_id)
        assert outcome.status == CommandStatus.ACCEPTED
        assert _wait_until(
            lambda: manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.PAUSED, timeout=3
        )

        resume_outcome = manager.resume_transfer(result.queue_entry_id)
        assert resume_outcome.status == CommandStatus.ACCEPTED
        assert _wait_until(
            lambda: not any(i.queue_entry_id == result.queue_entry_id for i in manager.snapshot().items), timeout=10
        )
        assert (tmp_path / "video").read_bytes() == BIG_BODY
    finally:
        manager.stop()


def test_facade_resume_blocked_by_queue_hold(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("svc-pause2", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/svc-pause2", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.TRANSFERRING, timeout=3
        )
        manager.pause_transfer(result.queue_entry_id)
        assert _wait_until(
            lambda: manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.PAUSED, timeout=3
        )
        manager.hold(result.queue_entry_id)
        manager.resume_transfer(result.queue_entry_id)
        time.sleep(0.2)
        assert manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.PAUSED  # still blocked

        manager.release_hold(result.queue_entry_id)
        assert _wait_until(
            lambda: not any(i.queue_entry_id == result.queue_entry_id for i in manager.snapshot().items), timeout=10
        )
    finally:
        manager.stop()


# --- real cancel E2E (§96/§97/§134) -------------------------------------------


def test_facade_cancel_waiting_no_network(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        active = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path / "a", filename_hint="video")
        )
        held = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path / "b", filename_hint="video")
        )
        manager.hold(held.queue_entry_id)  # keep it waiting, never dispatched
        outcome = manager.cancel(held.queue_entry_id)
        assert outcome.status == CommandStatus.APPLIED
        item = manager.item_snapshot(held.queue_entry_id)
        assert item.task_state == DownloadTaskState.CANCELLED
        assert item.queue_state.name == "REMOVED"
    finally:
        manager.stop()


def test_facade_cancel_active_transfer(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("svc-cancel1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/svc-cancel1", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.TRANSFERRING, timeout=3
        )
        outcome = manager.cancel(result.queue_entry_id)
        assert outcome.status == CommandStatus.ACCEPTED

        assert _wait_until(
            lambda: not any(i.queue_entry_id == result.queue_entry_id for i in manager.snapshot().items), timeout=5
        )
        assert not (tmp_path / "video").exists()
    finally:
        manager.stop()


# --- real retry E2E (§98/§133) -------------------------------------------------


def _retryable_mapper(exc: AcquisitionError) -> DownloadTaskFailure:
    return DownloadTaskFailure(code="TRANSIENT", message=str(exc), retryable=True)


def test_facade_real_retry_completes_automatically(http_fixture_server, tmp_path):
    http_fixture_server.configure_flaky("svc-retry1", fail_until=1)

    manager = DownloadManagerService(
        config=DownloadManagerConfig(
            database_path=tmp_path / "state.db", max_active_transfers=1,
            retry_policy_config=RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.05),
        ),
        failure_mapper=_retryable_mapper,
    )
    manager.start()
    try:
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/flaky/svc-retry1", destination_dir=tmp_path, filename_hint="video")
        )
        # Caller only ever calls add_download() -- A6's automatic retry runs
        # entirely inside the composed backend, never touched directly here.
        assert _wait_until(
            lambda: not any(i.queue_entry_id == result.queue_entry_id for i in manager.snapshot().items), timeout=5
        )
        assert (tmp_path / "video").exists()
    finally:
        manager.stop()


# --- real restart E2E (§102/§135) --------------------------------------------


def test_facade_restart_preserves_state_and_completes(http_fixture_server, tmp_path):
    manager1 = _manager(tmp_path, max_active_transfers=0)
    manager1.start()
    result = manager1.add_download(
        DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
    )
    manager1.stop()

    manager2 = _manager(tmp_path, max_active_transfers=1)
    manager2.start()
    try:
        assert _wait_until(
            lambda: not any(i.queue_entry_id == result.queue_entry_id for i in manager2.snapshot().items), timeout=5
        )
        assert (tmp_path / "video").exists()
    finally:
        manager2.stop()
