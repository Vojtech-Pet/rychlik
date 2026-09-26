from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rychlik.acquisition.contracts import AcquisitionError, CompletedDownload, DownloadCancelled, DownloadRequest
from rychlik.core.dispatch_coordinator import (
    DispatchConsistencyError,
    DispatchCoordinator,
    DispatchExecutionError,
    DispatchExecutionResult,
    DispatchOutcome,
)
from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import DownloadTask, DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.scheduler_policy import DispatchCandidate, SchedulerConfig, SchedulerPolicy

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=1)


class FakeAcquisitionService:
    """Deterministic stand-in for AcquisitionService. `outcome` controls
    what acquire() does; `observed_task_state` records the DownloadTask
    state at the moment acquire() is invoked (via the `state_probe`
    callback the test wires in), proving start_transfer() happens first."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def acquire(self, request, *, progress_callback=None, cancel_event=None):
        self.calls += 1
        if callable(self.outcome):
            return self.outcome(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _setup(task_state="READY"):
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("task-1")
    task = create_task("task-1", now=T0)
    if task_state == "READY":
        task = task.mark_ready(now=T0)
    tasks = {"task-1": task}
    requests = {"task-1": DownloadRequest(url="http://example.test/f", destination_dir=Path("/tmp/x"))}
    return queue, entry, tasks, requests


def _completed_download():
    return CompletedDownload(
        final_path=Path("/tmp/x/f"), display_name="f", source_url="http://example.test/f", size=100
    )


# --- successful dispatch (§48) ------------------------------------------


def test_successful_dispatch():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.COMPLETED
    assert result.completed_download is not None
    assert tasks["task-1"].state == DownloadTaskState.COMPLETED
    assert tasks["task-1"].attempt_count == 1
    assert acquisition.calls == 1


def test_successful_dispatch_removes_queue_entry():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


# --- state during acquisition (§49) --------------------------------------


def test_task_is_transferring_during_acquisition():
    queue, entry, tasks, requests = _setup()
    observed = {}

    def _probe(request):
        observed["state"] = tasks["task-1"].state
        return _completed_download()

    acquisition = FakeAcquisitionService(_probe)
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert observed["state"] == DownloadTaskState.TRANSFERRING


# --- cancellation (§50) ----------------------------------------------------


def test_cancellation_maps_to_cancelled():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(DownloadCancelled("cancelled"))
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.CANCELLED
    assert tasks["task-1"].state == DownloadTaskState.CANCELLED
    assert tasks["task-1"].last_failure is None
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


# --- non-retryable failure (§51) --------------------------------------------


def test_non_retryable_failure_maps_to_failed():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(AcquisitionError("404 not found"))
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.FAILED
    assert result.failure is not None
    assert tasks["task-1"].state == DownloadTaskState.FAILED
    assert tasks["task-1"].last_failure == result.failure
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


# --- retryable failure (§52) ------------------------------------------------


def test_retryable_failure_maps_to_retry_wait_and_preserves_queue_entry():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(AcquisitionError("connection reset"))

    def mapper(exc):
        return DownloadTaskFailure(code="NETWORK_ERROR", message=str(exc), retryable=True)

    coordinator = DispatchCoordinator(acquisition_service=acquisition, failure_mapper=mapper)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.RETRY_WAIT
    assert tasks["task-1"].state == DownloadTaskState.RETRY_WAIT
    assert tasks["task-1"].last_failure.retryable is True

    live_entry = queue.get(entry.queue_entry_id)
    assert live_entry.state.name == "QUEUED"
    assert live_entry.queue_entry_id == entry.queue_entry_id
    assert live_entry.position == entry.position


# --- retry position preservation (§53) -------------------------------------


def test_retry_preserves_position_among_siblings():
    queue = DownloadQueue(clock=lambda: T0)
    n1 = queue.enqueue("N1")
    n2 = queue.enqueue("N2")
    n3 = queue.enqueue("N3")
    tasks = {
        "N1": create_task("N1", now=T0).mark_ready(now=T0),
        "N2": create_task("N2", now=T0).mark_ready(now=T0),
        "N3": create_task("N3", now=T0).mark_ready(now=T0),
    }
    requests = {
        t: DownloadRequest(url=f"http://example.test/{t}", destination_dir=Path("/tmp/x")) for t in tasks
    }

    def mapper(exc):
        return DownloadTaskFailure(code="NET", message=str(exc), retryable=True)

    coordinator = DispatchCoordinator(
        acquisition_service=FakeAcquisitionService(AcquisitionError("x")), failure_mapper=mapper
    )
    candidate = DispatchCandidate(queue_entry_id=n2.queue_entry_id, task_id="N2")
    coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    order = [e.task_id for e in queue.active_entries()]
    assert order == ["N1", "N2", "N3"]

    tasks["N2"] = tasks["N2"].mark_retry_ready(now=T1)
    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=3))
    assert [c.task_id for c in plan.selected] == ["N1", "N2", "N3"]


# --- terminal removal / retry non-removal (§54) ------------------------------


@pytest.mark.parametrize(
    "outcome_setup",
    [
        ("completed", lambda: _completed_download(), True),
        ("cancelled", lambda: DownloadCancelled("x"), True),
        ("failed", lambda: AcquisitionError("x"), True),
    ],
)
def test_terminal_outcomes_remove_queue_entry(outcome_setup):
    label, factory, should_remove = outcome_setup
    queue, entry, tasks, requests = _setup()
    value = factory()
    acquisition = FakeAcquisitionService(value)
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


def test_retry_wait_does_not_remove_queue_entry():
    queue, entry, tasks, requests = _setup()

    def mapper(exc):
        return DownloadTaskFailure(code="NET", message=str(exc), retryable=True)

    coordinator = DispatchCoordinator(
        acquisition_service=FakeAcquisitionService(AcquisitionError("x")), failure_mapper=mapper
    )
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert queue.get(entry.queue_entry_id).state.name == "QUEUED"


# --- unexpected exception (§55/§56) -----------------------------------------


def test_unexpected_exception_fails_task_and_removes_queue_then_raises():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(RuntimeError("bug"))
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    with pytest.raises(DispatchExecutionError) as exc_info:
        coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert tasks["task-1"].state == DownloadTaskState.FAILED
    assert tasks["task-1"].last_failure.code == "ACQUISITION_RUNTIME_ERROR"
    assert queue.get(entry.queue_entry_id).state.name == "REMOVED"


def test_unexpected_exception_failure_has_no_raw_exception_object():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(RuntimeError("bug with secret traceback"))
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    with pytest.raises(DispatchExecutionError):
        coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    failure = tasks["task-1"].last_failure
    assert isinstance(failure, DownloadTaskFailure)
    assert isinstance(failure.code, str)
    assert isinstance(failure.message, str)
    # structurally: DownloadTaskFailure has no field capable of holding a
    # raw exception/traceback object at all (see Prompt A2)
    assert set(DownloadTaskFailure.__dataclass_fields__) == {"code", "message", "retryable"}


# --- stale: queue paused (§57) -----------------------------------------------


def test_stale_when_queue_entry_paused():
    queue, entry, tasks, requests = _setup()
    queue.pause(entry.queue_entry_id)
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.STALE
    assert acquisition.calls == 0
    assert tasks["task-1"].state == DownloadTaskState.READY
    assert tasks["task-1"].attempt_count == 0


# --- stale: removed entry (§58) -----------------------------------------------


def test_stale_when_queue_entry_removed():
    queue, entry, tasks, requests = _setup()
    queue.remove(entry.queue_entry_id)
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.STALE
    assert acquisition.calls == 0


# --- re-enqueue identity (§59) ------------------------------------------------


def test_old_removed_occurrence_cannot_dispatch_new_reenqueued_one():
    queue, old_entry, tasks, requests = _setup()
    queue.remove(old_entry.queue_entry_id)
    new_entry = queue.enqueue("task-1")
    tasks["task-1"] = tasks["task-1"]  # still READY
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    stale_candidate = DispatchCandidate(queue_entry_id=old_entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(stale_candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.STALE
    assert acquisition.calls == 0
    assert queue.get(new_entry.queue_entry_id).state.name == "QUEUED"


# --- task changed after plan (§47 style) --------------------------------------


def test_stale_when_task_no_longer_ready():
    queue, entry, tasks, requests = _setup()
    tasks["task-1"] = tasks["task-1"].cancel(now=T0)
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert result.outcome == DispatchOutcome.STALE
    assert acquisition.calls == 0
    assert tasks["task-1"].state == DownloadTaskState.CANCELLED  # not resurrected


# --- identity mismatch (§60) --------------------------------------------------


def test_identity_mismatch_raises_consistency_error():
    queue, entry, tasks, requests = _setup()
    queue.enqueue("task-2")
    tasks["task-2"] = create_task("task-2", now=T0).mark_ready(now=T0)
    requests["task-2"] = DownloadRequest(url="http://example.test/g", destination_dir=Path("/tmp/x"))
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    # entry belongs to task-1, but candidate claims task-2
    tampered_candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-2")

    with pytest.raises(DispatchConsistencyError):
        coordinator.dispatch(tampered_candidate, queue=queue, tasks=tasks, requests=requests, now=T0)

    assert acquisition.calls == 0


# --- unknown task (§61) ---------------------------------------------------------


def test_unknown_task_raises_consistency_error():
    queue, entry, tasks, requests = _setup()
    del tasks["task-1"]
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    with pytest.raises(DispatchConsistencyError):
        coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)


# --- unknown queue entry (§62) --------------------------------------------------


def test_unknown_queue_entry_raises_consistency_error():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id="does-not-exist", task_id="task-1")

    with pytest.raises(DispatchConsistencyError):
        coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)


def test_unknown_request_raises_consistency_error():
    queue, entry, tasks, requests = _setup()
    requests.clear()
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    with pytest.raises(DispatchConsistencyError):
        coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)


# --- double dispatch prevention (§63) --------------------------------------------


def test_double_dispatch_of_terminal_occurrence_is_stale():
    queue, entry, tasks, requests = _setup()
    acquisition = FakeAcquisitionService(_completed_download())
    coordinator = DispatchCoordinator(acquisition_service=acquisition)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    first = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)
    assert first.outcome == DispatchOutcome.COMPLETED
    assert acquisition.calls == 1

    second = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T1)
    assert second.outcome == DispatchOutcome.STALE
    assert acquisition.calls == 1  # not called again


# --- retry is a new attempt, not double dispatch (§64) ---------------------------


def test_retry_after_mark_retry_ready_is_new_attempt():
    queue, entry, tasks, requests = _setup()

    def mapper(exc):
        return DownloadTaskFailure(code="NET", message=str(exc), retryable=True)

    failing_acquisition = FakeAcquisitionService(AcquisitionError("x"))
    coordinator = DispatchCoordinator(acquisition_service=failing_acquisition, failure_mapper=mapper)
    candidate = DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id="task-1")

    result1 = coordinator.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result1.outcome == DispatchOutcome.RETRY_WAIT
    assert tasks["task-1"].attempt_count == 1

    tasks["task-1"] = tasks["task-1"].mark_retry_ready(now=T1)
    succeeding_acquisition = FakeAcquisitionService(_completed_download())
    coordinator2 = DispatchCoordinator(acquisition_service=succeeding_acquisition)
    result2 = coordinator2.dispatch(candidate, queue=queue, tasks=tasks, requests=requests, now=T1)

    assert result2.outcome == DispatchOutcome.COMPLETED
    assert tasks["task-1"].attempt_count == 2


# --- scheduler -> coordinator composition (§70) -----------------------------------


def test_scheduler_coordinator_composition():
    queue = DownloadQueue(clock=lambda: T0)
    a = queue.enqueue("A")
    queue.enqueue("B")
    tasks = {
        "A": create_task("A", now=T0).mark_ready(now=T0),
        "B": create_task("B", now=T0).mark_ready(now=T0),
    }
    requests = {
        t: DownloadRequest(url=f"http://example.test/{t}", destination_dir=Path("/tmp/x")) for t in tasks
    }
    config = SchedulerConfig(max_active_transfers=1)

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    assert [c.task_id for c in plan.selected] == ["A"]

    coordinator = DispatchCoordinator(acquisition_service=FakeAcquisitionService(_completed_download()))
    result = coordinator.dispatch(plan.selected[0], queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result.outcome == DispatchOutcome.COMPLETED

    assert tasks["A"].state == DownloadTaskState.COMPLETED
    assert queue.get(a.queue_entry_id).state.name == "REMOVED"
    assert tasks["B"].state == DownloadTaskState.READY

    plan2 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    assert [c.task_id for c in plan2.selected] == ["B"]


# --- retry composition (§71) ------------------------------------------------------


def test_retry_composition():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url="http://example.test/A", destination_dir=Path("/tmp/x"))}
    config = SchedulerConfig(max_active_transfers=1)

    def mapper(exc):
        return DownloadTaskFailure(code="NET", message=str(exc), retryable=True)

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    coordinator = DispatchCoordinator(
        acquisition_service=FakeAcquisitionService(AcquisitionError("x")), failure_mapper=mapper
    )
    coordinator.dispatch(plan.selected[0], queue=queue, tasks=tasks, requests=requests, now=T0)
    assert tasks["A"].state == DownloadTaskState.RETRY_WAIT

    plan2 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    assert plan2.selected == ()

    tasks["A"] = tasks["A"].mark_retry_ready(now=T1)
    plan3 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    assert [c.task_id for c in plan3.selected] == ["A"]


# --- queue pause composition (§72) -------------------------------------------------


def test_queue_pause_composition():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    requests = {"A": DownloadRequest(url="http://example.test/A", destination_dir=Path("/tmp/x"))}
    config = SchedulerConfig(max_active_transfers=1)

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    queue.pause(entry.queue_entry_id)

    coordinator = DispatchCoordinator(acquisition_service=FakeAcquisitionService(_completed_download()))
    result = coordinator.dispatch(plan.selected[0], queue=queue, tasks=tasks, requests=requests, now=T0)
    assert result.outcome == DispatchOutcome.STALE
    assert tasks["A"].state == DownloadTaskState.READY

    queue.resume(entry.queue_entry_id)
    plan2 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=config)
    assert [c.task_id for c in plan2.selected] == ["A"]


# --- structural import test (§73) ---------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.dispatch_coordinator as module

    forbidden = {"PySide6", "httpx", "yt_dlp", "asyncio"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    top_level = {name.split(".")[0] for name in imported_modules}
    assert top_level & forbidden == set()
    assert not any(m.startswith("rychlik.share") for m in imported_modules)
