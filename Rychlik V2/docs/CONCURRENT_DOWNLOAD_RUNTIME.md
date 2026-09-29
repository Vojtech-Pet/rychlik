# Concurrent Worker Runtime / Scheduler Loop (Prompt A5)

No automatic retry timers. No exponential backoff. No safe HTTP resume. No
actual transfer pause/resume. No bandwidth limiting. No per-host limits.
No persistence. No crash recovery.

## Runtime architecture

```text
DownloadQueue + DownloadTask + capacity
        │
        ▼
SchedulerPolicy.plan(reserved_queue_entry_ids=...)   -- A3, extended, still pure
        │
        ▼
ConcurrentDownloadRuntime                            -- A5 (this phase)
├── controller thread (one lightweight scheduler loop)
├── in-flight/reservation map (runtime-only, ephemeral)
├── bounded worker pool (ThreadPoolExecutor by default)
└── thread-safe completion collection
        │
        ▼
N workers, each: DispatchCoordinator.dispatch(candidate, lock=state_lock)   -- A4, extended
        │
        ▼
real AcquisitionService (unmodified since Prompt 04.5)
```

No runtime execution was moved into `DownloadQueue`, `DownloadTask`, or
`SchedulerPolicy` — those remain exactly what they were in A1–A3.

## Controller loop

One lightweight thread, event-driven, never busy-spins:

```text
while not stopping:
    wait_event.wait()           # blocks; no polling while idle
    if stopping: break
    with state_lock:
        wait_event.clear()
        if stopping: break
        plan = SchedulerPolicy.plan(..., reserved_queue_entry_ids=in_flight.keys())
        for candidate in plan.selected:
            reserve(candidate)
            submit_worker(candidate)
```

Planning + reservation is one runtime-critical section: no acquisition
work happens while the lock is held. `notify_state_changed()` (called
after `start()`, after every worker completion, and available for
external callers) simply sets the wake `Event` — level-triggered, so a
redundant or slightly-early wake is harmless; the controller always
re-plans against current state rather than diffing what changed.

## External wake contract

Because A1/A2 domains do not know about the runtime, `notify_state_changed()`
is the explicit, minimal coupling point. A future application service is
expected to call it after: `enqueue()`, `queue.resume()`,
`queue.set_priority()`, `task.mark_retry_ready()`, or any other action that
might make new work eligible. No global event bus, no Qt signals, no
observer graph was introduced for this.

## Reservation model (the central A5 mechanism)

**Reservation is runtime-only.** It does not alter `QueueEntryState` or
`DownloadTaskState` — no `CLAIMED`/`RUNNING`/`DISPATCHED`/`SCHEDULED` state
was added anywhere. Its sole purpose is to close the race between
`SchedulerPolicy.plan()` selecting a candidate and that candidate's worker
actually transitioning its task to `TRANSFERRING`:

```text
without reservation:
  scheduler selects A (still READY)
  → A submitted to a worker
  → A briefly still READY
  → scheduler runs again before the worker gets to start_transfer()
  → A selected a second time
```

Reservations are keyed by `queue_entry_id`, never by `task_id` — this
preserves the A1/A4 re-enqueue identity guarantee: an old (already
removed) queue occurrence can never block or accidentally execute a new
occurrence of the same task. A reservation exists in `_in_flight` from the
moment a candidate is submitted to a worker until that worker's `finally`
block releases it — regardless of outcome (`COMPLETED`, `RETRY_WAIT`,
`FAILED`, `CANCELLED`, `STALE`, or an unexpected exception). Duplicate
reservation of the same `queue_entry_id` is a runtime bug and fails loudly
(`AssertionError`) rather than silently double-dispatching.

## SchedulerPolicy extension (A3, backward-compatible)

`plan()` gained one new optional keyword argument:

```text
reserved_queue_entry_ids: frozenset[str] = frozenset()
```

An empty (default) set preserves every existing A3 behavior exactly —
verified by all 25 original A3 tests remaining unmodified and green, plus
a dedicated `test_empty_reservation_set_preserves_old_behavior`. Reserved
entries are excluded from `selected` even if still `QUEUED`+`READY`
(§8), and — only if their task has not yet become `TRANSFERRING` — count
toward occupied capacity so a reservation cannot be double-counted once
the worker's `start_transfer()` actually lands (§9). The returned
`DispatchPlan.active_transfer_count` field's meaning is **unchanged**: it
always reports only `TRANSFERRING` tasks, never reservations.

## DispatchCoordinator extension (A4, backward-compatible)

`dispatch()` gained one new optional keyword argument:

```text
lock: ContextManager | None = None
```

`lock=None` (the default) behaves exactly as in Prompt A4 — verified by
all 32 original A4 tests remaining unmodified and green. When a lock is
supplied, it guards exactly two phases:

```text
WITH lock:
    resolve + revalidate (QUEUED + READY) + task.start_transfer()
WITHOUT lock:
    AcquisitionService.acquire(...)          <-- real network I/O
WITH lock:
    task.complete()/fail()/cancel()/wait_for_retry() + queue.remove() if terminal
```

**Critical invariant, proven by a dedicated test**
(`test_acquisition_executes_outside_lock`): while one worker is blocked
inside `acquire()`, another thread can successfully acquire the same lock
— the lock is never held across network I/O. Holding it there would make
concurrency fake (worker 2 unable to progress while worker 1 downloads).

## State lock boundary

One `threading.RLock` (`ConcurrentDownloadRuntime._state_lock`), created
and owned by the runtime, protects every read+mutation of the shared
`DownloadQueue`/`DownloadTask` state the runtime touches: planning +
reservation (controller thread) and the coordinator's prepare/finalize
phases (worker threads, via the `lock=` parameter above). `DownloadQueue`
and `DownloadTask` themselves remain deliberately not thread-aware — no
lock was added inside either domain module. Concurrency is entirely an
orchestration-layer concern, per the explicit A1/A2 purity requirement.

Nothing relies on the GIL to make compound operations (lookup + validate +
transition + queue renumber + reservation bookkeeping) safe — every such
sequence happens under the explicit lock.

## Worker completion model

```text
WorkerCompletion
├── queue_entry_id
├── task_id
├── result: DispatchExecutionResult | None
└── error: str | None      -- "TypeName: message", never a raw exception object
```

A worker never lets an exception escape unrecorded: both
`DispatchCoordinatorError` (e.g. `DispatchExecutionError`, itself already
wrapping an unexpected acquisition-layer exception per A4) and any other
unexpected exception are caught, converted to a short domain-safe string,
and stored. The reservation is released and a completion is recorded in
the `finally` block regardless of outcome. `drain_completions()` returns
and clears the accumulated list under its own dedicated lock (kept
separate from `_state_lock` to avoid unnecessary lock coupling between
unrelated concerns).

## Start/stop semantics

`start()`: constructs a bounded `ThreadPoolExecutor(max_workers=
max_active_transfers)` (or no executor at all when `max_active_transfers
== 0` — a valid, no-op-dispatch configuration, never
`ThreadPoolExecutor(max_workers=0)`, which is invalid), starts the
controller thread, and triggers an initial planning pass. Calling `start()`
while already running raises `RuntimeAlreadyRunningError` — chosen over
silent idempotency to catch programming bugs early.

`stop(timeout=None)`: **graceful, not force-cancellation** (§43). Sets a
stop flag, wakes the controller so it exits its wait loop without
planning further, joins the controller thread, then calls
`executor.shutdown(wait=True, cancel_futures=False)` so any already-
submitted/in-flight worker finishes normally — no thread is killed, no
`.part` file is touched mid-write, no socket is torn down out from under
an active transfer. No new candidate is submitted once shutdown begins,
even if a slot frees during shutdown (tested explicitly). `stop()` while
already stopped (or never started) is a safe, fast, idempotent no-op.

## Controller vs. worker failure

These are different classes of failure, handled differently on purpose:
a **worker** exception (inside `_run_worker`) is caught, recorded as a
`WorkerCompletion.error`, and the controller loop continues scheduling
other candidates — proven by `test_worker_exception_does_not_kill_controller`.
An unexpected exception **in the controller loop itself** would be a much
more serious failure (a bug in the planning/reservation logic, not in one
download); it is captured in `ConcurrentDownloadRuntime.fatal_error`
rather than silently vanishing, and the controller thread exits. Callers
should check `fatal_error` after any suspicious idle period.

## Known limitations

```text
no automatic retry timer / exponential backoff -- RETRY_WAIT settles and
  waits for an external mark_retry_ready() + notify_state_changed(); the
  runtime never calls either on its own
no real mid-transfer pause/resume (DownloadTaskState.PAUSED exists since
  A2, but nothing in A5 exposes a way to actually reach it for a running
  worker)
no per-host concurrency limits -- only the single global
  max_active_transfers is honored; source hostnames are never inspected
no bandwidth management/throttling
no persistence or crash recovery -- all reservation/in-flight state is
  in-memory only; a process crash loses it entirely (see "crash windows"
  in docs/DISPATCH_COORDINATOR.md, which still apply unchanged)
no progress aggregation across concurrent workers (the existing A4/
  Prompt-04.5 progress_callback still works per-request internally, but
  A5 does not widen scope into multi-task progress snapshots)
no priority aging/fairness changes -- HIGH can still starve NORMAL/LOW
  indefinitely, unchanged from A1/A3
no GUI wiring -- runtime correctness first, PySide6 integration later
force-stop/hard-cancel is deferred; stop() is always graceful
```
