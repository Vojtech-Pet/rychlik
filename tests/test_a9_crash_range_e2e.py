"""Prompt A17 §6-13: the combined A9-CRASH-RANGE-E2E scenario.

Closes the `A9-CRASH-RANGE-E2E` validation debt item (see
docs/OPEN_VALIDATION_DEBT.md). Unlike A8's `test_real_crash_recovery_e2e`
(tests/test_real_process_crash_e2e.py, which downloads against `/slow` --
a route with no ETag/Last-Modified, so redownload after recovery is
always a full restart from byte 0) and unlike A9's
`test_real_manual_resume_e2e`/`test_real_retry_reuses_validated_partial_bytes`
(which prove a real Range resume, but only via a Python-level pause/retry
within the same process, never an actual killed process), this test proves
the full causal chain as ONE real scenario:

    real subprocess starts a real Range-capable HTTP download
        -> a durable partial checkpoint is written (durable_bytes > 0)
        -> the process is SIGKILLed mid-transfer (real, ungraceful)
        -> a fresh process opens the same database
        -> RestartRecovery runs (TRANSFERRING -> READY, attempt_count preserved)
        -> normal dispatch issues a REAL HTTP Range request using the
           recovered, locally-re-validated partial (not the raw .part size)
        -> byte-exact completion, attempt_count incremented
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueueEntryState
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.restart_recovery import RestartRecovery
from rychlik.core.scheduler_policy import SchedulerConfig
from rychlik.core.state_store import SqliteDownloadStateStore
from http_fixture_server import NORMAL_BODY

_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
_WORKER_SCRIPT = str(Path(__file__).resolve().parent / "_a9_crash_range_worker.py")

STRONG_ETAG = '"a17-crash-range-v1"'
BIG_BODY = NORMAL_BODY * 60  # ~1MB: comfortably larger than the worker's 50_000-byte checkpoint threshold


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_a9_crash_range_e2e(http_fixture_server, tmp_path):
    """The exact combined chain required to close A9-CRASH-RANGE-E2E (Prompt A17 §6-13)."""
    key = "a9-crash-range"
    http_fixture_server.reset_track()
    http_fixture_server.configure_resumable(
        key, etag=STRONG_ETAG, body=BIG_BODY, slow=True, slow_chunk_bytes=16384, slow_delay=0.02
    )
    db_path = tmp_path / "state.db"
    task_id = "A"

    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_DIR

    child = subprocess.Popen(
        [sys.executable, _WORKER_SCRIPT, str(db_path), http_fixture_server.base_url, task_id, key],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # --- §8: prove a durable checkpoint exists BEFORE the kill ---------
        def _durable_checkpoint():
            if not db_path.exists():
                return None
            try:
                probe = SqliteDownloadStateStore(db_path)
                probe.initialize()
                state = probe.load()
                probe.close()
            except Exception:
                return None
            task = state.tasks.get(task_id)
            if task is None or task.state != DownloadTaskState.TRANSFERRING:
                return None
            entry = next((e for e in state.queue_entries.values() if e.task_id == task_id), None)
            if entry is None:
                return None
            partial = state.partial_transfers.get(entry.queue_entry_id)
            if partial is None or partial.durable_bytes <= 0:
                return None
            return partial

        checkpoint_holder = {}

        def _checkpoint_ready() -> bool:
            partial = _durable_checkpoint()
            if partial is not None:
                checkpoint_holder["partial"] = partial
            return partial is not None

        assert _wait_until(_checkpoint_ready, timeout=10), (
            f"child never produced a durable partial checkpoint; stdout={child.stdout}"
        )
        pre_kill_partial = checkpoint_holder["partial"]
        assert pre_kill_partial.durable_bytes > 0
        assert pre_kill_partial.prefix_sha256
        assert pre_kill_partial.validator_value == STRONG_ETAG

        # --- §9: the raw .part file on disk may have outrun the durable
        # checkpoint (bytes written to disk but not yet fsynced/committed as
        # durable) -- recovery must trust only durable_bytes, never the raw
        # file size. This is a soft/best-effort check: under the slow-write
        # fixture config it is very likely, but not guaranteed, to observe
        # more raw bytes than the last checkpoint by the time we sample it.
        raw_part_size = pre_kill_partial.temp_path.stat().st_size if pre_kill_partial.temp_path.exists() else 0
        assert raw_part_size >= pre_kill_partial.durable_bytes

        # SIGKILL: no graceful shutdown, no chance to mark_clean_shutdown().
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

    # --- §10: fresh process-local recovery ---------------------------------
    store = SqliteDownloadStateStore(db_path)
    store.initialize()
    previous_clean = store.get_previous_shutdown_clean()
    store.mark_session_dirty()
    assert previous_clean is False  # the killed child never reached mark_clean_shutdown()

    persisted = store.load()
    pre_recovery_task = persisted.tasks[task_id]
    assert pre_recovery_task.state == DownloadTaskState.TRANSFERRING
    assert pre_recovery_task.attempt_count == 1

    result = RestartRecovery().recover(
        persisted, now=datetime.now(timezone.utc), monotonic_now=time.monotonic(), previous_shutdown_clean=previous_clean
    )
    recovered_task = result.state.tasks[task_id]
    assert recovered_task.state == DownloadTaskState.READY
    assert recovered_task.attempt_count == 1  # §12: interrupted attempt still counted, not reset
    store.replace_all(result.state)

    recovered_queue = DownloadQueue.restore(list(result.state.queue_entries.values()))
    recovered_entry = next(e for e in recovered_queue.active_entries() if e.task_id == task_id)
    assert recovered_entry.state == QueueEntryState.QUEUED

    # the durable partial (and its remote validator) must have survived recovery untouched
    recovered_partial = result.state.partial_transfers[recovered_entry.queue_entry_id]
    assert recovered_partial.durable_bytes == pre_kill_partial.durable_bytes
    assert recovered_partial.validator_value == STRONG_ETAG

    # --- §11/§13: redownload for real -- a brand-new runtime, same process now ---
    tasks = {task_id: recovered_task}
    requests = dict(result.state.requests)
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
    runtime = ConcurrentDownloadRuntime(
        queue=recovered_queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        state_store=store,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks[task_id].state == DownloadTaskState.COMPLETED, timeout=10)
    finally:
        runtime.stop(timeout=5)

    # §11: the real HTTP request actually observed by the fixture server must
    # carry a Range starting exactly at the persisted durable_bytes -- never
    # "bytes=0-", never a recomputed/raw .part size.
    last_req = http_fixture_server.resumable_last_request(key)
    assert last_req is not None
    assert last_req["range"] == f"bytes={pre_kill_partial.durable_bytes}-"
    assert last_req["if_range"] == STRONG_ETAG

    # §12: a new attempt on top of the preserved interrupted one
    assert tasks[task_id].attempt_count == 2

    # §13: final byte-exact integrity, and the crash/resume machinery cleans up fully
    final_path = requests[task_id].destination_dir / key
    assert final_path.read_bytes() == BIG_BODY
    loaded = store.load()
    assert loaded.tasks[task_id].state == DownloadTaskState.COMPLETED
    assert loaded.queue_entries[recovered_entry.queue_entry_id].state == QueueEntryState.REMOVED
    assert recovered_entry.queue_entry_id not in loaded.partial_transfers

    store.mark_clean_shutdown()
    store.close()
