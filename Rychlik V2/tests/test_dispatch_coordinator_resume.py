"""Prompt A9: DispatchCoordinator's RESUME/PAUSED handling and its bridge
to durable partial-transfer state via load_partial/save_partial/clear_partial."""

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import CompletedDownload, DownloadPaused, DownloadRequest
from rychlik.core.dispatch_coordinator import DispatchCoordinator, DispatchOutcome
from rychlik.core.download_queue import DownloadQueue, QueueEntryState
from rychlik.core.download_task import DownloadTaskState, create_task
from rychlik.core.partial_transfer import PartialTransferState, ValidatorKind
from rychlik.core.scheduler_policy import DispatchCandidate, DispatchKind

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _PausingAcquisition:
    def acquire(self, request, *, progress_callback=None, cancel_event=None, pause_event=None, resume=None):
        raise DownloadPaused("paused")


class _CompletingAcquisition:
    def __init__(self):
        self.seen_resume = None

    def acquire(self, request, *, progress_callback=None, cancel_event=None, pause_event=None, resume=None):
        self.seen_resume = resume
        return CompletedDownload(final_path=Path("/tmp/x"), display_name="x", source_url=request.url, size=1)


def _setup_paused_task(tmp_path):
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).pause_transfer(now=T0)
    tasks = {"A": task}
    requests = {"A": DownloadRequest(url="http://x/y", destination_dir=tmp_path)}
    return queue, entry, tasks, requests


def test_resume_kind_requires_paused_not_ready(tmp_path):
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}  # READY, not PAUSED
    requests = {"A": DownloadRequest(url="http://x/y", destination_dir=tmp_path)}
    coordinator = DispatchCoordinator(acquisition_service=_CompletingAcquisition())
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A", kind=DispatchKind.RESUME)

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result.outcome == DispatchOutcome.STALE
    assert "PAUSED" in result.detail


def test_resume_kind_transitions_paused_to_transferring_then_completed(tmp_path):
    queue, entry, tasks, requests = _setup_paused_task(tmp_path)
    coordinator = DispatchCoordinator(acquisition_service=_CompletingAcquisition())
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A", kind=DispatchKind.RESUME)

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result.outcome == DispatchOutcome.COMPLETED
    assert tasks["A"].attempt_count == 1  # unchanged by manual resume (§26)


def test_paused_outcome_keeps_queue_entry_queued(tmp_path):
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url="http://x/y", destination_dir=tmp_path)}
    coordinator = DispatchCoordinator(acquisition_service=_PausingAcquisition())
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0, pause_event=threading.Event())
    assert result.outcome == DispatchOutcome.PAUSED
    assert tasks["A"].state == DownloadTaskState.PAUSED
    assert queue.get(entry.queue_entry_id).state == QueueEntryState.QUEUED  # never removed


def test_load_partial_invalid_local_state_never_reaches_acquisition_as_resumable(tmp_path):
    queue, entry, tasks, requests = _setup_paused_task(tmp_path)
    acquisition = _CompletingAcquisition()
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A", kind=DispatchKind.RESUME)

    # Persisted partial exists but points at a file that was never actually
    # written -- local validation must reject it and pass initial_partial=None.
    ghost_partial = PartialTransferState(
        queue_entry_id=entry.queue_entry_id, task_id="A", attempt_count_snapshot=1,
        temp_path=tmp_path / "y.part", final_path=tmp_path / "y",
        durable_bytes=100, expected_total_bytes=None,
        validator_kind=ValidatorKind.STRONG_ETAG, validator_value='"x"',
        prefix_sha256="a" * 64, created_at=T0, updated_at=T0,
    )
    coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0,
        load_partial=lambda qeid: ghost_partial, save_partial=lambda p: None, clear_partial=lambda qeid: None,
    )
    assert acquisition.seen_resume is not None
    assert acquisition.seen_resume.initial_partial is None  # rejected by local validation


def test_load_partial_valid_local_state_reaches_acquisition(tmp_path):
    queue, entry, tasks, requests = _setup_paused_task(tmp_path)
    acquisition = _CompletingAcquisition()
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A", kind=DispatchKind.RESUME)

    body_prefix = b"hello"
    (tmp_path / "y.part").write_bytes(body_prefix)
    valid_partial = PartialTransferState(
        queue_entry_id=entry.queue_entry_id, task_id="A", attempt_count_snapshot=1,
        temp_path=tmp_path / "y.part", final_path=tmp_path / "y",
        durable_bytes=len(body_prefix), expected_total_bytes=None,
        validator_kind=ValidatorKind.STRONG_ETAG, validator_value='"x"',
        prefix_sha256=hashlib.sha256(body_prefix).hexdigest(), created_at=T0, updated_at=T0,
    )
    coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0,
        load_partial=lambda qeid: valid_partial, save_partial=lambda p: None, clear_partial=lambda qeid: None,
    )
    assert acquisition.seen_resume.initial_partial == valid_partial


def test_stale_generation_partial_never_reaches_acquisition(tmp_path):
    queue, entry, tasks, requests = _setup_paused_task(tmp_path)
    acquisition = _CompletingAcquisition()
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A", kind=DispatchKind.RESUME)

    body_prefix = b"hello"
    (tmp_path / "y.part").write_bytes(body_prefix)
    newer_generation_partial = PartialTransferState(
        queue_entry_id=entry.queue_entry_id, task_id="A", attempt_count_snapshot=999,  # impossible future generation
        temp_path=tmp_path / "y.part", final_path=tmp_path / "y",
        durable_bytes=len(body_prefix), expected_total_bytes=None,
        validator_kind=ValidatorKind.STRONG_ETAG, validator_value='"x"',
        prefix_sha256=hashlib.sha256(body_prefix).hexdigest(), created_at=T0, updated_at=T0,
    )
    coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0,
        load_partial=lambda qeid: newer_generation_partial, save_partial=lambda p: None, clear_partial=lambda qeid: None,
    )
    assert acquisition.seen_resume.initial_partial is None
