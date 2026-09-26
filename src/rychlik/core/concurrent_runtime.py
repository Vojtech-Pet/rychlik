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
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, MutableMapping

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.dispatch_coordinator import (
    DispatchCoordinator,
    DispatchCoordinatorError,
    DispatchExecutionResult,
)
from rychlik.core.download_queue import DownloadQueue
from rychlik.core.download_task import DownloadTask
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
    ) -> None:
        self._queue = queue
        self._tasks = tasks
        self._requests = requests
        self._coordinator = coordinator
        self._config = config
        self._scheduler = scheduler or SchedulerPolicy()
        self._executor_factory = executor_factory or (lambda n: ThreadPoolExecutor(max_workers=n))
        self._clock = clock

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
                self._wake_event.wait()
                if self._stop_event.is_set():
                    return
                with self._state_lock:
                    self._wake_event.clear()
                    if self._stop_event.is_set():
                        return
                    self._plan_and_submit_locked()
        except BaseException as exc:  # noqa: BLE001 - must surface, not disappear (§76)
            self._fatal_error = exc

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
