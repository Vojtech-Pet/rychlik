"""Prompt A17 real crash-to-Range-resume E2E helper process.

Not a test module (no test_ prefix, not collected by pytest). Started as a
real subprocess by test_a9_crash_range_e2e.py in test_a9_crash_range_e2e.py,
then killed ungracefully (SIGKILL) by the parent -- same shape as A8's
_a8_crash_worker.py, but downloads against the Range/ETag-capable
`/resumable/<key>` fixture route with a small checkpoint threshold, so a
durable partial checkpoint with a real remote validator exists before the
kill, making a real HTTP Range resume possible after recovery (which the
plain `/slow` route used by A8's worker cannot prove, since it never emits
a resume validator).

Usage: python _a9_crash_range_worker.py <db_path> <base_url> <task_id> <key>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import create_task
from rychlik.core.scheduler_policy import SchedulerConfig
from rychlik.core.state_store import SqliteDownloadStateStore

_CHECKPOINT_THRESHOLD = 50_000  # small: checkpoints reliably before the parent SIGKILLs


def main() -> None:
    db_path, base_url, task_id, key = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    dest_dir = Path(db_path).parent / "downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    store = SqliteDownloadStateStore(Path(db_path))
    store.initialize()
    store.mark_session_dirty()  # this process now owns the session (§21, A8)

    queue = DownloadQueue()
    entry = queue.enqueue(task_id, QueuePriority.NORMAL)
    task = create_task(task_id, now=_now()).mark_ready(now=_now())
    tasks = {task_id: task}
    requests = {task_id: DownloadRequest(url=f"{base_url}/resumable/{key}", destination_dir=dest_dir)}

    coordinator = DispatchCoordinator(
        acquisition_service=AcquisitionService(),
        resume_checkpoint_bytes_threshold=_CHECKPOINT_THRESHOLD,
    )
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        state_store=store,
    )
    store.checkpoint_task_state(task=task, request=requests[task_id], queue_entry=entry)
    runtime.start()

    print("READY", flush=True)
    time.sleep(120)  # parent SIGKILLs this process well before this elapses


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
