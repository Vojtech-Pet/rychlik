"""Restart recovery / normalization service (Prompt A8).

Answers exactly one question, deterministically:

    Given whatever a previous, possibly-crashed process durably
    checkpointed, what is the SAFE canonical in-memory state to resume
    from, and what actually happened to get there?

It never calls AcquisitionService or DispatchCoordinator.dispatch()
(§89) -- it only ever produces READY/CREATED/RETRY_WAIT/terminal tasks.
Actual downloading begins only once normal A3/A5 scheduling starts.

Conservative by design (per the user's own A8 framing): there is no safe
partial-transfer resume yet, so any transient in-flight state
(TRANSFERRING, task-level PAUSED, VERIFYING, POST_PROCESSING) is
recovered to READY -- a brand new attempt, never a claimed continuation
of a dead HTTP connection. RESOLVING has no resumable resolver contract
either, so it recovers to CREATED.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from rychlik.core.download_queue import QueueEntryState
from rychlik.core.download_task import DownloadTask, DownloadTaskState
from rychlik.core.retry_policy import RetryPolicy
from rychlik.core.state_store import (
    PersistedRetrySchedule,
    PersistentDownloadState,
    PersistentStateCorruptionError,
)


class RecoveryActionReason(Enum):
    INTERRUPTED_RESOLUTION = "INTERRUPTED_RESOLUTION"
    INTERRUPTED_TRANSFER = "INTERRUPTED_TRANSFER"
    INTERRUPTED_PAUSE = "INTERRUPTED_PAUSE"
    INTERRUPTED_VERIFICATION = "INTERRUPTED_VERIFICATION"
    INTERRUPTED_POST_PROCESSING = "INTERRUPTED_POST_PROCESSING"
    RETRY_RESTORED = "RETRY_RESTORED"
    RETRY_ALREADY_DUE = "RETRY_ALREADY_DUE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    TERMINAL_QUEUE_RECONCILED = "TERMINAL_QUEUE_RECONCILED"
    STALE_RETRY_DISCARDED = "STALE_RETRY_DISCARDED"


_TRANSIENT_TO_READY = frozenset(
    {DownloadTaskState.TRANSFERRING, DownloadTaskState.PAUSED, DownloadTaskState.VERIFYING, DownloadTaskState.POST_PROCESSING}
)
_TRANSIENT_REASON = {
    DownloadTaskState.TRANSFERRING: RecoveryActionReason.INTERRUPTED_TRANSFER,
    DownloadTaskState.PAUSED: RecoveryActionReason.INTERRUPTED_PAUSE,
    DownloadTaskState.VERIFYING: RecoveryActionReason.INTERRUPTED_VERIFICATION,
    DownloadTaskState.POST_PROCESSING: RecoveryActionReason.INTERRUPTED_POST_PROCESSING,
}


@dataclass(frozen=True)
class RecoveryAction:
    task_id: str
    queue_entry_id: str | None
    previous_state: str
    recovered_state: str
    reason: RecoveryActionReason


@dataclass(frozen=True)
class RecoveryReport:
    previous_shutdown_clean: bool
    actions: tuple[RecoveryAction, ...]
    restored_task_count: int
    restored_queue_entry_count: int
    restored_retry_count: int


@dataclass(frozen=True)
class RetrySeed:
    """Enough for a caller to seed ConcurrentDownloadRuntime's in-memory
    retry schedule with a freshly-reconstructed monotonic deadline (§18)."""

    queue_entry_id: str
    task_id: str
    due_monotonic: float
    attempt_count_snapshot: int


@dataclass(frozen=True)
class RecoveryResult:
    state: PersistentDownloadState
    retry_seeds: tuple[RetrySeed, ...]
    report: RecoveryReport


def _live_queue_entry_id(queue_entries: dict, task_id: str) -> str | None:
    for qe_id, entry in queue_entries.items():
        if entry.task_id == task_id and entry.state != QueueEntryState.REMOVED:
            return qe_id
    return None


class RestartRecovery:
    def __init__(self, *, retry_policy: RetryPolicy | None = None) -> None:
        self._retry_policy = retry_policy or RetryPolicy()

    def recover(
        self,
        persisted: PersistentDownloadState,
        *,
        now: datetime,
        monotonic_now: float,
        previous_shutdown_clean: bool,
    ) -> RecoveryResult:
        actions: list[RecoveryAction] = []
        tasks = dict(persisted.tasks)
        queue_entries = dict(persisted.queue_entries)
        retry_schedules = dict(persisted.retry_schedules)
        retry_seeds: list[RetrySeed] = []

        self._validate_retry_identity(queue_entries, retry_schedules)

        for task_id, task in list(tasks.items()):
            if task.state in _TRANSIENT_TO_READY:
                recovered = replace(task, state=DownloadTaskState.READY, updated_at=now, finished_at=None)
                tasks[task_id] = recovered
                actions.append(
                    RecoveryAction(
                        task_id,
                        _live_queue_entry_id(queue_entries, task_id),
                        task.state.name,
                        recovered.state.name,
                        _TRANSIENT_REASON[task.state],
                    )
                )
            elif task.state == DownloadTaskState.RESOLVING:
                recovered = replace(task, state=DownloadTaskState.CREATED, updated_at=now, finished_at=None)
                tasks[task_id] = recovered
                actions.append(
                    RecoveryAction(
                        task_id,
                        _live_queue_entry_id(queue_entries, task_id),
                        task.state.name,
                        recovered.state.name,
                        RecoveryActionReason.INTERRUPTED_RESOLUTION,
                    )
                )

        for task_id, task in list(tasks.items()):
            if task.state != DownloadTaskState.RETRY_WAIT:
                continue
            qe_id = _live_queue_entry_id(queue_entries, task_id)
            self._recover_retry_wait_task(
                task_id=task_id,
                task=task,
                qe_id=qe_id,
                tasks=tasks,
                queue_entries=queue_entries,
                retry_schedules=retry_schedules,
                retry_seeds=retry_seeds,
                actions=actions,
                now=now,
                monotonic_now=monotonic_now,
            )

        for qe_id, schedule in list(retry_schedules.items()):
            task = tasks.get(schedule.task_id)
            qe = queue_entries.get(qe_id)
            stale = (
                task is None
                or task.state != DownloadTaskState.RETRY_WAIT
                or qe is None
                or qe.state == QueueEntryState.REMOVED
                or qe.task_id != schedule.task_id
                or task.attempt_count != schedule.attempt_count_snapshot
            )
            if stale:
                del retry_schedules[qe_id]
                retry_seeds[:] = [seed for seed in retry_seeds if seed.queue_entry_id != qe_id]
                actions.append(
                    RecoveryAction(
                        schedule.task_id,
                        qe_id,
                        DownloadTaskState.RETRY_WAIT.name,
                        (task.state.name if task else "UNKNOWN"),
                        RecoveryActionReason.STALE_RETRY_DISCARDED,
                    )
                )

        for qe_id, qe in list(queue_entries.items()):
            if qe.state == QueueEntryState.REMOVED:
                continue
            task = tasks.get(qe.task_id)
            if task is None:
                raise PersistentStateCorruptionError(
                    f"queue_entry {qe_id!r} references unknown task {qe.task_id!r}"
                )
            if task.is_terminal:
                queue_entries[qe_id] = replace(qe, state=QueueEntryState.REMOVED, updated_at=now)
                actions.append(
                    RecoveryAction(
                        qe.task_id,
                        qe_id,
                        qe.state.name,
                        QueueEntryState.REMOVED.name,
                        RecoveryActionReason.TERMINAL_QUEUE_RECONCILED,
                    )
                )

        report = RecoveryReport(
            previous_shutdown_clean=previous_shutdown_clean,
            actions=tuple(actions),
            restored_task_count=len(tasks),
            restored_queue_entry_count=len(queue_entries),
            restored_retry_count=len(retry_schedules),
        )
        recovered_state = PersistentDownloadState(
            tasks=tasks,
            requests=dict(persisted.requests),
            queue_entries=queue_entries,
            retry_schedules=retry_schedules,
        )
        return RecoveryResult(state=recovered_state, retry_seeds=tuple(retry_seeds), report=report)

    def _recover_retry_wait_task(
        self,
        *,
        task_id: str,
        task: DownloadTask,
        qe_id: str | None,
        tasks: dict,
        queue_entries: dict,
        retry_schedules: dict,
        retry_seeds: list,
        actions: list,
        now: datetime,
        monotonic_now: float,
    ) -> None:
        if task.last_failure is None or not task.last_failure.retryable:
            raise PersistentStateCorruptionError(
                f"task {task_id!r} is persisted RETRY_WAIT without a retryable last_failure"
            )

        schedule = retry_schedules.get(qe_id) if qe_id is not None else None
        if schedule is not None:
            not_before = schedule.not_before_utc
            delay_seconds = schedule.delay_seconds
            scheduled_at = schedule.scheduled_at_utc
        else:
            decision = self._retry_policy.decide(attempt_count=task.attempt_count, failure=task.last_failure)
            if not decision.should_retry:
                failed = replace(
                    task, state=DownloadTaskState.FAILED, updated_at=now, finished_at=now
                )
                tasks[task_id] = failed
                if qe_id is not None:
                    queue_entries[qe_id] = replace(
                        queue_entries[qe_id], state=QueueEntryState.REMOVED, updated_at=now
                    )
                actions.append(
                    RecoveryAction(
                        task_id, qe_id, task.state.name, failed.state.name, RecoveryActionReason.RETRY_EXHAUSTED
                    )
                )
                return
            delay_seconds = decision.delay_seconds
            scheduled_at = task.updated_at
            not_before = scheduled_at + timedelta(seconds=delay_seconds)

        if not_before <= now:
            ready = task.mark_retry_ready(now=now)
            tasks[task_id] = ready
            if qe_id is not None:
                retry_schedules.pop(qe_id, None)
            actions.append(
                RecoveryAction(
                    task_id, qe_id, task.state.name, ready.state.name, RecoveryActionReason.RETRY_ALREADY_DUE
                )
            )
            return

        remaining = (not_before - now).total_seconds()
        if qe_id is not None:
            retry_seeds.append(
                RetrySeed(
                    queue_entry_id=qe_id,
                    task_id=task_id,
                    due_monotonic=monotonic_now + remaining,
                    attempt_count_snapshot=task.attempt_count,
                )
            )
            retry_schedules[qe_id] = PersistedRetrySchedule(
                queue_entry_id=qe_id,
                task_id=task_id,
                attempt_count_snapshot=task.attempt_count,
                delay_seconds=delay_seconds,
                scheduled_at_utc=scheduled_at,
                not_before_utc=not_before,
            )
        actions.append(
            RecoveryAction(
                task_id, qe_id, task.state.name, task.state.name, RecoveryActionReason.RETRY_RESTORED
            )
        )

    @staticmethod
    def _validate_retry_identity(queue_entries: dict, retry_schedules: dict) -> None:
        for qe_id, schedule in retry_schedules.items():
            qe = queue_entries.get(qe_id)
            if qe is not None and qe.task_id != schedule.task_id:
                raise PersistentStateCorruptionError(
                    f"retry_schedule {qe_id!r} claims task {schedule.task_id!r} but persisted "
                    f"queue_entry {qe_id!r} belongs to task {qe.task_id!r}"
                )
