"""Dispatch coordinator: bridges an A3 DispatchCandidate to a real
acquisition attempt (Prompt A4).

    DispatchCandidate
          v
    DispatchCoordinator
          v
    DownloadTask READY -> TRANSFERRING
          v
    existing AcquisitionService (Prompt 04.5)
          v
    real CompletedDownload / AcquisitionError / DownloadCancelled
          v
    DownloadTask lifecycle update + DownloadQueue cleanup

This is the first module in the queue/task/scheduler family with
intentional runtime side effects. It does NOT decide which candidate
should run -- that remains SchedulerPolicy's job (A3), which stays pure
and is never imported for its side effects here. It executes exactly ONE
already-selected DispatchCandidate, synchronously. No worker pool,
threads, asyncio, automatic retries, or scheduler loop exist here -- see
docs/DISPATCH_COORDINATOR.md for the full boundary and known limitations.

Required contract gap found and closed here (§7/§8 of the prompt): no
existing relationship connects a DownloadTask to the DownloadRequest that
should actually be downloaded -- DownloadTask.task_id is fully opaque
(Prompt A2's own design). Rather than inventing a second competing
request/task model, the smallest addition is an explicit
`requests: Mapping[task_id, DownloadRequest]` snapshot passed into
dispatch(), mirroring the `tasks: Mapping[task_id, DownloadTask]` shape
SchedulerPolicy already accepts. No new persistent registry/database is
introduced.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, ContextManager, Mapping, MutableMapping

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import (
    AcquisitionError,
    CompletedDownload,
    DownloadCancelled,
    DownloadRequest,
)
from rychlik.core.download_queue import DownloadQueue, QueueEntryState, UnknownQueueEntryError
from rychlik.core.download_task import DownloadTask, DownloadTaskFailure, DownloadTaskState
from rychlik.core.progress import ProgressRegistry
from rychlik.core.scheduler_policy import DispatchCandidate


class DispatchCoordinatorError(Exception):
    """Base type for all DispatchCoordinator errors."""


class DispatchConsistencyError(DispatchCoordinatorError):
    """Raised for a structurally inconsistent candidate: unknown
    queue_entry_id, unknown task_id, a queue entry that belongs to a
    different task_id than the candidate claims, or a task with no
    registered DownloadRequest. Never raised for ordinary stale-but-valid
    state (queue paused, task cancelled) -- that produces a STALE outcome
    instead, see DispatchOutcome."""


class DispatchExecutionError(DispatchCoordinatorError):
    """Raised when acquisition fails with an exception outside the
    documented AcquisitionError contract. The DownloadTask is still moved
    to FAILED and its QueueEntry removed before this is raised -- a task
    never remains stuck in TRANSFERRING after an unexpected error. The
    original exception is available via `__cause__` (raised with
    `from exc`); it is never stored inside DownloadTask/DownloadTaskFailure."""


class DispatchOutcome(Enum):
    COMPLETED = "COMPLETED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


@dataclass(frozen=True)
class DispatchExecutionResult:
    """Never leaks infrastructure: no HTTP response, no requests.Session,
    no socket, no traceback. `detail` (STALE only) is a short domain-safe
    string, never a raw exception."""

    queue_entry_id: str
    task_id: str
    outcome: DispatchOutcome
    completed_download: CompletedDownload | None = None
    failure: DownloadTaskFailure | None = None
    detail: str | None = None


FailureMapper = Callable[[AcquisitionError], DownloadTaskFailure]


def default_failure_mapper(exc: AcquisitionError) -> DownloadTaskFailure:
    """Conservative default (§25): retryable is never guessed from message
    text (§28) -- until a typed acquisition-failure taxonomy exists,
    every non-cancellation AcquisitionError is treated as non-retryable."""
    return DownloadTaskFailure(code="ACQUISITION_ERROR", message=str(exc), retryable=False)


class DispatchCoordinator:
    def __init__(
        self,
        *,
        acquisition_service: AcquisitionService,
        failure_mapper: FailureMapper = default_failure_mapper,
    ) -> None:
        self._acquisition_service = acquisition_service
        self._failure_mapper = failure_mapper

    def dispatch(
        self,
        candidate: DispatchCandidate,
        *,
        queue: DownloadQueue,
        tasks: MutableMapping[str, DownloadTask],
        requests: Mapping[str, DownloadRequest],
        now: datetime,
        progress_callback=None,
        cancel_event=None,
        lock: ContextManager | None = None,
        progress_registry: ProgressRegistry | None = None,
    ) -> DispatchExecutionResult:
        """`lock` is a Prompt A5 concurrency extension (§23/§24): an
        optional context manager (e.g. a shared `threading.RLock`) guarding
        only the two domain-mutation phases -- last-moment revalidation +
        start_transfer(), and terminal finalization + queue cleanup. The
        AcquisitionService network call always runs with the lock released,
        so one worker blocked on I/O can never prevent another thread from
        planning or finalizing. `lock=None` (the default) behaves exactly as
        in Prompt A4 -- no synchronization overhead, fully backward
        compatible with every existing synchronous caller/test.

        `progress_registry` is a Prompt A7 extension (§59): when supplied,
        a ProgressReporter bound to (task_id, queue_entry_id,
        attempt_count) is created immediately after start_transfer()
        succeeds and used as the acquisition progress callback (composed
        with any caller-supplied `progress_callback`, if also given).
        `progress_registry=None` (the default) behaves exactly as before --
        no telemetry, fully backward compatible."""

        def _locked() -> ContextManager:
            return lock if lock is not None else contextlib.nullcontext()

        with _locked():
            prepared = self._prepare(candidate, queue=queue, tasks=tasks, requests=requests, now=now)
        if isinstance(prepared, DispatchExecutionResult):
            return prepared  # STALE, decided entirely under the lock
        task, request = prepared

        effective_callback = progress_callback
        if progress_registry is not None:
            reporter = progress_registry.begin_attempt(
                candidate.task_id, candidate.queue_entry_id, task.attempt_count
            )
            if progress_callback is not None:
                user_callback = progress_callback

                def effective_callback(bytes_downloaded, total_bytes=None):
                    reporter(bytes_downloaded, total_bytes)
                    user_callback(bytes_downloaded, total_bytes)
            else:
                effective_callback = reporter

        try:
            completed = self._acquisition_service.acquire(
                request, progress_callback=effective_callback, cancel_event=cancel_event
            )
        except DownloadCancelled:
            with _locked():
                task = task.cancel(now=now)
                tasks[candidate.task_id] = task
                queue.remove(candidate.queue_entry_id, now=now)
            return DispatchExecutionResult(
                candidate.queue_entry_id, candidate.task_id, DispatchOutcome.CANCELLED
            )
        except AcquisitionError as exc:
            failure = self._failure_mapper(exc)
            if failure.retryable:
                with _locked():
                    task = task.wait_for_retry(failure, now=now)
                    tasks[candidate.task_id] = task
                # QueueEntry deliberately stays QUEUED (§16/§18): candidate
                # eligibility already requires Task READY, so a RETRY_WAIT
                # task cannot be re-dispatched, and priority/position survive
                # for whenever mark_retry_ready() is called externally.
                return DispatchExecutionResult(
                    candidate.queue_entry_id,
                    candidate.task_id,
                    DispatchOutcome.RETRY_WAIT,
                    failure=failure,
                )
            with _locked():
                task = task.fail(failure, now=now)
                tasks[candidate.task_id] = task
                queue.remove(candidate.queue_entry_id, now=now)
            return DispatchExecutionResult(
                candidate.queue_entry_id, candidate.task_id, DispatchOutcome.FAILED, failure=failure
            )
        except Exception as exc:
            # Unexpected programming/runtime error (§29/§30): never leave the
            # task TRANSFERRING, never store the raw exception in the domain.
            failure = DownloadTaskFailure(
                code="ACQUISITION_RUNTIME_ERROR", message=str(exc), retryable=False
            )
            with _locked():
                task = task.fail(failure, now=now)
                tasks[candidate.task_id] = task
                queue.remove(candidate.queue_entry_id, now=now)
            raise DispatchExecutionError(
                f"unexpected error during acquisition for task {candidate.task_id!r}"
            ) from exc

        with _locked():
            task = task.complete(now=now)
            tasks[candidate.task_id] = task
            queue.remove(candidate.queue_entry_id, now=now)
        return DispatchExecutionResult(
            candidate.queue_entry_id,
            candidate.task_id,
            DispatchOutcome.COMPLETED,
            completed_download=completed,
        )

    def _prepare(
        self,
        candidate: DispatchCandidate,
        *,
        queue: DownloadQueue,
        tasks: MutableMapping[str, DownloadTask],
        requests: Mapping[str, DownloadRequest],
        now: datetime,
    ) -> DispatchExecutionResult | tuple[DownloadTask, DownloadRequest]:
        """Runs entirely under the caller's lock (if any): resolve + revalidate
        + start_transfer(). Returns either a STALE result or a (task, request)
        pair ready for the (unlocked) acquisition call."""
        try:
            entry = queue.get(candidate.queue_entry_id)
        except UnknownQueueEntryError as exc:
            raise DispatchConsistencyError(
                f"unknown queue_entry_id {candidate.queue_entry_id!r}"
            ) from exc

        if entry.task_id != candidate.task_id:
            raise DispatchConsistencyError(
                f"candidate identity mismatch: queue_entry {candidate.queue_entry_id!r} "
                f"belongs to task {entry.task_id!r}, not {candidate.task_id!r}"
            )

        task = tasks.get(candidate.task_id)
        if task is None:
            raise DispatchConsistencyError(f"unknown task_id {candidate.task_id!r}")

        # Last-moment revalidation (§9/§10): a DispatchPlan is a decision over
        # a past snapshot. Anything that drifted since planning is STALE, not
        # an error -- no mutation, no acquisition call.
        if entry.state != QueueEntryState.QUEUED:
            return self._stale(candidate, f"queue entry is {entry.state.name}, not QUEUED")
        if task.state != DownloadTaskState.READY:
            return self._stale(candidate, f"task is {task.state.name}, not READY")

        request = requests.get(candidate.task_id)
        if request is None:
            raise DispatchConsistencyError(
                f"no DownloadRequest registered for task_id {candidate.task_id!r}"
            )

        try:
            task = task.start_transfer(now=now)
        except Exception:
            # Defensive only: in this synchronous coordinator the READY check
            # above and this call cannot actually diverge without the lock
            # being released in between, but a future caller must not
            # partially execute (§31).
            return self._stale(candidate, "task state changed before start_transfer")
        tasks[candidate.task_id] = task

        return task, request

    @staticmethod
    def _stale(candidate: DispatchCandidate, detail: str) -> DispatchExecutionResult:
        return DispatchExecutionResult(
            candidate.queue_entry_id, candidate.task_id, DispatchOutcome.STALE, detail=detail
        )
