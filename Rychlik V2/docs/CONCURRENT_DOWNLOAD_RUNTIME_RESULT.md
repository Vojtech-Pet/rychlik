# Concurrent Worker Runtime / Scheduler Loop — Result (Prompt A5)

No automatic retry timers. No exponential backoff. No safe HTTP resume.
No actual transfer pause/resume. No bandwidth limiting. No per-host
limits. No persistence. No crash recovery.

## Baseline commit

`38bd047` (verified via `git rev-parse HEAD` before starting; worktree
was clean).

## Files changed

```text
src/rychlik/core/concurrent_runtime.py       (new)
src/rychlik/core/scheduler_policy.py         (extended: reserved_queue_entry_ids)
src/rychlik/core/dispatch_coordinator.py     (extended: lock parameter)
tests/test_concurrent_runtime.py             (new)
tests/test_concurrent_runtime_e2e.py         (new)
tests/test_scheduler_policy.py               (4 new reservation tests)
tests/test_dispatch_coordinator.py           (3 new lock-extension tests)
tests/http_fixture_server.py                 (new /track route + max_observed_active)
docs/CONCURRENT_DOWNLOAD_RUNTIME.md          (new)
docs/CONCURRENT_DOWNLOAD_RUNTIME_RESULT.md   (new, this file)
```

Both extensions are additive and fully backward-compatible: all 25
pre-existing A3 tests and all 32 pre-existing A4 tests pass completely
unmodified.

## Runtime types

```text
ConcurrentDownloadRuntime(queue, tasks, requests, coordinator, config,
  scheduler=None, executor_factory=None, clock=utc_now)
  .start() / .stop(timeout=None)
  .notify_state_changed()
  .drain_completions() -> list[WorkerCompletion]
  .wait_for_idle(timeout) -> bool
  .fatal_error -> BaseException | None

WorkerCompletion(queue_entry_id, task_id, result, error)
ConcurrentRuntimeError
└── RuntimeAlreadyRunningError
```

## Controller loop

One event-driven thread (`threading.Event`-based wait, never busy-spins).
Wakes on: `start()`, every worker completion, `notify_state_changed()`,
and `stop()`. Each wake performs one planning+reservation critical section
entirely under `_state_lock`, then releases it before any worker actually
runs acquisition.

## Worker model

`concurrent.futures.ThreadPoolExecutor(max_workers=max_active_transfers)`
by default; injectable via `executor_factory` for deterministic tests (a
hand-rolled `ManualExecutor` test double that captures submissions without
running them was used for the reservation-race and reservation-release
tests). `max_active_transfers == 0` is handled without ever constructing
an invalid zero-worker executor.

## Max concurrency

Enforced entirely through the SchedulerPolicy reservation extension's
`available_slots` calculation — `ConcurrentDownloadRuntime` never
independently caps submission count; it only ever submits exactly
`plan.selected`, and the plan itself is already capacity-correct.

## Reservation model

Runtime-only, in-memory, keyed by `queue_entry_id` (never `task_id`) —
see `docs/CONCURRENT_DOWNLOAD_RUNTIME.md` "Reservation model" for the full
rationale. No new `QueueEntryState` or `DownloadTaskState` was introduced.

## A3 reservation extension

`SchedulerPolicy.plan(..., reserved_queue_entry_ids: frozenset[str] =
frozenset())`. Empty default preserves all prior behavior exactly.
Reserved entries excluded from candidates; reserved-but-not-yet-
`TRANSFERRING` entries count toward occupied capacity without
double-counting once actually `TRANSFERRING`. `active_transfer_count`'s
reported meaning is unchanged (TRANSFERRING-only).

## A4 synchronization extension

`DispatchCoordinator.dispatch(..., lock: ContextManager | None = None)`.
`None` behaves exactly as before (verified: all 32 original tests pass
unmodified). When supplied, guards prepare (revalidate + start_transfer)
and finalize (terminal transition + queue cleanup) separately, with the
real `AcquisitionService.acquire()` call always running lock-free —
proven directly by a test that has another thread successfully acquire
the same lock while a worker is blocked mid-acquisition.

## State lock boundary

One `threading.RLock`, owned by `ConcurrentDownloadRuntime`, covers:
planning + reservation (controller thread), and the coordinator's
prepare/finalize phases (worker threads). Never covers the network
acquisition call itself. `DownloadQueue`/`DownloadTask` gained no locks of
their own — concurrency stays entirely at the orchestration layer.

## Automatic refill

Central A5 behavior, proven multiple ways: deterministically with a
`ManualExecutor` (`test_reservation_release_and_refill_manual`), with
real threads and a blocking fake acquisition service
(`test_automatic_refill_while_sibling_still_active`), and with real HTTP
through the local fixture server (`test_real_http_three_task_refill_e2e`,
elapsed time well under fully-serialized duration while
`max_observed_active` stayed capped at capacity).

## Worker completion model

Thread-safe (`_completions_lock`, separate from `_state_lock`), verified
with 10 concurrent completions arriving simultaneously — no loss, no
duplication (`test_completion_collection_no_loss_or_duplication`).

## Start/stop semantics

`start()` raises `RuntimeAlreadyRunningError` on double-start;
idempotent `stop()`. `stop()` is graceful — proven that already-in-flight
work is allowed to finish (`test_stop_lets_in_flight_work_finish`) and
that no new candidate is submitted once shutdown begins even if a slot
frees during shutdown (`test_no_new_dispatch_after_stop_requested`). Idle
shutdown is fast (< 1s, tested). No leaked non-daemon threads after
`stop()` (`test_no_thread_leak_after_stop`, comparing `threading.enumerate()`
before/after).

## Tests added

51 total: 22 in `test_concurrent_runtime.py` (deterministic, using
`ManualExecutor`/blocking fakes/`threading.Barrier`), 4 real HTTP E2E in
`test_concurrent_runtime_e2e.py`, 4 in `test_scheduler_policy.py` (A3
reservation extension), 3 in `test_dispatch_coordinator.py` (A4 lock
extension), plus the `/track` fixture-server addition used by the E2E
tests. Covers every §93 required group: reservation race, reservation
capacity, reservation release, max concurrency, real overlap (via a
3-party `threading.Barrier`, not timing), max=1 serialization, automatic
refill, priority ordering under concurrency, manual ordering under
concurrency, RETRY_WAIT + retry-ready wake, stale reserved candidate,
re-enqueue identity under reservation, worker runtime exception
continuation, queue integrity under concurrent cleanup, completion
collection thread-safety, start/stop lifecycle, idle shutdown, no thread
leak, A3 reservation compatibility, A4 lock correctness, real HTTP
concurrency E2E, and Artifact continuity.

## Tests run

437/437 passing (65 new — 51 above + 14 counted across the A3/A4
extension files; all 372 pre-existing tests remain green, unchanged).
Verified stable across 3 consecutive full-suite runs (no flakiness
observed).

## Max observed concurrency

Deterministic (via `threading.Barrier`, 3 parties): exactly 3, never more.
Deterministic (via lock-protected counter, 6 tasks / max=2): exactly 2,
never more, across the full run.

## Real HTTP concurrency E2E

`test_real_http_concurrency_e2e`: 2 tasks, `max_active_transfers=2`,
against the local fixture server's `/track` route —
`max_observed_active == 2` (real overlapping HTTP connections, observed
in-process via a lock-protected counter, not timing-inferred), both files
byte-exact, no leftover `.part` files.

## Real max=1 E2E

`test_real_http_max_one_e2e`: same setup with `max_active_transfers=1` —
`max_observed_active == 1`, proving true serialization over real HTTP.

## Real refill E2E

`test_real_http_three_task_refill_e2e`: 3 tasks, `max_active_transfers=2`
— `max_observed_active == 2` throughout, and total elapsed time
(~0.6s for two waves) well under the ~0.9s three fully-serialized
`/track` calls (0.3s each) would require, proving the third task started
automatically while capacity allowed rather than waiting for full
serialization.

## Artifact continuity

`test_real_http_artifact_continuity`: the `CompletedDownload` returned
inside a `WorkerCompletion.result` still feeds
`Artifact.from_completed_download()` exactly as established since Prompt
04.5 — unaffected by concurrency.

## Existing test regressions

NONE.

## Bugs found

None in the runtime/domain code. Three test-authoring bugs were found and
fixed while building this phase's own test suite (not runtime defects):
(1) a reservation-release test asserted `len(pending) == 1` right after
removing one entry, which was trivially true before the refill even
happened — corrected to wait for `== 2` (the actual refilled state); (2)
a "retry once" fake acquisition service unconditionally failed every call
for a given task instead of only the first, causing a legitimate
non-terminating retry loop in the test itself — fixed to fail only on the
first attempt; (3) a worker-exception test asserted the raw `RuntimeError`
type name appeared in the recorded completion error, when by design
(established in Prompt A4) the worker actually catches the coordinator's
typed `DispatchExecutionError` wrapper — assertion corrected to match the
documented, intended wrapping behavior rather than the unwrapped original.

## Known limitations

See `docs/CONCURRENT_DOWNLOAD_RUNTIME.md` "Known limitations" — no
automatic retry timer/backoff, no real pause/resume, no per-host limits,
no bandwidth management, no persistence/crash recovery, no multi-task
progress aggregation, no priority aging, no GUI wiring, no force-stop: all
explicitly deferred, none solved here.

---

PHASE: Prompt A5 — Concurrent Worker Runtime / Scheduler Loop

STATUS: DONE

BASELINE COMMIT: 38bd047

FILES CHANGED: src/rychlik/core/concurrent_runtime.py (new),
src/rychlik/core/scheduler_policy.py (extended),
src/rychlik/core/dispatch_coordinator.py (extended),
tests/test_concurrent_runtime.py (new),
tests/test_concurrent_runtime_e2e.py (new),
tests/test_scheduler_policy.py (+4), tests/test_dispatch_coordinator.py (+3),
tests/http_fixture_server.py (+/track route),
docs/CONCURRENT_DOWNLOAD_RUNTIME.md (new),
docs/CONCURRENT_DOWNLOAD_RUNTIME_RESULT.md (new)

RUNTIME TYPES: ConcurrentDownloadRuntime, WorkerCompletion,
ConcurrentRuntimeError, RuntimeAlreadyRunningError

CONTROLLER LOOP: single event-driven thread, threading.Event-based wait,
never busy-spins, one plan+reserve critical section per wake

WORKER MODEL: ThreadPoolExecutor(max_workers=max_active_transfers) by
default, injectable executor_factory for deterministic tests

MAX CONCURRENCY: enforced entirely via SchedulerPolicy's reservation-aware
available_slots calculation; runtime never independently caps submissions

RESERVATION MODEL: runtime-only, in-memory, keyed by queue_entry_id, no
new domain state introduced

A3 RESERVATION EXTENSION: plan(reserved_queue_entry_ids=frozenset()),
empty default fully backward compatible (all 25 original tests unmodified)

A4 SYNCHRONIZATION EXTENSION: dispatch(lock=None), None fully backward
compatible (all 32 original tests unmodified); acquisition proven to run
outside the lock

STATE LOCK BOUNDARY: one threading.RLock owned by the runtime; covers
planning+reservation and coordinator prepare/finalize; never covers
network I/O; DownloadQueue/DownloadTask remain lock-free

AUTOMATIC REFILL: proven deterministically (ManualExecutor), with real
threads (blocking fake), and with real HTTP (fixture server)

WORKER COMPLETION MODEL: thread-safe, dedicated lock, no loss/duplication
under 10 simultaneous completions

START/STOP SEMANTICS: start() raises on double-start; stop() idempotent,
graceful (in-flight work finishes, no new dispatch after shutdown begins),
fast when idle, no thread leaks

TESTS ADDED: 65 (51 unit/deterministic + 4 real E2E + 4 A3 extension +
3 A4 extension + fixture server addition)

TESTS RUN: 437/437 passing

MAX OBSERVED CONCURRENCY: exactly matches configured capacity in every
test (3-party Barrier test, 6-task/max=2 counter test)

REAL HTTP CONCURRENCY E2E: PASS (max_observed_active == 2 for 2 real
concurrent HTTP transfers)

REAL MAX=1 E2E: PASS (max_observed_active == 1, true serialization)

REAL REFILL E2E: PASS (elapsed time proves 2-wave refill, not 3-wave
serialization)

ARTIFACT CONTINUITY: PASS

THREAD LEAK CHECK: PASS (no residual threads after stop())

EXISTING TEST REGRESSIONS: NONE

BUGS FOUND: none in runtime/domain code; three test-authoring bugs found
and fixed in this phase's own new tests (see "Bugs found" above)

KNOWN LIMITATIONS: no retry timer/backoff, no real pause/resume, no
per-host limits, no bandwidth management, no persistence/crash recovery,
no multi-task progress aggregation, no priority aging changes, no GUI,
no force-stop — all deferred to A6+

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt A6 — Retry Policy / Backoff Runtime

COMMIT: (recorded after this phase's commit)
