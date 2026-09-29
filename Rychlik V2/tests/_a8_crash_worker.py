"""Prompt A8 real-crash E2E helper process.

Not a test module (no test_ prefix, not collected by pytest). Started as a
real subprocess by test_real_crash_recovery_e2e in
test_real_process_crash_e2e.py, then killed ungracefully (SIGKILL) by the
parent to prove restart recovery works after an actual process death, not
just a simulated one.

Usage: python _a8_crash_worker.py <db_path> <base_url> <task_id>
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


def main() -> None:
    db_path, base_url, task_id = sys.argv[1], sys.argv[2], sys.argv[3]
    dest_dir = Path(db_path).parent / "downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    store = SqliteDownloadStateStore(Path(db_path))
    store.initialize()
    store.mark_session_dirty()  # this process now owns the session (§21)

    queue = DownloadQueue()
    entry = queue.enqueue(task_id, QueuePriority.NORMAL)
    task = create_task(task_id, now=_now()).mark_ready(now=_now())
    tasks = {task_id: task}
    requests = {task_id: DownloadRequest(url=f"{base_url}/slow", destination_dir=dest_dir)}

    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
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
