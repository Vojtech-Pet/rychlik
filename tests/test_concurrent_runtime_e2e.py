"""Real, non-mock end-to-end tests for ConcurrentDownloadRuntime (Prompt A5
§68-72). Uses the real AcquisitionService/DirectHttpAcquisition against the
deterministic local HTTP fixture server's /track route, which proves real
concurrency by a shared, lock-protected active-request counter observed
directly in-process -- not by guessing overlap from wall-clock timing.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.artifact import Artifact
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue
from rychlik.core.download_task import DownloadTaskState, create_task
from rychlik.core.scheduler_policy import SchedulerConfig
from http_fixture_server import NORMAL_BODY

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _wait_until(predicate, timeout=10.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _build(names, base_url, tmp_path, *, max_active_transfers):
    queue = DownloadQueue(clock=lambda: T0)
    tasks = {}
    requests = {}
    for name in names:
        queue.enqueue(name)
        tasks[name] = create_task(name, now=T0).mark_ready(now=T0)
        requests[name] = DownloadRequest(url=f"{base_url}/track", destination_dir=tmp_path / name)
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=max_active_transfers),
    )
    return queue, tasks, requests, runtime


def test_real_http_concurrency_e2e(http_fixture_server, tmp_path):
    http_fixture_server.reset_track()
    queue, tasks, requests, runtime = _build(
        ["A", "B"], http_fixture_server.base_url, tmp_path, max_active_transfers=2
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(10), timeout=10)
        assert http_fixture_server.max_observed_active == 2  # real overlap, not simulated
        assert tasks["A"].state == DownloadTaskState.COMPLETED
        assert tasks["B"].state == DownloadTaskState.COMPLETED
        for name in ["A", "B"]:
            final_path = tmp_path / name / "track"
            assert final_path.read_bytes() == NORMAL_BODY
            assert list((tmp_path / name).glob("*.part")) == []
    finally:
        runtime.stop(timeout=10)


def test_real_http_max_one_e2e(http_fixture_server, tmp_path):
    http_fixture_server.reset_track()
    queue, tasks, requests, runtime = _build(
        ["A", "B"], http_fixture_server.base_url, tmp_path, max_active_transfers=1
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(10), timeout=10)
        assert http_fixture_server.max_observed_active == 1  # never overlapped
        assert tasks["A"].state == DownloadTaskState.COMPLETED
        assert tasks["B"].state == DownloadTaskState.COMPLETED
    finally:
        runtime.stop(timeout=10)


def test_real_http_three_task_refill_e2e(http_fixture_server, tmp_path):
    http_fixture_server.reset_track()
    queue, tasks, requests, runtime = _build(
        ["A", "B", "C"], http_fixture_server.base_url, tmp_path, max_active_transfers=2
    )
    start = time.monotonic()
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(10), timeout=10)
        elapsed = time.monotonic() - start

        assert http_fixture_server.max_observed_active == 2  # capacity respected
        # Two waves of ~0.3s each (A+B, then C alone) should complete well
        # under three fully-serialized waves (~0.9s) -- proving automatic
        # refill happened through the real network path, not just capacity.
        assert elapsed < 0.85
        assert all(tasks[t].state == DownloadTaskState.COMPLETED for t in "ABC")
    finally:
        runtime.stop(timeout=10)


def test_real_http_artifact_continuity(http_fixture_server, tmp_path):
    """Proves A5 did not break the CompletedDownload -> Artifact path."""
    http_fixture_server.reset_track()
    queue, tasks, requests, runtime = _build(
        ["A"], http_fixture_server.base_url, tmp_path, max_active_transfers=1
    )
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.wait_for_idle(10), timeout=10)
        completion = runtime.drain_completions()[0]
        assert completion.result.outcome.name == "COMPLETED"

        artifact = Artifact.from_completed_download(
            completion.result.completed_download.final_path,
            source_url=completion.result.completed_download.source_url,
        )
        assert artifact.local_path.exists()
        assert artifact.size == len(NORMAL_BODY)
        assert artifact.sha256
    finally:
        runtime.stop(timeout=10)
