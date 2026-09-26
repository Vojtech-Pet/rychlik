from datetime import datetime, timedelta, timezone

import pytest

from rychlik.core.download_queue import QueueEntry, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.restart_recovery import RecoveryActionReason, RestartRecovery
from rychlik.core.retry_policy import RetryPolicy, RetryPolicyConfig
from rychlik.core.state_store import PersistedRetrySchedule, PersistentDownloadState, PersistentStateCorruptionError

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(**kwargs) -> QueueEntry:
    defaults = dict(
        queue_entry_id="qe-A",
        task_id="A",
        state=QueueEntryState.QUEUED,
        priority=QueuePriority.NORMAL,
        position=0,
        enqueued_at=T0,
        updated_at=T0,
    )
    defaults.update(kwargs)
    return QueueEntry(**defaults)


def _state(tasks=(), requests=(), queue_entries=(), retry_schedules=(), partial_transfers=()):
    return PersistentDownloadState(
        tasks={t.task_id: t for t in tasks},
        requests=dict(requests),
        queue_entries={e.queue_entry_id: e for e in queue_entries},
        retry_schedules={s.queue_entry_id: s for s in retry_schedules},
        partial_transfers={p.queue_entry_id: p for p in partial_transfers},
    )


def _recover(persisted, *, now=T0, monotonic_now=1000.0, clean=True, policy=None):
    recovery = RestartRecovery(retry_policy=policy)
    return recovery.recover(persisted, now=now, monotonic_now=monotonic_now, previous_shutdown_clean=clean)


# --- transient state normalization ------------------------------------------


def test_resolving_recovers_to_created():
    task = create_task("A", now=T0).start_resolving(now=T0)
    result = _recover(_state(tasks=[task]))
    assert result.state.tasks["A"].state == DownloadTaskState.CREATED
    assert result.state.tasks["A"].finished_at is None
    assert result.report.actions[0].reason == RecoveryActionReason.INTERRUPTED_RESOLUTION


def test_transferring_recovers_to_ready_preserving_attempt_count():
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    assert task.attempt_count == 1
    result = _recover(_state(tasks=[task]))
    recovered = result.state.tasks["A"]
    assert recovered.state == DownloadTaskState.READY
    assert recovered.attempt_count == 1
    assert recovered.finished_at is None
    assert any(a.reason == RecoveryActionReason.INTERRUPTED_TRANSFER for a in result.report.actions)


def test_task_paused_recovers_to_ready_not_resume_transfer():
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .pause_transfer(now=T0)
    )
    result = _recover(_state(tasks=[task]))
    assert result.state.tasks["A"].state == DownloadTaskState.READY
    assert any(a.reason == RecoveryActionReason.INTERRUPTED_PAUSE for a in result.report.actions)


def test_verifying_recovers_to_ready():
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).start_verification(now=T0)
    result = _recover(_state(tasks=[task]))
    assert result.state.tasks["A"].state == DownloadTaskState.READY


def test_post_processing_recovers_to_ready():
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .start_post_processing(now=T0)
    )
    result = _recover(_state(tasks=[task]))
    assert result.state.tasks["A"].state == DownloadTaskState.READY


def test_queue_paused_preserved_distinct_from_task_paused():
    task = create_task("A", now=T0).mark_ready(now=T0)
    entry = _entry(state=QueueEntryState.PAUSED, paused_at=T0)
    result = _recover(_state(tasks=[task], queue_entries=[entry]))
    assert result.state.tasks["A"].state == DownloadTaskState.READY
    assert result.state.queue_entries["qe-A"].state == QueueEntryState.PAUSED


def test_paused_with_valid_partial_stays_paused(tmp_path):
    import hashlib

    from rychlik.acquisition.contracts import DownloadRequest
    from rychlik.core.partial_transfer import PartialTransferState, ValidatorKind

    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .pause_transfer(now=T0)
    )
    request = DownloadRequest(url="http://x/y", destination_dir=tmp_path, filename_hint="video")
    prefix = b"hello"
    part_path = request.destination_dir / "video.part"
    part_path.write_bytes(prefix)
    partial = PartialTransferState(
        queue_entry_id="qe-A", task_id="A", attempt_count_snapshot=1,
        temp_path=part_path, final_path=request.destination_dir / "video",
        durable_bytes=len(prefix), expected_total_bytes=None,
        validator_kind=ValidatorKind.STRONG_ETAG, validator_value='"x"',
        prefix_sha256=hashlib.sha256(prefix).hexdigest(), created_at=T0, updated_at=T0,
    )
    result = _recover(
        _state(tasks=[task], queue_entries=[_entry()], requests={"A": request}, partial_transfers=[partial])
    )
    assert result.state.tasks["A"].state == DownloadTaskState.PAUSED
    assert any(a.reason == RecoveryActionReason.PAUSED_PRESERVED for a in result.report.actions)


def test_paused_without_valid_partial_recovers_to_ready():
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .pause_transfer(now=T0)
    )
    result = _recover(_state(tasks=[task], queue_entries=[_entry()]))  # no request, no partial
    assert result.state.tasks["A"].state == DownloadTaskState.READY
    assert any(a.reason == RecoveryActionReason.INTERRUPTED_PAUSE for a in result.report.actions)


def test_terminal_states_preserved():
    for maker in (
        lambda: create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).complete(now=T0),
        lambda: create_task("B", now=T0).fail(DownloadTaskFailure("X", "x", False), now=T0),
        lambda: create_task("C", now=T0).cancel(now=T0),
    ):
        task = maker()
        result = _recover(_state(tasks=[task]))
        assert result.state.tasks[task.task_id].state == task.state


# --- terminal + live queue reconciliation ------------------------------------


def test_terminal_task_with_live_queue_entry_reconciled():
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).complete(now=T0)
    entry = _entry()  # still QUEUED -- crash-window inconsistency
    result = _recover(_state(tasks=[task], queue_entries=[entry]))
    assert result.state.queue_entries["qe-A"].state == QueueEntryState.REMOVED
    assert any(a.reason == RecoveryActionReason.TERMINAL_QUEUE_RECONCILED for a in result.report.actions)


def test_active_task_with_missing_task_reference_is_corruption():
    entry = _entry(task_id="ghost")
    with pytest.raises(PersistentStateCorruptionError):
        _recover(_state(tasks=[], queue_entries=[entry]))


# --- retry restoration --------------------------------------------------------


def test_retry_future_deadline_restored_with_remaining_delay():
    failure = DownloadTaskFailure("NET", "x", True)
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(failure, now=T0)
    )
    schedule = PersistedRetrySchedule(
        queue_entry_id="qe-A",
        task_id="A",
        attempt_count_snapshot=1,
        delay_seconds=10.0,
        scheduled_at_utc=T0,
        not_before_utc=T0 + timedelta(seconds=10),
    )
    now = T0 + timedelta(seconds=9)  # 1 second remaining
    result = _recover(
        _state(tasks=[task], queue_entries=[_entry()], retry_schedules=[schedule]), now=now, monotonic_now=500.0
    )
    assert result.state.tasks["A"].state == DownloadTaskState.RETRY_WAIT
    assert len(result.retry_seeds) == 1
    seed = result.retry_seeds[0]
    assert seed.queue_entry_id == "qe-A"
    assert seed.due_monotonic == pytest.approx(501.0, abs=0.001)


def test_retry_already_due_restores_to_ready():
    failure = DownloadTaskFailure("NET", "x", True)
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(failure, now=T0)
    )
    schedule = PersistedRetrySchedule("qe-A", "A", 1, 10.0, T0, T0 + timedelta(seconds=10))
    now = T0 + timedelta(seconds=20)  # already past due
    result = _recover(_state(tasks=[task], queue_entries=[_entry()], retry_schedules=[schedule]), now=now)
    assert result.state.tasks["A"].state == DownloadTaskState.READY
    assert result.state.retry_schedules == {}
    assert result.retry_seeds == ()
    assert any(a.reason == RecoveryActionReason.RETRY_ALREADY_DUE for a in result.report.actions)


def test_retry_exhausted_restores_to_failed_and_removes_queue_entry():
    failure = DownloadTaskFailure("NET", "x", True)
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(failure, now=T0)
    )
    task = task  # attempt_count == 1
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=1))  # already exhausted at attempt 1
    result = _recover(_state(tasks=[task], queue_entries=[_entry()]), policy=policy)
    assert result.state.tasks["A"].state == DownloadTaskState.FAILED
    assert result.state.tasks["A"].last_failure == failure
    assert result.state.queue_entries["qe-A"].state == QueueEntryState.REMOVED
    assert any(a.reason == RecoveryActionReason.RETRY_EXHAUSTED for a in result.report.actions)


def test_missing_retry_schedule_reconstructed_from_policy():
    failure = DownloadTaskFailure("NET", "x", True)
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(failure, now=T0)
    )
    policy = RetryPolicy(RetryPolicyConfig(max_attempts=5, base_delay_seconds=10.0))
    now = T0 + timedelta(seconds=1)  # well before the reconstructed 10s deadline
    result = _recover(_state(tasks=[task], queue_entries=[_entry()]), now=now, policy=policy, monotonic_now=0.0)
    assert result.state.tasks["A"].state == DownloadTaskState.RETRY_WAIT
    assert len(result.retry_seeds) == 1
    assert result.retry_seeds[0].due_monotonic == pytest.approx(9.0, abs=0.001)


def test_stale_retry_row_discarded_when_task_no_longer_retry_wait():
    task = create_task("A", now=T0).mark_ready(now=T0)  # already promoted elsewhere
    schedule = PersistedRetrySchedule("qe-A", "A", 1, 10.0, T0, T0 + timedelta(seconds=10))
    result = _recover(_state(tasks=[task], queue_entries=[_entry()], retry_schedules=[schedule]))
    assert result.state.retry_schedules == {}
    assert any(a.reason == RecoveryActionReason.STALE_RETRY_DISCARDED for a in result.report.actions)


def test_retry_generation_mismatch_discarded_as_stale():
    failure = DownloadTaskFailure("NET", "x", True)
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(failure, now=T0)
    )
    # schedule references an OLDER attempt generation
    schedule = PersistedRetrySchedule("qe-A", "A", 999, 10.0, T0, T0 + timedelta(seconds=10))
    result = _recover(_state(tasks=[task], queue_entries=[_entry()], retry_schedules=[schedule]))
    # the live retry_wait handling path reconstructs its own schedule since
    # attempt_count_snapshot won't match -- old stale row must not survive
    assert "qe-A" not in [
        qe for qe, s in result.state.retry_schedules.items() if s.attempt_count_snapshot == 999
    ]


def test_retry_identity_mismatch_is_corruption():
    schedule = PersistedRetrySchedule("qe-A", "WRONG_TASK", 1, 10.0, T0, T0 + timedelta(seconds=10))
    entry = _entry(task_id="A")
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).wait_for_retry(
        DownloadTaskFailure("NET", "x", True), now=T0
    )
    with pytest.raises(PersistentStateCorruptionError):
        _recover(_state(tasks=[task], queue_entries=[entry], retry_schedules=[schedule]))


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.restart_recovery as module

    forbidden = {"PySide6", "sqlite3", "requests", "threading"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    top_level = {name.split(".")[0] for name in imported}
    assert top_level & forbidden == set()


def test_retry_wait_without_retryable_failure_is_corruption():
    # Construct a structurally-inconsistent persisted task directly (this
    # cannot happen via normal A2 transitions, only via a corrupted DB row).
    from rychlik.core.download_task import restore_task

    task = restore_task(
        "A", state=DownloadTaskState.RETRY_WAIT, created_at=T0, updated_at=T0, attempt_count=1, last_failure=None
    )
    with pytest.raises(PersistentStateCorruptionError):
        _recover(_state(tasks=[task]))
