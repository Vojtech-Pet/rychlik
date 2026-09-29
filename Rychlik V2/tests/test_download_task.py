from datetime import datetime, timedelta, timezone

import pytest

from rychlik.core.download_task import (
    DownloadTask,
    DownloadTaskFailure,
    DownloadTaskState,
    InvalidRetryFailureError,
    InvalidTaskOperationError,
    InvalidTaskTransitionError,
    create_task,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)
T4 = T0 + timedelta(minutes=4)


def _retryable_failure(code="NETWORK_ERROR"):
    return DownloadTaskFailure(code=code, message="connection reset", retryable=True)


def _fatal_failure(code="HTTP_ERROR"):
    return DownloadTaskFailure(code=code, message="404 not found", retryable=False)


# --- basic state tests (§55) -------------------------------------------


def test_new_task_starts_created():
    task = create_task("task-1", now=T0)
    assert task.state == DownloadTaskState.CREATED


def test_created_to_resolving():
    task = create_task("task-1", now=T0)
    updated = task.start_resolving(now=T1)
    assert updated.state == DownloadTaskState.RESOLVING


def test_created_to_ready():
    task = create_task("task-1", now=T0)
    updated = task.mark_ready(now=T1)
    assert updated.state == DownloadTaskState.READY


def test_resolving_to_ready():
    task = create_task("task-1", now=T0).start_resolving(now=T1)
    updated = task.mark_ready(now=T2)
    assert updated.state == DownloadTaskState.READY


def test_ready_to_transferring():
    task = create_task("task-1", now=T0).mark_ready(now=T1)
    updated = task.start_transfer(now=T2)
    assert updated.state == DownloadTaskState.TRANSFERRING


def test_transferring_to_paused_and_back():
    task = create_task("task-1", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    paused = task.pause_transfer(now=T3)
    assert paused.state == DownloadTaskState.PAUSED
    resumed = paused.resume_transfer(now=T4)
    assert resumed.state == DownloadTaskState.TRANSFERRING


def test_transferring_to_verifying_to_post_processing_to_completed():
    task = create_task("task-1", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    task = task.start_verification(now=T3)
    task = task.start_post_processing(now=T3)
    task = task.complete(now=T4)
    assert task.state == DownloadTaskState.COMPLETED


# --- optional pipeline shapes (§56) -------------------------------------


def test_pipeline_direct():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).complete(now=T3)
    assert task.state == DownloadTaskState.COMPLETED


def test_pipeline_verify():
    task = (
        create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_verification(now=T3)
        .complete(now=T4)
    )
    assert task.state == DownloadTaskState.COMPLETED


def test_pipeline_post_process():
    task = (
        create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_post_processing(now=T3)
        .complete(now=T4)
    )
    assert task.state == DownloadTaskState.COMPLETED


def test_pipeline_full():
    task = (
        create_task("t", now=T0)
        .start_resolving(now=T1)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_verification(now=T3)
        .start_post_processing(now=T3)
        .complete(now=T4)
    )
    assert task.state == DownloadTaskState.COMPLETED


# --- pause test (§57) ---------------------------------------------------


def test_pause_resume_does_not_increment_attempt_or_change_started_at():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    assert task.attempt_count == 1
    assert task.started_at == T2

    task = task.pause_transfer(now=T3).resume_transfer(now=T4)
    assert task.attempt_count == 1
    assert task.started_at == T2


def test_ready_to_paused_is_rejected():
    task = create_task("t", now=T0).mark_ready(now=T1)
    with pytest.raises(InvalidTaskTransitionError):
        task.pause_transfer(now=T2)


# --- retry test (§58/§59) ------------------------------------------------


def test_retry_cycle_increments_attempt_and_preserves_started_at():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    assert task.attempt_count == 1
    assert task.started_at == T2

    failure = _retryable_failure()
    task = task.wait_for_retry(failure, now=T3)
    assert task.state == DownloadTaskState.RETRY_WAIT

    task = task.mark_ready(now=T3).start_transfer(now=T4)
    assert task.attempt_count == 2
    assert task.started_at == T2  # first attempt time, unchanged
    assert task.last_failure == failure


def test_non_retryable_failure_rejected_for_retry_wait():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    with pytest.raises(InvalidRetryFailureError):
        task.wait_for_retry(_fatal_failure(), now=T3)


def test_non_retryable_failure_leads_to_fail():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    failure = _fatal_failure()
    task = task.fail(failure, now=T3)
    assert task.state == DownloadTaskState.FAILED
    assert task.last_failure == failure


def test_mark_retry_ready_only_valid_from_retry_wait():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    with pytest.raises(InvalidTaskTransitionError):
        task.mark_retry_ready(now=T3)  # currently TRANSFERRING, not RETRY_WAIT

    retry_task = task.wait_for_retry(_retryable_failure(), now=T3)
    ready = retry_task.mark_retry_ready(now=T4)
    assert ready.state == DownloadTaskState.READY


# --- failure from each phase (§60) ---------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: create_task("t", now=T0),
        lambda: create_task("t", now=T0).start_resolving(now=T1),
        lambda: create_task("t", now=T0).mark_ready(now=T1),
        lambda: create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2),
        lambda: create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).pause_transfer(now=T3),
        lambda: create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .wait_for_retry(_retryable_failure(), now=T3),
        lambda: create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_verification(now=T3),
        lambda: create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_post_processing(now=T3),
    ],
)
def test_failure_from_each_nonterminal_phase(build):
    task = build()
    failure = _fatal_failure()
    failed = task.fail(failure, now=T4)
    assert failed.state == DownloadTaskState.FAILED
    assert failed.finished_at == T4
    assert failed.last_failure == failure


# --- cancellation (§61) ---------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: create_task("t", now=T0),
        lambda: create_task("t", now=T0).mark_ready(now=T1),
        lambda: create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2),
        lambda: create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).pause_transfer(now=T3),
        lambda: create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .wait_for_retry(_retryable_failure(), now=T3),
        lambda: create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_verification(now=T3),
        lambda: create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_post_processing(now=T3),
    ],
)
def test_cancel_from_each_nonterminal_phase(build):
    task = build()
    cancelled = task.cancel(now=T4)
    assert cancelled.state == DownloadTaskState.CANCELLED
    assert cancelled.finished_at == T4
    # cancel() never fabricates a failure -- last_failure is exactly whatever
    # (if anything) the task already carried from its history (§14).
    assert cancelled.last_failure == task.last_failure


# --- terminal immutability (§62) ------------------------------------------


def _completed_task():
    return create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).complete(now=T3)


def _failed_task():
    return create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).fail(_fatal_failure(), now=T3)


def _cancelled_task():
    return create_task("t", now=T0).cancel(now=T1)


@pytest.mark.parametrize("build", [_completed_task, _failed_task, _cancelled_task])
def test_terminal_task_cannot_restart(build):
    task = build()
    with pytest.raises(InvalidTaskTransitionError):
        task.mark_ready(now=T4)
    with pytest.raises(InvalidTaskTransitionError):
        task.start_transfer(now=T4)
    with pytest.raises(InvalidTaskTransitionError):
        task.resume_transfer(now=T4)
    with pytest.raises(InvalidTaskTransitionError):
        task.start_verification(now=T4)


def test_completed_cannot_be_cancelled_or_refailed():
    task = _completed_task()
    with pytest.raises(InvalidTaskTransitionError):
        task.cancel(now=T4)
    with pytest.raises(InvalidTaskTransitionError):
        task.fail(_fatal_failure(), now=T4)


def test_failed_cannot_be_completed():
    task = _failed_task()
    with pytest.raises(InvalidTaskTransitionError):
        task.complete(now=T4)


def test_cancelled_cannot_be_failed():
    task = _cancelled_task()
    with pytest.raises(InvalidTaskTransitionError):
        task.fail(_fatal_failure(), now=T4)


# --- timestamps (§63) ------------------------------------------------------


def test_timestamps_are_timezone_aware():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).complete(now=T3)
    assert task.created_at.tzinfo is not None
    assert task.updated_at.tzinfo is not None
    assert task.started_at.tzinfo is not None
    assert task.finished_at.tzinfo is not None


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        create_task("t", now=datetime(2026, 1, 1))


def test_created_at_is_immutable_across_transitions():
    task = create_task("t", now=T0)
    updated = task.start_resolving(now=T1)
    assert updated.created_at == T0


def test_real_transition_updates_updated_at():
    task = create_task("t", now=T0)
    updated = task.start_resolving(now=T1)
    assert updated.updated_at == T1


def test_idempotent_noop_leaves_updated_at_unchanged():
    task = create_task("t", now=T0).start_resolving(now=T1)
    result = task.start_resolving(now=T2)  # already RESOLVING
    assert result.updated_at == T1


def test_finished_at_only_set_for_terminal_states():
    task = create_task("t", now=T0).mark_ready(now=T1)
    assert task.finished_at is None
    completed = task.start_transfer(now=T2).complete(now=T3)
    assert completed.finished_at == T3


# --- attempt count (§64) ---------------------------------------------------


def test_attempt_count_starts_at_zero():
    task = create_task("t", now=T0)
    assert task.attempt_count == 0


def test_attempt_count_not_incremented_by_resolving_verifying_post_processing():
    task = (
        create_task("t", now=T0)
        .start_resolving(now=T1)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
    )
    assert task.attempt_count == 1
    task = task.start_verification(now=T3).start_post_processing(now=T3)
    assert task.attempt_count == 1


# --- last_failure semantics (§65) ------------------------------------------


def test_last_failure_retained_through_retry_and_success():
    failure = _retryable_failure()
    task = (
        create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .wait_for_retry(failure, now=T3)
        .mark_ready(now=T3)
        .start_transfer(now=T4)
    )
    assert task.last_failure == failure  # retained through the retry
    completed = task.complete(now=T4)
    assert completed.state == DownloadTaskState.COMPLETED
    assert completed.last_failure == failure  # historical evidence remains (§13/§65)


# --- fail() / cancel() require a real DownloadTaskFailure -----------------


def test_fail_rejects_non_failure_object():
    task = create_task("t", now=T0)
    with pytest.raises(InvalidTaskOperationError):
        task.fail("boom", now=T1)  # type: ignore[arg-type]


def test_wait_for_retry_rejects_non_failure_object():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2)
    with pytest.raises(InvalidTaskOperationError):
        task.wait_for_retry("boom", now=T3)  # type: ignore[arg-type]


# --- FAILED invariant -------------------------------------------------------


def test_failed_state_requires_last_failure_at_construction():
    with pytest.raises(ValueError):
        DownloadTask(
            task_id="t",
            state=DownloadTaskState.FAILED,
            created_at=T0,
            updated_at=T0,
        )


# --- query helpers -----------------------------------------------------------


def test_query_helpers():
    created = create_task("t", now=T0)
    assert not created.is_terminal
    assert not created.is_active_transfer
    assert not created.is_successful
    assert not created.can_retry

    transferring = created.mark_ready(now=T1).start_transfer(now=T2)
    assert transferring.is_active_transfer

    retry = transferring.wait_for_retry(_retryable_failure(), now=T3)
    assert retry.can_retry

    completed = transferring.complete(now=T3)
    assert completed.is_terminal
    assert completed.is_successful


# --- queue/task semantic separation (§66) -----------------------------------


def test_task_ready_and_queue_paused_is_a_valid_independent_combination():
    from rychlik.core.download_queue import DownloadQueue

    task = create_task("task-1", now=T0).mark_ready(now=T1)
    queue = DownloadQueue(clock=lambda: T1)
    entry = queue.enqueue(task.task_id)
    queue.pause(entry.queue_entry_id)

    # Neither domain mutated the other.
    assert task.state == DownloadTaskState.READY
    assert queue.get(entry.queue_entry_id).state.name == "PAUSED"

    # Resuming the queue entry does not change the task.
    queue.resume(entry.queue_entry_id)
    assert task.state == DownloadTaskState.READY

    # Pausing the task's transfer does not touch the queue entry.
    transferring = task.start_transfer(now=T2)
    paused_task = transferring.pause_transfer(now=T3)
    assert queue.get(entry.queue_entry_id).state.name == "QUEUED"
    assert paused_task.state == DownloadTaskState.PAUSED


# --- structural independence (§67) ------------------------------------------


def test_module_has_no_forbidden_imports():
    import sys

    import rychlik.core.download_task as module

    forbidden = ("PySide6", "requests", "yt_dlp")
    module_file = module.__file__
    with open(module_file) as f:
        source = f.read()
    for name in forbidden:
        assert name not in source


# --- complex end-to-end scenario (§68) ---------------------------------------


def test_complex_end_to_end_scenario():
    failure = _retryable_failure()
    task = create_task("t", now=T0)
    task = task.start_resolving(now=T0)
    task = task.mark_ready(now=T1)
    task = task.start_transfer(now=T1)  # attempt 1, started_at = T1
    task = task.wait_for_retry(failure, now=T2)
    task = task.mark_ready(now=T2)
    task = task.start_transfer(now=T3)  # attempt 2
    task = task.pause_transfer(now=T3)
    task = task.resume_transfer(now=T4)
    task = task.start_verification(now=T4)
    task = task.start_post_processing(now=T4)
    task = task.complete(now=T4)

    assert task.attempt_count == 2
    assert task.started_at == T1
    assert task.finished_at == T4
    assert task.last_failure == failure
    assert task.state == DownloadTaskState.COMPLETED
    assert task.is_terminal
    assert task.is_successful


# --- invalid transition matrix (§69) ------------------------------------------


def test_created_to_transferring_rejected():
    task = create_task("t", now=T0)
    with pytest.raises(InvalidTaskTransitionError):
        task.start_transfer(now=T1)


def test_created_to_verifying_rejected():
    task = create_task("t", now=T0)
    with pytest.raises(InvalidTaskTransitionError):
        task.start_verification(now=T1)


def test_resolving_to_transferring_rejected():
    task = create_task("t", now=T0).start_resolving(now=T1)
    with pytest.raises(InvalidTaskTransitionError):
        task.start_transfer(now=T2)


def test_ready_to_completed_rejected():
    task = create_task("t", now=T0).mark_ready(now=T1)
    with pytest.raises(InvalidTaskTransitionError):
        task.complete(now=T2)


def test_paused_to_verifying_rejected():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).pause_transfer(now=T3)
    with pytest.raises(InvalidTaskTransitionError):
        task.start_verification(now=T4)


def test_retry_wait_to_transferring_rejected():
    task = (
        create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .wait_for_retry(_retryable_failure(), now=T3)
    )
    with pytest.raises(InvalidTaskTransitionError):
        task.start_transfer(now=T4)


def test_verifying_to_paused_rejected():
    task = create_task("t", now=T0).mark_ready(now=T1).start_transfer(now=T2).start_verification(now=T3)
    with pytest.raises(InvalidTaskTransitionError):
        task.pause_transfer(now=T4)


def test_post_processing_to_transferring_rejected():
    task = (
        create_task("t", now=T0)
        .mark_ready(now=T1)
        .start_transfer(now=T2)
        .start_post_processing(now=T3)
    )
    with pytest.raises(InvalidTaskTransitionError):
        task.start_transfer(now=T4)
