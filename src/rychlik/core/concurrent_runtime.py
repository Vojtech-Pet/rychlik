"""Concurrent worker runtime / scheduler loop (Prompt A5).

The first component in the queue/task/scheduler/coordinator family that
executes MULTIPLE dispatches concurrently, with bounded parallelism and
automatic refill of freed slots:

    Queue + Tasks + capacity
            v
    SchedulerPolicy.plan(reserved_queue_entry_ids=...)   -- A3, extended, still pure
            v
    ConcurrentDownloadRuntime (this module): reserve + submit workers
            v
    N worker threads, each: DispatchCoordinator.dispatch(..., lock=state_lock)
            v
    real AcquisitionService (unmodified since Prompt 04.5)

No new QueueEntryState or DownloadTaskState was introduced anywhere for
this. The race between "SchedulerPolicy selected candidate X" and "worker
actually transitions X's task to TRANSFERRING" is closed by a
runtime-only, in-memory, ephemeral reservation set keyed by
queue_entry_id -- never by task_id, preserving the A1/A4 re-enqueue
identity guarantee. See docs/CONCURRENT_DOWNLOAD_RUNTIME.md for the full
architecture, locking boundary, and known limitations.

Prompt A6 extends this same module with deterministic, bounded automatic
retry/backoff. `retry_policy=None` (the default) means exactly the A5
behavior: a RETRY_WAIT task just sits until something external calls
`mark_retry_ready()` + `notify_state_changed()` -- fully backward
compatible, verified by every original A5 test remaining unmodified. See
docs/RETRY_POLICY_BACKOFF.md for the retry runtime architecture.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, MutableMapping

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.dispatch_coordinator import (
    DispatchCoordinator,
    DispatchCoordinatorError,
    DispatchExecutionResult,
    DispatchOutcome,
)
from rychlik.core.download_queue import DownloadQueue, QueueEntryState, UnknownQueueEntryError
from rychlik.core.download_task import DownloadTask, DownloadTaskState
from rychlik.core.retry_policy import RetryPolicy
from rychlik.core.scheduler_policy import DispatchCandidate, SchedulerConfig, SchedulerPolicy


class ConcurrentRuntimeError(Exception):
    """Base type for ConcurrentDownloadRuntime errors."""


class RuntimeAlreadyRunningError(ConcurrentRuntimeError):
    """Raised by start() when the runtime is already running (§45)."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WorkerCompletion:
    """Never leaks infrastructure (§41): `error` (if any) is a short
    domain-safe string, never a raw exception/traceback object."""

    queue_entry_id: str
    task_id: str
    result: DispatchExecutionResult | None
    error: str | None = None


@dataclass(frozen=True)
class _InFlight:
    task_id: str
    candidate: DispatchCandidate
    future: Future


class RetryEventKind(Enum):
    SCHEDULED = "SCHEDULED"
    READY = "READY"
    EXHAUSTED = "EXHAUSTED"
    STALE = "STALE"


@dataclass(frozen=True)
class RetryRuntimeEvent:
    """Never leaks infrastructure (§58): no cookies, headers, raw
    exceptions, or tracebacks -- only identity and the retry decision."""

    queue_entry_id: str
    task_id: str
    kind: RetryEventKind
    attempt_count: int
    delay_seconds: float | None = None


@dataclass(frozen=True)
class _RetrySchedule:
    """Runtime-only (§17): never persisted, never holds a Thread/Timer/
    Exception/HTTP object. Keyed by queue_entry_id in the runtime's
    _retry_schedule dict, never by task_id (§18/§19) -- an old, removed
    queue occurrence's timer must never affect a newer re-enqueued one.
    `attempt_count_snapshot` guards against an old attempt generation
    firing late and mutating a task that has since moved on (§38)."""

    task_id: str
    due_monotonic: float
    attempt_count_snapshot: int


class ConcurrentDownloadRuntime:
    """Owns: a controller thread, a bounded worker pool, and an in-memory
    reservation/in-flight map. Does NOT own domain purity decisions (A1/A2),
    ordering (A1), candidate selection (A3), or the single-dispatch
    execution contract (A4) -- it only orchestrates them concurrently.
    """

    def __init__(
        self,
        *,
        queue: DownloadQueue,
        tasks: MutableMapping[str, DownloadTask],
        requests: Mapping[str, DownloadRequest],
        coordinator: DispatchCoordinator,
        config: SchedulerConfig,
        scheduler: SchedulerPolicy | None = None,
        executor_factory: Callable[[int], Executor] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        retry_policy: RetryPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue = queue
        self._tasks = tasks
        self._requests = requests
        self._coordinator = coordinator
        self._config = config
        self._scheduler = scheduler or SchedulerPolicy()
        self._executor_factory = executor_factory or (lambda n: ThreadPoolExecutor(max_workers=n))
        self._clock = clock
        # None (default) = exactly A5 behavior: no automatic retry, a
        # RETRY_WAIT task just sits until something external calls
        # mark_retry_ready() + notify_state_changed(). This keeps every
        # original A5 test valid unmodified.
        self._retry_policy = retry_policy
        self._monotonic = monotonic

        # One runtime coordination lock (§20/§21) protects all shared
        # Queue/Task reads+mutations the runtime performs, and is handed to
        # DispatchCoordinator.dispatch() so its prepare/finalize phases are
        # covered too. Network acquisition always runs with this released.
        self._state_lock = threading.RLock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()

        self._in_flight: dict[str, _InFlight] = {}

        self._completions: list[WorkerCompletion] = []
        self._completions_lock = threading.Lock()

        # Retry schedule and events live independently of _in_flight: a
        # backing-off task is NOT reserved/in-flight (§43) -- it consumes no
        # transfer slot while it waits.
        self._retry_schedule: dict[str, _RetrySchedule] = {}
        self._retry_events: list[RetryRuntimeEvent] = []
        self._retry_events_lock = threading.Lock()

        self._lifecycle_lock = threading.Lock()
        self._running = False
        self._controller_thread: threading.Thread | None = None
        self._executor: Executor | None = None
        self._fatal_error: BaseException | None = None

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._running:
                raise RuntimeAlreadyRunningError("runtime is already running")
            self._stop_event.clear()
            self._fatal_error = None
            if self._config.max_active_transfers > 0:
                self._executor = self._executor_factory(self._config.max_active_transfers)
            else:
                self._executor = None  # §15/§47: valid, just never submits anything
            self._controller_thread = threading.Thread(
                target=self._controller_loop, name="rychlik-scheduler-loop", daemon=True
            )
            self._running = True
            self._controller_thread.start()
        self.notify_state_changed()  # trigger the initial planning pass

    def stop(self, *, timeout: float | None = None) -> None:
        """Graceful (§43/§44): no new dispatch is submitted after shutdown
        begins; already-submitted/in-flight work is allowed to finish. This
        is NOT force-cancellation -- no thread is killed, no .part file is
        touched, no socket is torn down out from under an active transfer."""
        with self._lifecycle_lock:
            if not self._running:
                return  # idempotent no-op (§45)
            self._stop_event.set()
            self._wake_event.set()  # unblock a controller currently waiting
            controller_thread = self._controller_thread
            executor = self._executor

        if controller_thread is not None:
            controller_thread.join(timeout=timeout)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

        with self._lifecycle_lock:
            self._running = False
            self._controller_thread = None
            self._executor = None

    @property
    def fatal_error(self) -> BaseException | None:
        """Set if the controller loop itself raised an unexpected exception
        (§76) -- distinct from an individual worker failure, which is
        recorded as a WorkerCompletion instead and never kills the loop."""
        return self._fatal_error

    # --- external wake contract (§18/§19) ---------------------------------

    def notify_state_changed(self) -> None:
        """Call after any external mutation that might make new work
        eligible: enqueue, queue.resume(), set_priority(), mark_retry_ready(),
        a freshly-READY task, etc. Cheap, thread-safe, no domain coupling."""
        self._wake_event.set()

    # --- completion observation (§39/§40) ----------------------------------

    def drain_completions(self) -> list[WorkerCompletion]:
        with self._completions_lock:
            drained = list(self._completions)
            self._completions.clear()
        return drained

    def drain_retry_events(self) -> list[RetryRuntimeEvent]:
        with self._retry_events_lock:
            drained = list(self._retry_events)
            self._retry_events.clear()
        return drained

    def wait_for_idle(self, timeout: float) -> bool:
        """Idle = no reservations/in-flight workers and no pending wake
        signal. Polls with a fine-grained sleep bounded by `timeout` -- a
        test helper, not a production hot path (§66/§67)."""
        deadline = time.monotonic() + timeout
        while True:
            with self._state_lock:
                if not self._in_flight and not self._wake_event.is_set():
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)

    # --- controller loop ----------------------------------------------------

    def _controller_loop(self) -> None:
        try:
            while True:
                timeout = self._next_wait_timeout()
                self._wake_event.wait(timeout=timeout)
                if self._stop_event.is_set():
                    return
                with self._state_lock:
                    self._wake_event.clear()
                    if self._stop_event.is_set():
                        return
                    self._process_due_retries_locked()
                    self._plan_and_submit_locked()
        except BaseException as exc:  # noqa: BLE001 - must surface, not disappear (§76)
            self._fatal_error = exc

    def _next_wait_timeout(self) -> float | None:
        """None = block indefinitely (Event.wait(None)) until the next
        external wake -- no busy polling. A pending retry schedule makes
        the controller wake exactly at (or slightly after) its deadline
        instead, without ever spinning in a tight loop (§23/§24/§79)."""
        with self._state_lock:
            if not self._retry_schedule:
                return None
            nearest_due = min(entry.due_monotonic for entry in self._retry_schedule.values())
        return max(0.0, nearest_due - self._monotonic())

    def _plan_and_submit_locked(self) -> None:
        """Caller must already hold self._state_lock. Planning + reservation
        is one runtime-critical section (§22): no acquisition work happens
        while holding it."""
        if self._executor is None:
            return  # max_active_transfers == 0

        reserved_ids = frozenset(self._in_flight.keys())
        plan = self._scheduler.plan(
            queue=self._queue,
            tasks=self._tasks,
            config=self._config,
            reserved_queue_entry_ids=reserved_ids,
        )
        for candidate in plan.selected:
            self._reserve_and_submit_locked(candidate)

    # --- retry runtime (Prompt A6) -------------------------------------------

    def _process_due_retries_locked(self) -> None:
        if not self._retry_schedule:
            return
        now = self._monotonic()
        due_ids = [qeid for qeid, entry in self._retry_schedule.items() if entry.due_monotonic <= now]
        for queue_entry_id in due_ids:
            schedule = self._retry_schedule.pop(queue_entry_id)
            self._promote_retry_locked(queue_entry_id, schedule)

    def _promote_retry_locked(self, queue_entry_id: str, schedule: _RetrySchedule) -> None:
        """Only ever performs RETRY_WAIT -> READY (§30/§33) -- never calls
        DispatchCoordinator directly. A promoted task re-enters normal A3/A5
        selection on the controller's next planning pass in this same wake,
        preserving priority/order/capacity exactly like any other READY task."""
        try:
            entry = self._queue.get(queue_entry_id)
        except UnknownQueueEntryError:
            self._record_retry_event(
                queue_entry_id, schedule.task_id, RetryEventKind.STALE, schedule.attempt_count_snapshot
            )
            return

        # Re-enqueue identity safety (§19/§37): an old, removed occurrence's
        # timer must never affect a same-task-different-occurrence re-enqueue.
        if entry.task_id != schedule.task_id or entry.state == QueueEntryState.REMOVED:
            self._record_retry_event(
                queue_entry_id, schedule.task_id, RetryEventKind.STALE, schedule.attempt_count_snapshot
            )
            return

        task = self._tasks.get(schedule.task_id)
        if (
            task is None
            or task.state != DownloadTaskState.RETRY_WAIT
            or task.attempt_count != schedule.attempt_count_snapshot  # stale attempt generation (§38)
        ):
            self._record_retry_event(
                queue_entry_id, schedule.task_id, RetryEventKind.STALE, schedule.attempt_count_snapshot
            )
            return

        updated = task.mark_retry_ready(now=self._clock())
        self._tasks[schedule.task_id] = updated
        self._record_retry_event(
            queue_entry_id, schedule.task_id, RetryEventKind.READY, schedule.attempt_count_snapshot
        )
        # QueueEntry PAUSED + Task READY is valid (§31/§32/§67): A3 simply
        # will not select it until the queue entry is resumed. No special
        # casing needed here either way.

    def _handle_retry_wait_locked(self, candidate: DispatchCandidate) -> None:
        """Called from a worker thread immediately after dispatch() returns
        RETRY_WAIT, under self._state_lock. Preserves the original A4
        WorkerCompletion untouched (§26/§28) -- exhaustion is a SEPARATE
        subsequent runtime event, never a rewrite of that result."""
        if self._retry_policy is None:
            return  # backward compatible: no automatic retry configured

        task = self._tasks.get(candidate.task_id)
        if task is None or task.state != DownloadTaskState.RETRY_WAIT:
            return  # nothing sensible to schedule; state already moved on

        decision = self._retry_policy.decide(attempt_count=task.attempt_count, failure=task.last_failure)

        if decision.should_retry:
            due = self._monotonic() + decision.delay_seconds
            self._retry_schedule[candidate.queue_entry_id] = _RetrySchedule(
                task_id=candidate.task_id, due_monotonic=due, attempt_count_snapshot=task.attempt_count
            )
            self._record_retry_event(
                candidate.queue_entry_id,
                candidate.task_id,
                RetryEventKind.SCHEDULED,
                task.attempt_count,
                delay_seconds=decision.delay_seconds,
            )
        else:
            # Attempts exhausted (§15/§47): terminalize. last_failure is
            # preserved as-is -- it explains the real transfer failure; the
            # retry budget explains why no further attempt was made, which
            # is what the separate EXHAUSTED event is for (§16/§28).
            failed_task = task.fail(task.last_failure, now=self._clock())
            self._tasks[candidate.task_id] = failed_task
            self._queue.remove(candidate.queue_entry_id, now=self._clock())
            self._record_retry_event(
                candidate.queue_entry_id, candidate.task_id, RetryEventKind.EXHAUSTED, task.attempt_count
            )

    def _record_retry_event(
        self,
        queue_entry_id: str,
        task_id: str,
        kind: RetryEventKind,
        attempt_count: int,
        *,
        delay_seconds: float | None = None,
    ) -> None:
        with self._retry_events_lock:
            self._retry_events.append(
                RetryRuntimeEvent(
                    queue_entry_id=queue_entry_id,
                    task_id=task_id,
                    kind=kind,
                    attempt_count=attempt_count,
                    delay_seconds=delay_seconds,
                )
            )

    def _reserve_and_submit_locked(self, candidate: DispatchCandidate) -> None:
        if candidate.queue_entry_id in self._in_flight:
            # Must never happen: SchedulerPolicy already excludes reserved
            # entries. Fail loudly (§27) rather than silently double-dispatch.
            raise AssertionError(
                f"duplicate reservation for queue_entry_id {candidate.queue_entry_id!r}"
            )
        future = self._executor.submit(self._run_worker, candidate)
        self._in_flight[candidate.queue_entry_id] = _InFlight(
            task_id=candidate.task_id, candidate=candidate, future=future
        )

    # --- worker -------------------------------------------------------------

    def _run_worker(self, candidate: DispatchCandidate) -> None:
        """Runs on a worker thread. Must never let an exception escape
        un-recorded (§38/§39) and must always release its reservation
        (§11), regardless of outcome."""
        result: DispatchExecutionResult | None = None
        error: str | None = None
        try:
            result = self._coordinator.dispatch(
                candidate,
                queue=self._queue,
                tasks=self._tasks,
                requests=self._requests,
                now=self._clock(),
                lock=self._state_lock,
            )
            if result.outcome == DispatchOutcome.RETRY_WAIT:
                with self._state_lock:
                    self._handle_retry_wait_locked(candidate)
        except DispatchCoordinatorError as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - worker bug must not kill the loop (§38)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._state_lock:
                self._in_flight.pop(candidate.queue_entry_id, None)
            with self._completions_lock:
                self._completions.append(
                    WorkerCompletion(
                        queue_entry_id=candidate.queue_entry_id,
                        task_id=candidate.task_id,
                        result=result,
                        error=error,
                    )
                )
            self.notify_state_changed()  # release a slot -> wake controller to refill
