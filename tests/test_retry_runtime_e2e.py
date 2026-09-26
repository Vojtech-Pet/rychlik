"""Real, non-mock end-to-end tests for the Prompt A6 retry runtime.

Uses the real AcquisitionService/DirectHttpAcquisition against the local
HTTP fixture server's /flaky/<key> route (deterministic 503-then-200), the
real DispatchCoordinator, and the real ConcurrentDownloadRuntime/
RetryPolicy. No mock network.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import AcquisitionError, DownloadRequest
from rychlik.core.artifact import Artifact
from rychlik.core.concurrent_runtime import ConcurrentDownloadRuntime
from rychlik.core.dispatch_coordinator import DispatchCoordinator
from rychlik.core.download_queue import DownloadQueue
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.retry_policy import RetryPolicy, RetryPolicyConfig
from rychlik.core.scheduler_policy import SchedulerConfig
from http_fixture_server import NORMAL_BODY

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _retryable_mapper(exc: AcquisitionError) -> DownloadTaskFailure:
    """A test-specific mapper (§86): the production default_failure_mapper
    is conservatively retryable=False for every AcquisitionError, since no
    HTTP-status taxonomy exists yet (deliberately, per A4/A6 scope). This
    proves A6's retry ORCHESTRATION over a real transient failure, not a
    final HTTP retry-classification policy -- no string matching is used."""
    return DownloadTaskFailure(code="TRANSIENT_HTTP", message=str(exc), retryable=True)


def test_real_http_transient_retry_e2e(http_fixture_server, tmp_path):
    http_fixture_server.configure_flaky("a6-transient", fail_until=1)  # 1st call fails, 2nd succeeds

    queue = DownloadQueue(clock=lambda: T0)
    queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {
        "A": DownloadRequest(
            url=f"{http_fixture_server.base_url}/flaky/a6-transient", destination_dir=tmp_path
        )
    }
    coordinator = DispatchCoordinator(
        acquisition_service=AcquisitionService(), failure_mapper=_retryable_mapper
    )
    retry_policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.05))
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        retry_policy=retry_policy,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=5)

        assert tasks["A"].attempt_count == 2
        assert http_fixture_server.flaky_call_count("a6-transient") == 2
        assert queue.active_entries() == ()

        completions = runtime.drain_completions()
        completed = next(c for c in completions if c.result and c.result.outcome.name == "COMPLETED")
        final_path = completed.result.completed_download.final_path
        assert final_path.read_bytes() == NORMAL_BODY
        assert list(tmp_path.glob("*.part")) == []
    finally:
        runtime.stop(timeout=5)


def test_real_http_transient_retry_artifact_continuity(http_fixture_server, tmp_path):
    http_fixture_server.configure_flaky("a6-artifact", fail_until=1)

    queue = DownloadQueue(clock=lambda: T0)
    queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {
        "A": DownloadRequest(
            url=f"{http_fixture_server.base_url}/flaky/a6-artifact", destination_dir=tmp_path
        )
    }
    coordinator = DispatchCoordinator(
        acquisition_service=AcquisitionService(), failure_mapper=_retryable_mapper
    )
    retry_policy = RetryPolicy(RetryPolicyConfig(max_attempts=3, base_delay_seconds=0.05))
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        retry_policy=retry_policy,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.COMPLETED, timeout=5)
        completion = next(
            c
            for c in runtime.drain_completions()
            if c.task_id == "A" and c.result and c.result.outcome.name == "COMPLETED"
        )

        artifact = Artifact.from_completed_download(
            completion.result.completed_download.final_path,
            source_url=completion.result.completed_download.source_url,
        )
        assert artifact.local_path.exists()
        assert artifact.size == len(NORMAL_BODY)
        assert artifact.sha256
    finally:
        runtime.stop(timeout=5)


def test_real_http_retry_exhaustion_e2e(http_fixture_server, tmp_path):
    http_fixture_server.configure_flaky("a6-exhaust", fail_until=999)  # always fails

    queue = DownloadQueue(clock=lambda: T0)
    queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {
        "A": DownloadRequest(
            url=f"{http_fixture_server.base_url}/flaky/a6-exhaust", destination_dir=tmp_path
        )
    }
    coordinator = DispatchCoordinator(
        acquisition_service=AcquisitionService(), failure_mapper=_retryable_mapper
    )
    retry_policy = RetryPolicy(RetryPolicyConfig(max_attempts=2, base_delay_seconds=0.02))
    runtime = ConcurrentDownloadRuntime(
        queue=queue,
        tasks=tasks,
        requests=requests,
        coordinator=coordinator,
        config=SchedulerConfig(max_active_transfers=1),
        retry_policy=retry_policy,
    )
    runtime.start()
    try:
        assert _wait_until(lambda: tasks["A"].state == DownloadTaskState.FAILED, timeout=5)

        assert http_fixture_server.flaky_call_count("a6-exhaust") == 2  # exactly max_attempts, never a 3rd
        assert tasks["A"].attempt_count == 2
        assert queue.active_entries() == ()
        assert list(tmp_path.glob("*.part")) == []
    finally:
        runtime.stop(timeout=5)
