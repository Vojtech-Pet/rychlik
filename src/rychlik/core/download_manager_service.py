"""Download Manager Application Service / Backend Facade (Prompt A10).

Answers exactly one question:

    What is the ONE stable API a future GUI/CLI/API caller uses to operate
    downloads, so it never needs to know that A1-A9 exist?

The service composes DownloadQueue (A1), DownloadTask (A2), SchedulerPolicy
(A3), DispatchCoordinator (A4), ConcurrentDownloadRuntime (A5), RetryPolicy
(A6), ProgressRegistry (A7), SqliteDownloadStateStore + RestartRecovery
(A8), and the DispatchKind/pause/resume/partial-resume machinery (A9). It
does not reimplement any of their domain rules -- queue ordering, the
DownloadTask transition graph, scheduler candidate selection, retry delay
computation, Range validation, speed calculation, and SQLite schema logic
all remain exactly where they already live.

No PySide6/Qt dependency exists here. This module is fully headless and
testable; the GUI wiring is a later, separate milestone (see
docs/DOWNLOAD_MANAGER_SERVICE.md, "Why not GUI yet").
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.concurrent_runtime import (
    ConcurrentDownloadRuntime,
    PauseRequestOutcome,
    RetryEventKind,
    RetryRuntimeEvent,
    WorkerCompletion,
)
from rychlik.core.dispatch_coordinator import DispatchCoordinator, DispatchOutcome, FailureMapper, default_failure_mapper
from rychlik.core.download_queue import (
    CrossPriorityReorderError,
    DownloadQueue,
    InvalidQueueOperationError,
    QueueEntryState,
    QueuePriority,
    UnknownQueueEntryError,
)
from rychlik.core.download_task import DownloadTask, DownloadTaskState, create_task
from rychlik.core.download_view import DownloadManagerSnapshot, DownloadViewSnapshot, build_view_snapshot
from rychlik.core.progress import ProgressRegistry
from rychlik.core.restart_recovery import RecoveryReport, RestartRecovery
from rychlik.core.retry_policy import RetryPolicy, RetryPolicyConfig
from rychlik.core.scheduler_policy import SchedulerConfig
from rychlik.core.state_store import SqliteDownloadStateStore, default_state_db_path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- errors -----------------------------------------------------------------


class DownloadManagerError(Exception):
    """Base type for all DownloadManagerService errors."""


class ManagerNotRunningError(DownloadManagerError):
    """Raised by a mutating/query command when the service is not RUNNING."""


class UnknownDownloadError(DownloadManagerError):
    """Reserved for callers that want a raised error instead of a REJECTED
    ManagerCommandResult for an unknown queue_entry_id. Every public command
    in this module returns REJECTED instead of raising this, for a single
    consistent contract (§89) -- exposed for callers/tests that prefer to
    assert via exception."""


class PersistenceCommandError(DownloadManagerError):
    """Raised when a command's durable checkpoint fails. The in-memory
    domain mutation is rolled back where a safe, well-defined inverse
    exists (add_download, hold, release_hold, set_priority, cancel of a
    waiting task); reordering does not attempt exact rollback (see
    docs/DOWNLOAD_MANAGER_SERVICE.md known limitations). The runtime is
    never woken and no success is ever reported after this is raised."""


class ManagerFaultedError(DownloadManagerError):
    """Raised by any command once the service has entered FAULTED --
    reconciliation is not attempted automatically; the database is never
    silently reset."""


# --- lifecycle ----------------------------------------------------------


class ManagerState(Enum):
    """Application service lifecycle -- NOT a DownloadTaskState."""

    NEW = "NEW"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAULTED = "FAULTED"


# --- command results ------------------------------------------------------


class CommandStatus(Enum):
    APPLIED = "APPLIED"
    ACCEPTED = "ACCEPTED"
    NO_OP = "NO_OP"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ManagerCommandResult:
    """Answers only "was this command accepted/applied?" -- never a copy
    of live system state. Callers read `snapshot()` for that (§83)."""

    status: CommandStatus
    task_id: str | None = None
    queue_entry_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AddDownloadResult:
    task_id: str
    queue_entry_id: str


# --- events -----------------------------------------------------------------


class ManagerEventKind(Enum):
    DOWNLOAD_ADDED = "DOWNLOAD_ADDED"
    QUEUE_CHANGED = "QUEUE_CHANGED"
    PROGRESS_CHANGED = "PROGRESS_CHANGED"
    PAUSED = "PAUSED"
    RESUME_REQUESTED = "RESUME_REQUESTED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    RETRY_READY = "RETRY_READY"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ManagerEvent:
    """A small invalidation signal, never a duplicated snapshot (§52).
    Consumers call `snapshot()`/`item_snapshot()` in response."""

    kind: ManagerEventKind
    task_id: str | None = None
    queue_entry_id: str | None = None


# --- configuration ------------------------------------------------------


@dataclass(frozen=True)
class DownloadManagerConfig:
    """Only configuration already owned by an existing subsystem (§8) --
    no duplicated defaults invented here."""

    max_active_transfers: int = 2
    retry_policy_config: RetryPolicyConfig = field(default_factory=RetryPolicyConfig)
    resume_checkpoint_bytes_threshold: int = 8 * 1024 * 1024
    database_path: Path | None = None  # None -> default_state_db_path()
    progress_event_interval_seconds: float = 0.25


_COMPLETION_EVENT_KIND = {
    DispatchOutcome.COMPLETED: ManagerEventKind.COMPLETED,
    DispatchOutcome.FAILED: ManagerEventKind.FAILED,
    DispatchOutcome.CANCELLED: ManagerEventKind.CANCELLED,
    DispatchOutcome.PAUSED: ManagerEventKind.PAUSED,
}
_RETRY_EVENT_KIND = {
    RetryEventKind.SCHEDULED: ManagerEventKind.RETRY_SCHEDULED,
    RetryEventKind.READY: ManagerEventKind.RETRY_READY,
    RetryEventKind.EXHAUSTED: ManagerEventKind.RETRY_EXHAUSTED,
}


class DownloadManagerService:
    """The single application boundary over A1-A9 (§1-§4). Presentation
    code (a future GUI/CLI) never touches DownloadQueue, DownloadTask,
    SchedulerPolicy, DispatchCoordinator, ConcurrentDownloadRuntime,
    RetryPolicy, ProgressRegistry, SqliteDownloadStateStore,
    RestartRecovery, or TransferControl directly again after this."""

    def __init__(
        self,
        *,
        config: DownloadManagerConfig | None = None,
        state_store: SqliteDownloadStateStore | None = None,
        acquisition_service: AcquisitionService | None = None,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        executor_factory=None,
        task_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        failure_mapper: FailureMapper = default_failure_mapper,
    ) -> None:
        """No constructor side effects (§86): opening the database,
        recovering, and starting the runtime/network all happen in
        start(), never here. Every collaborator is overridable for tests
        (§85) -- nothing hard-codes the real user home directory."""
        self._config = config or DownloadManagerConfig()
        self._state_store = state_store or SqliteDownloadStateStore(
            self._config.database_path or default_state_db_path()
        )
        self._acquisition_service = acquisition_service or AcquisitionService()
        self._clock = clock
        self._monotonic = monotonic
        self._executor_factory = executor_factory
        self._task_id_factory = task_id_factory
        self._failure_mapper = failure_mapper

        self._lifecycle_lock = threading.Lock()
        self._state = ManagerState.NEW
        self._fault_reason: str | None = None

        self._queue: DownloadQueue | None = None
        self._tasks: dict[str, DownloadTask] = {}
        self._requests: dict[str, DownloadRequest] = {}
        self._progress_registry: ProgressRegistry | None = None
        self._runtime: ConcurrentDownloadRuntime | None = None
        self._recovery_report: RecoveryReport | None = None

        self._subscribers: dict[int, Callable[[ManagerEvent], None]] = {}
        self._subscribers_lock = threading.Lock()
        self._next_subscriber_token = 1

        self._event_pump_thread: threading.Thread | None = None
        self._event_pump_stop = threading.Event()

    # --- lifecycle --------------------------------------------------------

    @property
    def state(self) -> ManagerState:
        return self._state

    @property
    def last_recovery_report(self) -> RecoveryReport | None:
        """§61: exposes A8's RecoveryReport through a safe accessor so a
        future caller can show e.g. "Recovered 2 interrupted downloads"
        without opening RestartRecovery/state_store internals."""
        return self._recovery_report

    def start(self) -> RecoveryReport | None:
        """Idempotent on RUNNING (§13). Owns the full A8 startup ordering
        (§11): open/validate/migrate schema, RestartRecovery, persist the
        recovered canonical state, rebuild runtime-only A5/A7/A6 state,
        THEN start the A5 runtime -- workers never run before recovery
        completes."""
        with self._lifecycle_lock:
            if self._state == ManagerState.RUNNING:
                return self._recovery_report
            if self._state == ManagerState.FAULTED:
                raise ManagerFaultedError(self._fault_reason or "service is faulted")
            if self._state == ManagerState.STOPPING:
                raise DownloadManagerError("cannot start while stopping")

            try:
                self._state_store.initialize()
                previous_clean = self._state_store.get_previous_shutdown_clean()
                self._state_store.mark_session_dirty()
                persisted = self._state_store.load()

                retry_policy = RetryPolicy(self._config.retry_policy_config)
                recovery = RestartRecovery(retry_policy=retry_policy).recover(
                    persisted,
                    now=self._clock(),
                    monotonic_now=self._monotonic(),
                    previous_shutdown_clean=previous_clean,
                )
                self._state_store.replace_all(recovery.state)

                self._queue = DownloadQueue.restore(
                    list(recovery.state.queue_entries.values()), clock=self._clock
                )
                self._tasks = dict(recovery.state.tasks)
                self._requests = dict(recovery.state.requests)
                self._progress_registry = ProgressRegistry()
                self._recovery_report = recovery.report

                coordinator = DispatchCoordinator(
                    acquisition_service=self._acquisition_service,
                    failure_mapper=self._failure_mapper,
                    resume_checkpoint_bytes_threshold=self._config.resume_checkpoint_bytes_threshold,
                )
                runtime_kwargs = dict(
                    queue=self._queue,
                    tasks=self._tasks,
                    requests=self._requests,
                    coordinator=coordinator,
                    config=SchedulerConfig(max_active_transfers=self._config.max_active_transfers),
                    clock=self._clock,
                    retry_policy=retry_policy,
                    monotonic=self._monotonic,
                    progress_registry=self._progress_registry,
                    state_store=self._state_store,
                    initial_retry_schedule=recovery.retry_seeds,
                    enable_pause_resume=True,
                )
                if self._executor_factory is not None:
                    runtime_kwargs["executor_factory"] = self._executor_factory
                self._runtime = ConcurrentDownloadRuntime(**runtime_kwargs)
                self._runtime.start()

                self._event_pump_stop.clear()
                self._event_pump_thread = threading.Thread(
                    target=self._event_pump_loop, name="rychlik-manager-events", daemon=True
                )
                self._event_pump_thread.start()

                self._state = ManagerState.RUNNING
                return recovery.report
            except Exception as exc:
                self._state = ManagerState.FAULTED
                self._fault_reason = f"start() failed: {exc}"
                raise

    def stop(self, *, timeout: float | None = None) -> None:
        """Idempotent on STOPPED/NEW (§14). Graceful (§12): stop accepting
        new commands' effects on the runtime, let A5 finish in-flight work
        under its own existing semantics, THEN mark clean shutdown -- never
        marked clean before the runtime has actually stopped, and never
        marked clean if the service was FAULTED."""
        with self._lifecycle_lock:
            if self._state in (ManagerState.STOPPED, ManagerState.NEW):
                self._state = ManagerState.STOPPED
                return
            was_faulted = self._state == ManagerState.FAULTED
            self._state = ManagerState.STOPPING

        if self._runtime is not None:
            self._runtime.stop(timeout=timeout)
        self._event_pump_stop.set()
        if self._event_pump_thread is not None:
            self._event_pump_thread.join(timeout=timeout)
            self._event_pump_thread = None

        if not was_faulted:
            try:
                self._state_store.mark_clean_shutdown()
            except Exception:
                pass  # best-effort; do not prevent close()
        self._state_store.close()

        with self._lifecycle_lock:
            self._state = ManagerState.STOPPED

    def __enter__(self) -> "DownloadManagerService":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def _require_running(self) -> None:
        if self._state == ManagerState.FAULTED:
            raise ManagerFaultedError(self._fault_reason or "service is faulted")
        if self._state != ManagerState.RUNNING:
            raise ManagerNotRunningError(f"service is {self._state.name}, not RUNNING")

    # --- add download (§17-§21) -------------------------------------------

    def add_download(
        self, request: DownloadRequest, *, priority: QueuePriority = QueuePriority.NORMAL
    ) -> AddDownloadResult:
        """Durably creates task + request + queue occurrence BEFORE waking
        the runtime (§19) -- a worker can never see this download before
        it is committed. On persistence failure, the in-memory mutation is
        rolled back and PersistenceCommandError is raised; no
        AddDownloadResult is ever returned for an uncommitted download
        (§20)."""
        self._require_running()
        task_id = self._task_id_factory()
        now = self._clock()
        task = create_task(task_id, now=now).mark_ready(now=now)

        with self._runtime.state_lock:
            entry = self._queue.enqueue(task_id, priority, now=now)
            self._tasks[task_id] = task
            self._requests[task_id] = request
            try:
                self._state_store.checkpoint_task_state(task=task, request=request, queue_entry=entry)
            except Exception as exc:
                self._queue.remove(entry.queue_entry_id, now=now)
                del self._tasks[task_id]
                del self._requests[task_id]
                raise PersistenceCommandError(
                    f"failed to durably persist new download for task_id={task_id!r}"
                ) from exc

        self._runtime.notify_state_changed()
        self._emit(ManagerEvent(ManagerEventKind.DOWNLOAD_ADDED, task_id=task_id, queue_entry_id=entry.queue_entry_id))
        return AddDownloadResult(task_id=task_id, queue_entry_id=entry.queue_entry_id)

    # --- queue hold / release (§22-§24) ------------------------------------

    def hold(self, queue_entry_id: str) -> ManagerCommandResult:
        """QueueEntry -> PAUSED (a queue-eligibility hold). Never stops an
        already-active transfer -- see pause_transfer() for that."""
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            if entry.state == QueueEntryState.REMOVED:
                return ManagerCommandResult(CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason="occurrence is REMOVED")
            if entry.state == QueueEntryState.PAUSED:
                return ManagerCommandResult(CommandStatus.NO_OP, task_id=entry.task_id, queue_entry_id=queue_entry_id)
            self._queue.pause(queue_entry_id, now=self._clock())
            self._checkpoint_or_rollback(
                task_id=entry.task_id, queue_entry_id=queue_entry_id,
                rollback=lambda: self._queue.resume(queue_entry_id, now=self._clock()),
            )
        self._runtime.notify_state_changed()
        self._emit(ManagerEvent(ManagerEventKind.QUEUE_CHANGED, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    def release_hold(self, queue_entry_id: str) -> ManagerCommandResult:
        """QueueEntry PAUSED -> QUEUED. A READY task may then become
        schedulable through normal A3/A5 selection."""
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            if entry.state == QueueEntryState.REMOVED:
                return ManagerCommandResult(CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason="occurrence is REMOVED")
            if entry.state == QueueEntryState.QUEUED:
                return ManagerCommandResult(CommandStatus.NO_OP, task_id=entry.task_id, queue_entry_id=queue_entry_id)
            self._queue.resume(queue_entry_id, now=self._clock())
            self._checkpoint_or_rollback(
                task_id=entry.task_id, queue_entry_id=queue_entry_id,
                rollback=lambda: self._queue.pause(queue_entry_id, now=self._clock()),
            )
        self._runtime.notify_state_changed()
        self._emit(ManagerEvent(ManagerEventKind.QUEUE_CHANGED, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    # --- real transfer pause / resume (§25-§29) ----------------------------

    def pause_transfer(self, queue_entry_id: str) -> ManagerCommandResult:
        """Delegates to A9's cooperative pause (never sets task.state
        directly -- A9 confirms the physical transfer actually stopped
        before the task becomes PAUSED). ACCEPTED means the request was
        registered, not that PAUSED has been reached yet (§79/§82) --
        observe events/snapshot() for the final state."""
        self._require_running()
        entry = self._safe_get_entry(queue_entry_id)
        if entry is None:
            return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
        outcome = self._runtime.request_pause(queue_entry_id)
        if outcome == PauseRequestOutcome.REQUESTED:
            return ManagerCommandResult(CommandStatus.ACCEPTED, task_id=entry.task_id, queue_entry_id=queue_entry_id)
        return ManagerCommandResult(
            CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason="not currently transferring"
        )

    def resume_transfer(self, queue_entry_id: str) -> ManagerCommandResult:
        """Registers an A9 resume intent and wakes the runtime -- normal
        A3/A5 priority, capacity, and queue-hold rules still apply
        (§27/§29/§81); this never opens an HTTP connection directly."""
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            task = self._tasks.get(entry.task_id)
            if task is None or task.state != DownloadTaskState.PAUSED:
                return ManagerCommandResult(
                    CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason="task is not PAUSED"
                )
        self._runtime.request_resume(queue_entry_id)
        self._emit(ManagerEvent(ManagerEventKind.RESUME_REQUESTED, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.ACCEPTED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    # --- cancel (§30-§33) ---------------------------------------------------

    def cancel(self, queue_entry_id: str) -> ManagerCommandResult:
        """Waiting tasks (CREATED/RESOLVING/READY/RETRY_WAIT/PAUSED/
        VERIFYING/POST_PROCESSING) cancel synchronously with no network
        involved. An actively TRANSFERRING task is cooperatively cancelled
        through A9's cancel_event -- ACCEPTED means requested, not yet
        CANCELLED (§80). Idempotent on already-CANCELLED (NO_OP);
        COMPLETED/FAILED return an explicit not-cancellable REJECTED."""
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            task = self._tasks.get(entry.task_id)
            if task is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown task")

            if task.state == DownloadTaskState.CANCELLED:
                return ManagerCommandResult(CommandStatus.NO_OP, task_id=task.task_id, queue_entry_id=queue_entry_id)
            if task.state in (DownloadTaskState.COMPLETED, DownloadTaskState.FAILED):
                return ManagerCommandResult(
                    CommandStatus.REJECTED, task_id=task.task_id, queue_entry_id=queue_entry_id,
                    reason=f"task is terminal ({task.state.name})",
                )

            if task.state == DownloadTaskState.TRANSFERRING:
                requested = self._runtime.request_cancel(queue_entry_id)
                if not requested:
                    return ManagerCommandResult(
                        CommandStatus.REJECTED, task_id=task.task_id, queue_entry_id=queue_entry_id,
                        reason="not currently transferring",
                    )
                return ManagerCommandResult(CommandStatus.ACCEPTED, task_id=task.task_id, queue_entry_id=queue_entry_id)

            # Waiting task: cancel synchronously, no network (§31).
            now = self._clock()
            self._runtime.discard_resume_request(queue_entry_id)
            self._runtime.discard_retry_schedule(queue_entry_id)
            cancelled = task.cancel(now=now)
            self._tasks[entry.task_id] = cancelled
            removed_entry = self._queue.remove(queue_entry_id, now=now)
            try:
                self._state_store.checkpoint_task_state(
                    task=cancelled,
                    queue_entry=removed_entry,
                    delete_retry_schedule_id=queue_entry_id,
                    delete_partial_transfer_id=queue_entry_id,
                )
            except Exception as exc:
                raise PersistenceCommandError(
                    f"failed to durably persist cancellation for queue_entry_id={queue_entry_id!r}"
                ) from exc

        self._emit(ManagerEvent(ManagerEventKind.CANCELLED, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    # --- retry now (§34-§37) -------------------------------------------------

    def retry_now(self, queue_entry_id: str) -> ManagerCommandResult:
        """Ends a RETRY_WAIT backoff early: removes the pending A6 schedule
        and calls task.mark_retry_ready() -- never dispatches directly
        (§35). Because A6 already only leaves a task in RETRY_WAIT when its
        attempt budget is not yet exhausted, this can never manufacture an
        attempt beyond RetryPolicyConfig.max_attempts (§37)."""
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            task = self._tasks.get(entry.task_id)
            if task is None or task.state != DownloadTaskState.RETRY_WAIT:
                return ManagerCommandResult(
                    CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason="task is not RETRY_WAIT"
                )

            now = self._clock()
            ready = task.mark_retry_ready(now=now)
            self._tasks[entry.task_id] = ready
            self._runtime.discard_retry_schedule(queue_entry_id)
            try:
                self._state_store.checkpoint_task_state(task=ready, delete_retry_schedule_id=queue_entry_id)
            except Exception as exc:
                raise PersistenceCommandError(
                    f"failed to durably persist retry_now for queue_entry_id={queue_entry_id!r}"
                ) from exc

        self._runtime.notify_state_changed()
        self._emit(ManagerEvent(ManagerEventKind.RETRY_READY, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    # --- priority / reorder (§38-§40) ---------------------------------------

    def set_priority(self, queue_entry_id: str, priority: QueuePriority) -> ManagerCommandResult:
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            if entry.state == QueueEntryState.REMOVED:
                return ManagerCommandResult(CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason="occurrence is REMOVED")
            old_priority = entry.priority
            if old_priority == priority:
                return ManagerCommandResult(CommandStatus.NO_OP, task_id=entry.task_id, queue_entry_id=queue_entry_id)

            self._queue.set_priority(queue_entry_id, priority, now=self._clock())
            try:
                self._checkpoint_priority_band(old_priority)
                self._checkpoint_priority_band(priority)
            except Exception as exc:
                self._queue.set_priority(queue_entry_id, old_priority, now=self._clock())
                raise PersistenceCommandError(
                    f"failed to durably persist priority change for queue_entry_id={queue_entry_id!r}"
                ) from exc
        self._runtime.notify_state_changed()
        self._emit(ManagerEvent(ManagerEventKind.QUEUE_CHANGED, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    def move_before(self, queue_entry_id: str, target_queue_entry_id: str) -> ManagerCommandResult:
        return self._reorder(queue_entry_id, target_queue_entry_id, before=True)

    def move_after(self, queue_entry_id: str, target_queue_entry_id: str) -> ManagerCommandResult:
        return self._reorder(queue_entry_id, target_queue_entry_id, before=False)

    def _reorder(self, queue_entry_id: str, target_queue_entry_id: str, *, before: bool) -> ManagerCommandResult:
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return ManagerCommandResult(CommandStatus.REJECTED, queue_entry_id=queue_entry_id, reason="unknown queue_entry_id")
            try:
                if before:
                    self._queue.move_before(queue_entry_id, target_queue_entry_id, now=self._clock())
                else:
                    self._queue.move_after(queue_entry_id, target_queue_entry_id, now=self._clock())
            except (UnknownQueueEntryError, CrossPriorityReorderError, InvalidQueueOperationError) as exc:
                return ManagerCommandResult(
                    CommandStatus.REJECTED, task_id=entry.task_id, queue_entry_id=queue_entry_id, reason=str(exc)
                )
            try:
                self._checkpoint_priority_band(entry.priority)
            except Exception as exc:
                raise PersistenceCommandError(
                    f"failed to durably persist reorder for queue_entry_id={queue_entry_id!r}"
                ) from exc
        self._runtime.notify_state_changed()
        self._emit(ManagerEvent(ManagerEventKind.QUEUE_CHANGED, task_id=entry.task_id, queue_entry_id=queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, task_id=entry.task_id, queue_entry_id=queue_entry_id)

    # --- snapshots (§41-§46) -------------------------------------------------

    def snapshot(self) -> DownloadManagerSnapshot:
        """Returns A7's existing immutable DownloadManagerSnapshot -- no
        second GUI representation is invented here. Safe to call from any
        thread at any time while RUNNING (§43); uses the exact same
        copy-then-compose locking A7 already established."""
        self._require_running()
        return self._runtime.manager_snapshot()

    def item_snapshot(self, queue_entry_id: str) -> DownloadViewSnapshot | None:
        """A7's flat, GUI-safe DownloadViewSnapshot for one occurrence, or
        None for an unknown queue_entry_id."""
        self._require_running()
        with self._runtime.state_lock:
            entry = self._safe_get_entry(queue_entry_id)
            if entry is None:
                return None
            task = self._tasks.get(entry.task_id)
            request = self._requests.get(entry.task_id)
        progress = self._progress_registry.snapshot(queue_entry_id) if self._progress_registry else None
        return build_view_snapshot(queue_entry=entry, task=task, request=request, progress=progress)

    # --- events (§50-§56, §107-§112) -----------------------------------------

    def subscribe(self, callback: Callable[[ManagerEvent], None]) -> int:
        """Framework-neutral (§51 -- no Qt Signal here; a future adapter
        translates events to one). Returns an opaque token for
        unsubscribe(). Safe to call snapshot()/item_snapshot() from inside
        `callback` (§111/§112) -- callbacks always run OUTSIDE any A5/A7/
        state-store lock (§107)."""
        with self._subscribers_lock:
            token = self._next_subscriber_token
            self._next_subscriber_token += 1
            self._subscribers[token] = callback
        return token

    def unsubscribe(self, token: int) -> None:
        with self._subscribers_lock:
            self._subscribers.pop(token, None)

    def _emit(self, event: ManagerEvent) -> None:
        with self._subscribers_lock:
            callbacks = list(self._subscribers.values())
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                pass  # §108: one broken subscriber must not corrupt the runtime

    def _event_pump_loop(self) -> None:
        """A small dedicated thread (mirrors A5's own controller thread)
        translating low-frequency WorkerCompletion/RetryRuntimeEvent
        results into ManagerEvents, plus one coalesced PROGRESS_CHANGED
        tick per interval while anything is actively transferring (§54/
        §110) -- never one event per A7 progress chunk."""
        while not self._event_pump_stop.is_set():
            for completion in self._runtime.drain_completions():
                self._emit_for_completion(completion)
            for retry_event in self._runtime.drain_retry_events():
                self._emit_for_retry_event(retry_event)
            try:
                snap = self._runtime.manager_snapshot()
                if snap.active_transfer_count > 0:
                    self._emit(ManagerEvent(ManagerEventKind.PROGRESS_CHANGED))
            except Exception:
                pass
            self._event_pump_stop.wait(self._config.progress_event_interval_seconds)

    def _emit_for_completion(self, completion: WorkerCompletion) -> None:
        if completion.result is None:
            return
        kind = _COMPLETION_EVENT_KIND.get(completion.result.outcome)
        if kind is not None:
            self._emit(ManagerEvent(kind, task_id=completion.task_id, queue_entry_id=completion.queue_entry_id))

    def _emit_for_retry_event(self, retry_event: RetryRuntimeEvent) -> None:
        kind = _RETRY_EVENT_KIND.get(retry_event.kind)
        if kind is not None:
            self._emit(ManagerEvent(kind, task_id=retry_event.task_id, queue_entry_id=retry_event.queue_entry_id))

    # --- internals ------------------------------------------------------

    def _safe_get_entry(self, queue_entry_id: str):
        try:
            return self._queue.get(queue_entry_id)
        except UnknownQueueEntryError:
            return None

    def _checkpoint_or_rollback(self, *, task_id: str, queue_entry_id: str, rollback: Callable[[], None]) -> None:
        try:
            self._runtime.checkpoint_task(task_id, queue_entry_id=queue_entry_id)
        except Exception as exc:
            rollback()
            raise PersistenceCommandError(
                f"failed to durably persist change for queue_entry_id={queue_entry_id!r}"
            ) from exc

    def _checkpoint_priority_band(self, priority: QueuePriority) -> None:
        """A move/priority change can renumber every sibling's `position`
        within a band (§38-§40's delegation to A1) -- every entry in the
        affected band is re-checkpointed so a restart's contiguous-
        position invariant (DownloadQueue.restore()) never sees a stale
        row. Only queue_entries rows are touched -- retry_schedules/
        partial_transfers are never read or rewritten by this path."""
        for e in self._queue.active_entries():
            if e.priority == priority:
                task = self._tasks.get(e.task_id)
                if task is not None:
                    self._state_store.checkpoint_task_state(task=task, queue_entry=e)
