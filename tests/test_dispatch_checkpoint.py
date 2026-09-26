"""Prompt A8: DispatchCoordinator's `checkpoint` hook (pre-network + terminal)."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import AcquisitionError, DownloadRequest
from rychlik.core.dispatch_coordinator import (
    DispatchCheckpointError,
    DispatchCoordinator,
    DispatchOutcome,
)
from rychlik.core.download_queue import DownloadQueue
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.scheduler_policy import DispatchCandidate

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _StubAcquisition:
    def __init__(self, outcome="complete"):
        self.outcome = outcome
        self.calls = 0

    def acquire(self, request, *, progress_callback=None, cancel_event=None):
        self.calls += 1
        if self.outcome == "complete":
            from rychlik.acquisition.contracts import CompletedDownload

            return CompletedDownload(final_path=Path("/tmp/x"), display_name="x", source_url=request.url, size=1)
        if self.outcome == "retry":
            raise AcquisitionError("transient")
        raise AcquisitionError("boom")


def _setup(outcome="complete", failure_mapper=None):
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url="http://x/y", destination_dir=Path("/tmp"))}
    kwargs = {}
    if failure_mapper is not None:
        kwargs["failure_mapper"] = failure_mapper
    coordinator = DispatchCoordinator(acquisition_service=_StubAcquisition(outcome), **kwargs)
    return queue, tasks, requests, coordinator, entry


def test_checkpoint_called_at_pre_network_before_network(monkeypatch):
    queue, tasks, requests, coordinator, entry = _setup("complete")
    calls = []

    def checkpoint():
        calls.append(tasks["A"].state)

    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")
    coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0, checkpoint=checkpoint)
    assert calls[0] == DownloadTaskState.TRANSFERRING


def test_checkpoint_called_for_completed():
    queue, tasks, requests, coordinator, entry = _setup("complete")
    calls = []
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")
    coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0,
        checkpoint=lambda: calls.append(tasks["A"].state),
    )
    assert calls == [DownloadTaskState.TRANSFERRING, DownloadTaskState.COMPLETED]


def test_checkpoint_called_for_retry_wait():
    def retryable_mapper(exc):
        return DownloadTaskFailure(code="X", message=str(exc), retryable=True)

    queue, tasks, requests, coordinator, entry = _setup("retry", failure_mapper=retryable_mapper)
    calls = []
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")
    result = coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0,
        checkpoint=lambda: calls.append(tasks["A"].state),
    )
    assert result.outcome == DispatchOutcome.RETRY_WAIT
    assert calls == [DownloadTaskState.TRANSFERRING, DownloadTaskState.RETRY_WAIT]


def test_checkpoint_called_for_failed():
    queue, tasks, requests, coordinator, entry = _setup("fail")
    calls = []
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")
    coordinator.dispatch(
        candidate, queue=queue, tasks=tasks, requests=requests, now=T0,
        checkpoint=lambda: calls.append(tasks["A"].state),
    )
    assert calls == [DownloadTaskState.TRANSFERRING, DownloadTaskState.FAILED]


def test_pre_network_checkpoint_failure_prevents_network_call():
    queue, tasks, requests, coordinator, entry = _setup("complete")
    acquisition = coordinator._acquisition_service

    def boom_checkpoint():
        raise RuntimeError("disk full")

    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")
    with pytest.raises(DispatchCheckpointError):
        coordinator.dispatch(
            candidate, queue=queue, tasks=tasks, requests=requests, now=T0, checkpoint=boom_checkpoint
        )
    assert acquisition.calls == 0


def test_no_checkpoint_is_exactly_prior_behavior():
    queue, tasks, requests, coordinator, entry = _setup("complete")
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="A")
    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result.outcome == DispatchOutcome.COMPLETED
