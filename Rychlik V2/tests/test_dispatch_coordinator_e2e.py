"""Real, non-mock end-to-end tests for DispatchCoordinator (Prompt A4 §65-68).

Uses the real AcquisitionService/DirectHttpAcquisition against the
deterministic local HTTP fixture server -- no fake acquisition here.
"""

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.artifact import Artifact
from rychlik.core.dispatch_coordinator import DispatchCoordinator, DispatchOutcome
from rychlik.core.download_queue import DownloadQueue
from rychlik.core.download_task import DownloadTaskState, create_task
from rychlik.core.scheduler_policy import DispatchCandidate
from http_fixture_server import NORMAL_BODY

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _setup(url: str, tmp_path: Path):
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("task-1")
    tasks = {"task-1": create_task("task-1", now=T0).mark_ready(now=T0)}
    requests = {"task-1": DownloadRequest(url=url, destination_dir=tmp_path)}
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")
    return queue, entry, tasks, requests, candidate


def test_real_http_success_e2e(http_fixture_server, tmp_path):
    queue, entry, tasks, requests, candidate = _setup(
        f"{http_fixture_server.base_url}/normal.mp4", tmp_path
    )
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.COMPLETED
    assert result.completed_download is not None
    assert result.completed_download.final_path.read_bytes() == NORMAL_BODY
    assert tasks["task-1"].state == DownloadTaskState.COMPLETED
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


def test_real_http_success_e2e_artifact_continuity(http_fixture_server, tmp_path):
    """Proves A4 did not break the existing CompletedDownload -> Artifact path
    (Prompt 04.5), without moving Artifact ownership into the coordinator."""
    queue, entry, tasks, requests, candidate = _setup(
        f"{http_fixture_server.base_url}/normal.mp4", tmp_path
    )
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result.outcome == DispatchOutcome.COMPLETED

    artifact = Artifact.from_completed_download(
        result.completed_download.final_path, source_url=result.completed_download.source_url
    )
    assert artifact.local_path.exists()
    assert artifact.size == len(NORMAL_BODY)
    assert artifact.sha256


def test_real_http_404_failure_e2e(http_fixture_server, tmp_path):
    queue, entry, tasks, requests, candidate = _setup(
        f"{http_fixture_server.base_url}/notfound", tmp_path
    )
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.FAILED
    assert result.failure is not None
    assert result.failure.retryable is False  # conservative default mapper
    assert tasks["task-1"].state == DownloadTaskState.FAILED
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"
    assert list(tmp_path.iterdir()) == []  # no partial file left behind


def test_real_http_500_failure_e2e(http_fixture_server, tmp_path):
    queue, entry, tasks, requests, candidate = _setup(
        f"{http_fixture_server.base_url}/servererror", tmp_path
    )
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.FAILED
    assert tasks["task-1"].state == DownloadTaskState.FAILED
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


def test_real_cancellation_e2e(http_fixture_server, tmp_path):
    """Uses the existing (already-supported) cancel_event contract from
    Prompt 04.5's DirectHttpAcquisition against the /slow fixture route --
    no worker threads introduced in the coordinator itself; only this test
    uses a background thread to flip the cancel signal mid-transfer."""
    queue, entry, tasks, requests, candidate = _setup(
        f"{http_fixture_server.base_url}/slow", tmp_path
    )
    coordinator = DispatchCoordinator(acquisition_service=AcquisitionService())
    cancel_event = threading.Event()

    def _cancel_soon():
        time.sleep(0.1)
        cancel_event.set()

    threading.Thread(target=_cancel_soon).start()

    result = coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0, cancel_event=cancel_event
    )

    assert result.outcome == DispatchOutcome.CANCELLED
    assert tasks["task-1"].state == DownloadTaskState.CANCELLED
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"
    assert list(tmp_path.glob("*.part")) == []
