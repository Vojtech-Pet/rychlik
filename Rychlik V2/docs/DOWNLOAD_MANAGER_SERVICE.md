# Download Manager Application Service / Backend Facade (Prompt A10)

No GUI wiring exists. No PySide6/Qt dependency exists in
`download_manager_service.py`. No yt-dlp backend exists. No bandwidth
limiting exists. No final download-history model exists. Share by Link is
not owned by `DownloadManagerService`.

## Why the facade exists

The backend is powerful (A1–A9) but fragmented: a caller would otherwise
need to personally sequence `queue.enqueue()`, `task.mark_ready()`,
`state_store.checkpoint_task_state(...)`, and
`runtime.notify_state_changed()` correctly, every time, for every
operation — coupling presentation code to nine layers of internals.
`DownloadManagerService` is the one application boundary: presentation
code (a future GUI/CLI/API) only ever calls it.

```text
GUI / CLI / future API
         |
         v
DownloadManagerService
         |
         +-- A1 Queue            +-- A6 Retry
         +-- A2 Tasks            +-- A7 Progress
         +-- A3 Scheduler        +-- A8 Persistence
         +-- A4 Coordinator      +-- A9 Pause/Resume
         +-- A5 Runtime
```

The service **composes**, it does not **reimplement**: queue ordering
(A1), the `DownloadTask` transition graph (A2), scheduler candidate
selection (A3), retry delay computation (A6), Range validation (A9),
speed calculation (A7), and SQLite schema logic (A8) all remain exactly
where they already lived before this phase.

## Why not GUI yet

A10 must first prove one facade with stable commands, stable snapshots,
and stable notifications, entirely headlessly. Only once that is proven
should PySide6 consume it — otherwise GUI code risks becoming the
orchestration layer by accident, exactly the coupling this phase exists
to prevent. `main.py`/`DownloadWidget`/`launch.sh`/`rychlik.desktop` are
untouched by this phase.

## Public API table

| API | Meaning |
|---|---|
| `start()` | open/migrate the state store, run `RestartRecovery`, start the A5 runtime; returns the `RecoveryReport` |
| `stop(timeout=None)` | graceful A5 shutdown, mark clean shutdown, close the store |
| `add_download(request, priority=NORMAL)` | durably create + enqueue a new download |
| `hold(queue_entry_id)` | pause queue eligibility (`QueueEntry -> PAUSED`) |
| `release_hold(queue_entry_id)` | restore queue eligibility (`QueueEntry -> QUEUED`) |
| `pause_transfer(queue_entry_id)` | request A9's cooperative real transfer pause |
| `resume_transfer(queue_entry_id)` | request A9's scheduled partial resume |
| `cancel(queue_entry_id)` | terminal cancellation, waiting or active |
| `retry_now(queue_entry_id)` | end a `RETRY_WAIT` backoff early, within budget |
| `set_priority(queue_entry_id, priority)` | change A1 priority |
| `move_before(queue_entry_id, target_id)` | manual same-band reorder |
| `move_after(queue_entry_id, target_id)` | manual same-band reorder |
| `snapshot()` | A7's immutable `DownloadManagerSnapshot` |
| `item_snapshot(queue_entry_id)` | A7's immutable `DownloadViewSnapshot`, or `None` |
| `subscribe(callback)` / `unsubscribe(token)` | framework-neutral change notifications |
| `last_recovery_report` | the `RecoveryReport` from the most recent `start()` |
| `state` | the service's own `ManagerState` (NEW/RUNNING/STOPPING/STOPPED/FAULTED) |

## Service lifecycle

`ManagerState` is an application lifecycle enum — **not** a
`DownloadTaskState` and never mixed with one: `NEW -> RUNNING ->
STOPPING -> STOPPED`, with `FAULTED` reachable from any state when
`start()` itself raises. `start()` is idempotent on `RUNNING` (returns the
existing `RecoveryReport`); `stop()` is idempotent on `STOPPED`/`NEW`. No
constructor side effects (`__init__` never opens the database or starts
threads) — `start()`/`stop()` are explicit, so tests and future callers
have full control over lifecycle timing. An optional `with
DownloadManagerService(...) as manager:` context-manager form is
supported purely as sugar over `start()`/`stop()`.

### Startup sequence

```text
open + validate/migrate schema (A8)
-> RestartRecovery (A8/A9, including PAUSED-with-valid-partial logic)
-> persist the recovered canonical state (replace_all)
-> rebuild runtime-only state: DownloadQueue.restore(), a fresh
   ProgressRegistry (A7), the recovered retry seeds (A6)
-> construct DispatchCoordinator + ConcurrentDownloadRuntime
   (enable_pause_resume=True always, so hold/pause/resume/cancel all work
   through the facade without a separate opt-in)
-> runtime.start()
-> start the event pump thread
-> state = RUNNING
```

Workers never run before recovery has fully persisted its canonical
state — `ConcurrentDownloadRuntime` is only constructed (let alone
started) after `replace_all()` succeeds. Any exception during `start()`
leaves the service `FAULTED` rather than partially initialized.

### Shutdown sequence

```text
state = STOPPING
-> runtime.stop(timeout) (graceful: no new dispatch, in-flight work
   finishes under A5's own existing semantics)
-> stop the event pump thread
-> mark_clean_shutdown() -- SKIPPED if the service was FAULTED, since a
   faulted service's durable state was never proven consistent
-> close the state store
-> state = STOPPED
```

## `add_download`: durability-before-dispatch

```text
create DownloadTask (READY) + associate DownloadRequest
-> enqueue (A1)
-> durably checkpoint task + request + queue_entry, all inside the SAME
   critical section (the runtime's own state_lock)
-> only on success: notify_state_changed() + emit DOWNLOAD_ADDED
-> on persistence failure: roll back the in-memory queue/task/request
   mutation, raise PersistenceCommandError -- the runtime is never woken,
   AddDownloadResult is never returned
```

A worker can therefore never see a task the caller was told failed to
persist.

## Queue hold vs. transfer pause (kept unambiguous)

Exactly the A1/A9 distinction is preserved at the API surface, under
deliberately different names:

```text
hold() / release_hold()       -- QueueEntry eligibility hold; never
                                  touches an already-active transfer
pause_transfer() / resume_transfer() -- A9's real cooperative transfer
                                  pause/resume; never mutates
                                  QueueEntryState
```

A future GUI may present these with friendlier combined labels, but the
backend API itself never conflates them.

## Async command semantics

`pause_transfer()`, `cancel()` (when the task is actively `TRANSFERRING`),
and `resume_transfer()` are all **asynchronous**: they return
`ACCEPTED` once the underlying A9/A5 request has been registered, not once
the final lifecycle state (`PAUSED`/`CANCELLED`/dispatched) has actually
been reached. Consumers observe the final state via `subscribe()` events
or a later `snapshot()`/`item_snapshot()` call — never by blocking inside
the command itself. `hold()`, `release_hold()`, `set_priority()`,
`move_before()`/`move_after()`, `retry_now()`, and the waiting-task path
of `cancel()` are synchronous and return `APPLIED` (or `NO_OP`/`REJECTED`)
once the durable checkpoint has actually committed.

```text
APPLIED  -- the domain mutation and its durable checkpoint both succeeded
ACCEPTED -- an asynchronous request was registered; final state pending
NO_OP    -- already in the requested state; no mutation was needed
REJECTED -- rejected for a stated reason (unknown/REMOVED occurrence,
            wrong task state, cross-priority reorder, ...)
```

`SUCCESS` is deliberately never used as a status name — it would imply a
synchronous physical pause that A9's cooperative model cannot promise.

## Persistence coordination

Every durable command follows the same sequence:

```text
acquire the runtime's own state_lock (reentrant RLock -- a command may
  safely call runtime.checkpoint_task() from within it)
-> validate (unknown occurrence / wrong state -> REJECTED, no mutation)
-> apply the domain mutation (A1/A2 call)
-> durable checkpoint (state_store.checkpoint_task_state(), via the
   runtime's own checkpoint_task() helper where a single row suffices)
-> release the lock
-> notify_state_changed() + emit a ManagerEvent, both OUTSIDE the lock
```

`set_priority()`/`move_before()`/`move_after()` checkpoint every entry in
the affected priority band(s), not just the moved entry — a reorder can
renumber every sibling's `position` in that band (A1's own documented side
effect), and `DownloadQueue.restore()`'s contiguous-position invariant
would otherwise see a stale row after a restart. Only `queue_entries` rows
are touched by this path; `retry_schedules`/`partial_transfers` are never
read or rewritten by a priority/reorder command.

Rollback on a persistence failure is implemented for the operations with
a well-defined, cheap inverse: `add_download` (remove the new
task/request/queue-entry), `hold`/`release_hold` (call the opposite queue
operation), `set_priority` (restore the old priority). `move_before`/
`move_after` and the async commands do not attempt exact rollback on
checkpoint failure — see Known limitations.

`DownloadManagerService` still makes no atomicity claim beyond what A8/A9
already documented: SQLite, the filesystem, and the network are not one
transaction.

## Runtime wake coordination

No caller of the service ever needs to call
`ConcurrentDownloadRuntime.notify_state_changed()` — every command that
can make new work eligible calls it itself, after (never before) its
durable checkpoint succeeds.

## Snapshot exposure

`snapshot()` returns A7's existing `DownloadManagerSnapshot` unmodified —
no second GUI representation is invented. `item_snapshot()` returns A7's
`DownloadViewSnapshot` for one occurrence (or `None`). Both remain safe to
call from any thread, at any time, while the runtime is active, using the
exact copy-then-compose locking A7 already established — A10 adds no new
unsafe read path. Neither ever leaks a `Future`, `Thread`, `Lock`,
`ProgressRegistry`, live `DownloadTask`/`QueueEntry`, or a
`requests.Response` — commands and results deal exclusively in IDs,
enums, and immutable dataclasses.

## Event / notification model

`ManagerEvent(kind, task_id=None, queue_entry_id=None)` is a small
invalidation signal, never a duplicated snapshot — consumers call
`snapshot()`/`item_snapshot()` in response. `subscribe()`/`unsubscribe()`
is pure Python (no `Signal`); a future Qt adapter can translate events to
one later.

Two event sources:

1. **Synchronous, command-driven** — `DOWNLOAD_ADDED`, `QUEUE_CHANGED`
   (hold/release/priority/reorder), `RESUME_REQUESTED`, `CANCELLED`
   (waiting-task path), `RETRY_READY` (from `retry_now()`) are emitted
   immediately after the triggering command's durable checkpoint commits.
2. **Asynchronous, background pump** — a small dedicated thread (mirroring
   A5's own controller thread) periodically drains
   `runtime.drain_completions()`/`drain_retry_events()` and translates
   them: `COMPLETED`/`FAILED`/`CANCELLED`/`PAUSED` from
   `WorkerCompletion.result.outcome`, `RETRY_SCHEDULED`/`RETRY_READY`/
   `RETRY_EXHAUSTED` from `RetryRuntimeEvent.kind`. The same pump also
   emits at most one coalesced `PROGRESS_CHANGED` event per
   `progress_event_interval_seconds` (default 0.25s) tick whenever
   anything is currently `TRANSFERRING` — never one event per A7 progress
   chunk, which would flood subscribers on a fast connection.

## Event backpressure

There is no unbounded event queue: events are dispatched synchronously,
directly to each subscriber, from whichever thread produced them (a
command's calling thread, or the pump thread). Progress-changed
invalidation is bounded by construction (one tick per pump interval, not
per chunk); lifecycle-critical events (completion/failure/cancellation/
retry) are never coalesced or dropped — they are drained exactly once
each from A5's own bounded completion/retry-event lists.

## Thread-safety model

Every public command method is safe to call from any thread, including a
future Qt UI thread — internally they all synchronize through the same
`ConcurrentDownloadRuntime.state_lock` (a reentrant `RLock`) that A5's own
controller/workers already use. **Subscriber callbacks never run while any
internal lock is held** — `_emit()` copies the subscriber list under its
own small `_subscribers_lock`, releases it, then invokes callbacks
entirely outside that lock and outside the runtime's state lock. This is
what makes `snapshot()`-from-inside-a-callback safe (tested directly) and
is the property a future Qt integration will most rely on to avoid
deadlocks.

## Fault model

`DownloadManagerError` is the base type, with
`ManagerNotRunningError` / `UnknownDownloadError` (reserved, not raised by
any built-in command — every command returns a `REJECTED`
`ManagerCommandResult` for an unknown occurrence instead, for one
consistent contract) / `PersistenceCommandError` /
`ManagerFaultedError` as the bounded subtypes. An unexpected exception
during `start()` moves the service to `FAULTED` and re-raises; every
subsequent command raises `ManagerFaultedError` immediately. The service
never automatically resets or recreates the database on a startup
failure — corruption is surfaced, not silently discarded.

## Structural boundary

`download_manager_service.py` imports only A1–A9 core/acquisition modules
plus the stdlib. It imports no `PySide6`, `ShareLink`, `SharePreview`, or
`FriendSend` — verified by an AST-based structural test, matching the
pattern already established for every prior phase's boundary modules.

## Known limitations

```text
move_before()/move_after() does not attempt exact in-memory rollback on a
  persistence (checkpoint) failure -- the reorder already applied to the
  in-memory DownloadQueue is left as-is and PersistenceCommandError is
  raised; a caller should treat this as "state may be inconsistent,
  re-read snapshot()" rather than assuming a clean revert. add_download/
  hold/release_hold/set_priority DO roll back cleanly.

pause_transfer()/cancel(active)/resume_transfer() are asynchronous by
  design (A9's cooperative model) -- there is no bounded "wait until
  actually paused/cancelled" call in this phase; a caller polls
  snapshot()/item_snapshot() or subscribes to events.

no TRANSFER_STARTED event exists -- a READY->TRANSFERRING transition is
  only observable via the coalesced PROGRESS_CHANGED tick or a direct
  snapshot() poll, not a dedicated event kind. Documented rather than
  silently omitted.

no dedicated "wait for terminal state" helper is exposed publicly (tests
  use their own polling helpers, matching every prior phase's pattern).

A9's combined SIGKILL -> Range-resume real subprocess E2E remains OPEN
  (docs/OPEN_VALIDATION_DEBT.md, A9-CRASH-RANGE-E2E) -- not executed
  during A10 either; A10 does not touch acquisition/resume mechanics.

no GUI wiring, no yt-dlp backend, no bandwidth limiting, no download-
  history model, no Share-by-Link ownership -- all explicitly out of
  scope for this phase.
```
