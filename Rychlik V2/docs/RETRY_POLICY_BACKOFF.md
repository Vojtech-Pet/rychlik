# Retry Policy / Backoff Runtime (Prompt A6)

No HTTP failure taxonomy was introduced. No jitter exists. No Retry-After
support exists. No persistence exists. No GUI retry UX exists.

## RetryPolicy boundary

`RetryPolicy` (`rychlik/core/retry_policy.py`) is pure and deterministic:
given `attempt_count` and the task's `last_failure`, it decides whether
another attempt is allowed and, if so, after how long. It does not know
about threads, `Condition`, HTTP, `requests`, `QueueEntry`, the worker
pool, or PySide6 — structurally verified by an AST-based import test. It
does **not** decide whether a failure is retryable in the first place;
that remains entirely A4's failure-mapping boundary
(`DownloadTaskFailure.retryable`). `RetryPolicy.decide()` re-validates
that the supplied failure is actually retryable and raises
`InvalidRetryStateError` otherwise — A2 already guarantees this holds for
any task genuinely in `RETRY_WAIT`, so a violation here means externally
malformed state, not a normal outcome (§14).

## max_attempts meaning

`max_attempts` counts **total transfer attempts, including the first**.
`max_attempts = 1` means zero retries — the very first retryable failure
exhausts the budget immediately. `max_attempts = 3` means: attempt 1,
retry attempt 2, retry attempt 3, then give up. This is tested explicitly
(`test_max_attempts_one_means_zero_retries`).

## Backoff formula

```text
delay = min(max_delay_seconds, base_delay_seconds * backoff_multiplier ** (attempt_count - 1))
```

where `attempt_count` is the attempt that just failed. No jitter, no
randomness — fully deterministic, testable without real sleeping via an
injectable monotonic clock.

## attempt_count relationship

A6 introduces **no second attempt counter**. It reads and only ever
indirectly advances `DownloadTask.attempt_count` (A2's sole counter,
incremented only on `READY -> TRANSFERRING`) — promoting a task from
`RETRY_WAIT` to `READY` does not itself touch `attempt_count`; the next
real `start_transfer()` call (via the normal A4/A5 path) does that, exactly
as it always has.

## Runtime deadline model

`ConcurrentDownloadRuntime` (extended, not a new module) maintains
`_retry_schedule: dict[queue_entry_id, _RetrySchedule]` — runtime-only,
in-memory, never persisted, never holding a `Thread`/`Timer`/`Exception`/
HTTP object. **Keyed by `queue_entry_id`, never `task_id`** — this is
critical: an old, removed queue occurrence's timer must never affect a
newer re-enqueued occurrence of the same task (§18/§19/§37, proven by
`test_reenqueue_identity_old_schedule_does_not_affect_new_occurrence`).
Each schedule entry also carries an `attempt_count_snapshot` so a stale
deadline from an earlier attempt generation can never mutate a task that
has since moved on (§38, proven by the stale-schedule tests below).

## Monotonic clock

Retry deadlines are computed and compared using `time.monotonic()` by
default (injectable via the `monotonic=` constructor parameter, exactly
like the existing `clock=` parameter for `datetime.now`). Wall-clock UTC
is never used for backoff waiting — immune to system clock changes, DST,
NTP adjustments, or manual clock changes. Tests use a hand-rolled
`FakeMonotonic` controllable clock; no test sleeps for a real backoff
duration longer than a few tens of milliseconds.

## No one-thread-per-retry

There is no `threading.Timer` per failed task. The existing A5 controller
loop was extended: `Event.wait(timeout=...)` now uses the nearest pending
retry deadline (or `None` — block indefinitely — when no retry is
pending) instead of always waiting forever. This means the controller
wakes exactly when needed: on an explicit `notify_state_changed()`, or
precisely at the earliest retry deadline, whichever comes first — never a
tight polling loop.

## Controller integration

Each controller wake now does, under the single state lock:

```text
1. process all currently-due retry schedules (RETRY_WAIT -> READY only,
   after full revalidation -- see "stale timer safety" below)
2. run normal SchedulerPolicy planning + reservation + worker submission
   (unchanged from A5)
```

A promoted task re-enters ordinary A3/A5 selection in the very same
planning pass — it never bypasses `SchedulerPolicy`, reservations, or
capacity. **A6 never calls `DispatchCoordinator.dispatch()` directly** —
the only operation a due retry performs is `DownloadTask.mark_retry_ready()`.

## Retry registration

When a worker's `DispatchCoordinator.dispatch()` call returns
`RETRY_WAIT`, the runtime (still under the state lock, from the same
worker thread) evaluates `RetryPolicy.decide()`:

- **Retry allowed**: a `_RetrySchedule` is registered with `due_monotonic
  = now + delay`, and a `SCHEDULED` `RetryRuntimeEvent` is recorded.
- **Attempts exhausted**: `task.fail(task.last_failure)` +
  `queue.remove(queue_entry_id)` happen immediately (not deferred to a
  later controller wake), and an `EXHAUSTED` event is recorded.

The original A4 `WorkerCompletion` (showing `RETRY_WAIT`) is **never
rewritten** — exhaustion is a separate, subsequent runtime event/mutation,
not a retroactive edit of what the worker actually observed (§26/§28).
`last_failure` on an exhausted task remains the real transfer failure that
caused it, never replaced with a synthetic "retry limit exceeded" failure
— the `EXHAUSTED` event is what explains *why* no further attempt was
made (§16).

## Backward compatibility

`retry_policy=None` (the default) means **exactly A5 behavior**: a
`RETRY_WAIT` task simply sits until something external calls
`mark_retry_ready()` + `notify_state_changed()`. Every original A5 test
(65 of them) passes completely unmodified — verified directly.

## Queue behavior during RETRY_WAIT

Unchanged from A4/A5: the `QueueEntry` stays `QUEUED` throughout
`RETRY_WAIT` and the subsequent backoff wait. A backing-off task consumes
**no transfer slot and no runtime reservation** — `_in_flight` only ever
tracks active worker submissions, never retry schedules (§43, proven by
`test_retry_wait_releases_slot_for_sibling`).

## Queue PAUSED semantics

`QueueEntry PAUSED` + `DownloadTask RETRY_WAIT` is valid, and when the
backoff deadline expires the task **still** becomes `READY` — queue pause
controls dispatch eligibility, not the task's lifecycle timer (§31/§32).
`SchedulerPolicy` then correctly refuses to select it until the queue
entry is resumed (proven by
`test_paused_queue_entry_still_becomes_ready_but_not_dispatched`).

## Slot release semantics

A retryable failure releases the worker's reservation exactly as any
other outcome does (A5's existing `finally` block) — a sibling `READY`
task can start immediately even if the retrying task's backoff is long
(e.g. 10s), proven with real threads, not just fake-clock unit tests.

## Exhaustion behavior

Deterministic: `attempt_count >= max_attempts` after the just-failed
attempt terminates the task as `FAILED` and removes its `QueueEntry`,
with `last_failure` preserved as the real underlying failure. Never a
third attempt beyond `max_attempts`, even with `base_delay_seconds = 0`
(proven: `test_zero_delay_does_not_exceed_max_attempts`).

## Stale timer safety

A due retry schedule is discarded (recorded as a `STALE`
`RetryRuntimeEvent`, no mutation) whenever, at processing time:

```text
the QueueEntry no longer exists, or
the QueueEntry's task_id no longer matches the schedule, or
the QueueEntry is REMOVED, or
the task no longer exists, or
the task is no longer in RETRY_WAIT, or
the task's attempt_count no longer matches the schedule's snapshot
```

All six conditions are individually exercised by the test suite (task
cancelled before deadline, queue entry removed before deadline, the same
task re-enqueued as a new occurrence before the old deadline fires).

## Shutdown behavior

Once `stop()` begins, the controller checks the stop flag immediately
after acquiring the state lock and returns before processing any due
retries or submitting any new work — no retry is promoted and no new
attempt starts after shutdown begins, even if a fake/test clock is
advanced past a pending deadline afterward (proven:
`test_shutdown_does_not_promote_or_start_new_retry`). Registered-but-not-
yet-due retry schedules are **retained** in the runtime object's own state
(not cleared by `stop()`) — since neither A5 nor A6 clears any other
runtime state on `stop()` either, a subsequent `start()` on the same
runtime instance would naturally resume processing them. This is a
deliberate, minimal choice consistent with A5's existing (already
implicit) restart-on-same-instance behavior, not a new guarantee being
introduced.

## Known limitations

```text
no durable retry deadlines -- a process crash loses all pending schedules
no Retry-After header support
no HTTP-status-specific retry policy (no "404 never retries, 503 always
  does" -- that requires an explicit failure taxonomy, deliberately out
  of scope)
no jitter -- fully deterministic exponential backoff only
no per-host retry policy -- A6 never inspects URLs/hostnames
no connectivity-aware retry (no "wait for network" signal)
no manual GUI retry button -- DownloadTask.mark_retry_ready() already
  provides the domain primitive; UI wiring is a later phase
no actual transfer pause/resume -- unrelated to this phase, unchanged
  from A5
no persistence/crash recovery
```
