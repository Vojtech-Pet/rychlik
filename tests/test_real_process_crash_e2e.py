"""Prompt A8, §120-125: a REAL subprocess crash + restart recovery E2E.

A separate Python process opens the real SQLite state store, starts a real
ConcurrentDownloadRuntime, and begins a real (slow) HTTP download against
the shared fixture server. Once the parent observes real evidence that the
child is genuinely mid-transfer (bytes flowing into the fixture server AND
a durable TRANSFERRING row in the database), the parent SIGKILLs the child
-- no graceful shutdown, no cleanup, exactly an ungraceful process death.

The parent then opens the SAME database file in-process, runs
RestartRecovery, and proves: the previous session is reported unclean, the
interrupted TRANSFERRING task recovers to READY with its identity and
attempt_count preserved, and a brand-new attempt (started by a fresh
runtime in the parent process) completes with attempt_count incremented
and a byte-exact final file -- proving the stale .part file left behind by
the killed child cannot corrupt the redownload.
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
_WORKER_SCRIPT = str(Path(__file__).resolve().parent / "_a8_crash_worker.py")


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_real_crash_recovery_e2e(http_fixture_server, tmp_path):
    http_fixture_server.reset_track()
    db_path = tmp_path / "state.db"
    task_id = "A"

    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_DIR

    child = subprocess.Popen(
        [sys.executable, _WORKER_SCRIPT, str(db_path), http_fixture_server.base_url, task_id],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for real evidence the child is genuinely mid-transfer: bytes
        # actually reaching the fixture server's /slow handler AND a durable
        # TRANSFERRING row committed to the real SQLite file.
        def _child_transferring() -> bool:
            if not db_path.exists():
                return False
            try:
                probe = SqliteDownloadStateStore(db_path)
                probe.initialize()
                state = probe.load()
                probe.close()
            except Exception:
                return False
            task = state.tasks.get(task_id)
            return task is not None and task.state == DownloadTaskState.TRANSFERRING

        assert _wait_until(_child_transferring, timeout=10), (
            f"child never reached TRANSFERRING; stdout={child.stdout}"
        )

        # SIGKILL: no graceful shutdown, no chance to mark_clean_shutdown().
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

    # --- fresh process-local recovery (this test process stands in for the
    # "new Rychlik process") ---
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
    assert recovered_task.attempt_count == 1  # interrupted attempt still counted (§40)
    store.replace_all(result.state)

    recovered_queue = DownloadQueue.restore(list(result.state.queue_entries.values()))
    recovered_entry = next(e for e in recovered_queue.active_entries() if e.task_id == task_id)
    assert recovered_entry.state == QueueEntryState.QUEUED

    # --- redownload for real: a brand-new runtime, same process now ---
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

    assert tasks[task_id].attempt_count == 2  # new attempt on top of the preserved interrupted one
    final_path = requests[task_id].destination_dir / "slow"
    assert final_path.read_bytes() == NORMAL_BODY  # byte-exact despite the killed child's stale .part

    loaded = store.load()
    assert loaded.tasks[task_id].state == DownloadTaskState.COMPLETED
    assert loaded.queue_entries[recovered_entry.queue_entry_id].state == QueueEntryState.REMOVED

    store.mark_clean_shutdown()
    store.close()
    store2 = SqliteDownloadStateStore(db_path)
    store2.initialize()
    assert store2.get_previous_shutdown_clean() is True
