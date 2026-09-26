# Retry Policy / Backoff Runtime — Result (Prompt A6)

No HTTP failure taxonomy was introduced. No jitter exists. No Retry-After
support exists. No persistence exists. No GUI retry UX exists.

## Baseline commit

`0e8b72b` (verified via `git rev-parse HEAD` before starting; worktree
was clean).

## Files changed

```text
src/rychlik/core/retry_policy.py              (new)
src/rychlik/core/concurrent_runtime.py         (extended: retry integration)
tests/test_retry_policy.py                     (new)
tests/test_retry_runtime.py                    (new)
tests/test_retry_runtime_e2e.py                (new)
tests/http_fixture_server.py                   (new /flaky/<key> route)
docs/RETRY_POLICY_BACKOFF.md                   (new)
docs/RETRY_POLICY_BACKOFF_RESULT.md            (new, this file)
```

`ConcurrentDownloadRuntime`'s extension is purely additive: two new
optional constructor parameters (`retry_policy=None`, `monotonic=
time.monotonic`), defaulting to exact A5 behavior. All 65 pre-existing A5
tests pass completely unmodified.

## Policy types

```text
RetryPolicyConfig(max_attempts=3, base_delay_seconds=1.0,
  backoff_multiplier=2.0, max_delay_seconds=30.0)
RetryDecision(should_retry, delay_seconds, reason)
RetryReason: RETRY_SCHEDULED, ATTEMPTS_EXHAUSTED
RetryPolicy.decide(attempt_count, failure) -> RetryDecision
RetryPolicyDomainError
├── InvalidRetryPolicyConfigError
└── InvalidRetryStateError
```

## Runtime types / extensions

```text
RetryEventKind: SCHEDULED, READY, EXHAUSTED, STALE
RetryRuntimeEvent(queue_entry_id, task_id, kind, attempt_count, delay_seconds)
ConcurrentDownloadRuntime.drain_retry_events() -> list[RetryRuntimeEvent]
ConcurrentDownloadRuntime(..., retry_policy=None, monotonic=time.monotonic)
```

## Max-attempt semantics

Total transfer attempts including the first: `max_attempts=1` -> zero
retries; `max_attempts=N` -> the initial attempt plus up to `N-1` retries.
Uses A2's `DownloadTask.attempt_count` exclusively — no second counter.

## Backoff formula

`delay = min(max_delay_seconds, base_delay_seconds * backoff_multiplier **
(attempt_count - 1))`, no jitter, fully deterministic.

## Deadline mechanism

Runtime-only `_retry_schedule: dict[queue_entry_id, _RetrySchedule]`,
never persisted. Keyed by `queue_entry_id` (never `task_id`) with an
`attempt_count_snapshot` guard, closing both the re-enqueue-identity and
stale-generation hazards. The A5 controller loop's `Event.wait()` now
takes a computed timeout (nearest pending deadline, or `None` to block
indefinitely) instead of always blocking forever — no
`threading.Timer`-per-task, no busy polling.

## A5 integration

Two backward-compatible extension points in the already-existing module:
retry registration happens inside `_run_worker` right after a `RETRY_WAIT`
`dispatch()` result (under the shared state lock); due-retry processing
(`_process_due_retries_locked`) runs at the start of every controller wake,
before normal planning — a promoted task re-enters the ordinary
`SchedulerPolicy` selection in the very same pass. A6 never calls
`DispatchCoordinator.dispatch()` directly from retry logic.

## Queue behavior during RETRY_WAIT

`QueueEntry` remains `QUEUED` throughout backoff; no transfer slot or
runtime reservation is consumed while a task waits (proven with real
threads: a sibling starts immediately despite a 10s-backoff neighbor).

## Queue PAUSED behavior

`QueueEntry PAUSED` + `DownloadTask RETRY_WAIT` is valid; on deadline
expiry the task still becomes `READY` (queue pause is a dispatch-
eligibility concern, not a lifecycle-timer freeze), but `SchedulerPolicy`
correctly refuses to select it until the queue entry is resumed.

## Exhaustion behavior

`attempt_count >= max_attempts` -> immediate `task.fail(last_failure)` +
`queue.remove()`, with the real underlying failure preserved untouched —
never overwritten with a synthetic "retry limit" failure. A separate
`EXHAUSTED` `RetryRuntimeEvent` explains why no further attempt was made.
Proven never to exceed `max_attempts` even with `base_delay_seconds = 0`.

## Retry events

`SCHEDULED` / `READY` / `EXHAUSTED` / `STALE`, drained via
`drain_retry_events()` (dedicated lock, same pattern as `WorkerCompletion`/
`drain_completions()`). Never contains cookies, headers, raw exceptions,
or tracebacks.

## Tests added

36 total: 15 in `test_retry_policy.py` (pure policy: first/second retry
delay, delay cap, exhaustion, every config-validation boundary, malformed-
state rejection, zero-delay, structural import check), 18 in
`test_retry_runtime.py` (manual fake-clock deadline precision, exhaustion,
`max_attempts=1`, zero-delay-never-exceeds-budget, eventual success within
budget, backward-compatible `retry_policy=None`, slot release during
backoff, queue-paused-still-becomes-ready, priority preserved when a
retried task competes with a fresh HIGH task, all stale-schedule
scenarios — cancelled task / removed queue entry / re-enqueue identity —
earlier-wake-for-new-task not blocked by a distant retry deadline,
multiple simultaneous deadlines, shutdown starts no new retries, no
thread leak, duplicate-schedule-safety, structural import check), 3 real
non-mock E2E in `test_retry_runtime_e2e.py`.

## Tests run

473/473 passing (36 new across 3 dedicated files, plus the reused
fixture-server addition; all 437 pre-existing tests remain green,
unchanged). Verified stable across repeated full-suite runs.

## Real HTTP transient retry E2E

`test_real_http_transient_retry_e2e`: a real `/flaky/<key>` fixture route
returns `503` once then `200`; through the real
`AcquisitionService`/`DirectHttpAcquisition`/`DispatchCoordinator`/
`ConcurrentDownloadRuntime`/`RetryPolicy` stack (no mock network), the
task reaches `COMPLETED` with `attempt_count == 2`, exactly 2 real HTTP
requests observed server-side, byte-exact file, no leftover `.part`. A
test-specific failure mapper marks the transient `AcquisitionError` as
retryable (§86) — the production `default_failure_mapper` remains
conservatively `retryable=False` for every `AcquisitionError`, since no
HTTP-status taxonomy exists yet; this proves A6's retry *orchestration*,
not a final HTTP retry-classification policy, and is documented as such.

## Real retry exhaustion E2E

`test_real_http_retry_exhaustion_e2e`: **PASS**. A fixture route that
always returns `503`, with `max_attempts=2`, produces exactly 2 real HTTP
requests (never a 3rd), ends `FAILED`, `QueueEntry` removed, no leftover
`.part` file.

## Artifact continuity

`test_real_http_transient_retry_artifact_continuity`: **PASS**. The
`CompletedDownload` from the eventually-successful retry attempt still
feeds `Artifact.from_completed_download()` unaffected by the retry
machinery.

## Existing test regressions

NONE.

## Bugs found

None in the retry policy or runtime integration code. Two test-authoring
mistakes were found and fixed while building this phase's own tests: (1)
an assertion expecting exactly 1 `SCHEDULED` retry event for
`max_attempts=3` was arithmetically wrong (attempt 1 fails -> SCHEDULED,
attempt 2 fails -> SCHEDULED again, attempt 3 fails -> EXHAUSTED — 2
`SCHEDULED` events, not 1); (2) an artifact-continuity E2E test picked the
*first* `WorkerCompletion` matching the task_id (which was the initial
`RETRY_WAIT` result with `completed_download=None`) instead of filtering
for the eventual `COMPLETED` one, causing an `AttributeError` — fixed to
filter by outcome explicitly.

## Known limitations

See `docs/RETRY_POLICY_BACKOFF.md` "Known limitations" — no durable retry
deadlines, no Retry-After support, no HTTP-status-specific policy, no
jitter, no per-host retry policy, no connectivity-aware retry, no manual
GUI retry button, no real pause/resume, no persistence/crash recovery: all
explicitly deferred, none solved here.

---

PHASE: Prompt A6 — Retry Policy / Backoff Runtime

STATUS: DONE

BASELINE COMMIT: 0e8b72b

FILES CHANGED: src/rychlik/core/retry_policy.py (new),
src/rychlik/core/concurrent_runtime.py (extended),
tests/test_retry_policy.py (new), tests/test_retry_runtime.py (new),
tests/test_retry_runtime_e2e.py (new),
tests/http_fixture_server.py (+/flaky/<key> route),
docs/RETRY_POLICY_BACKOFF.md (new), docs/RETRY_POLICY_BACKOFF_RESULT.md (new)

RETRY POLICY TYPES: RetryPolicyConfig, RetryDecision, RetryReason,
RetryPolicy, RetryPolicyDomainError + 2 subtypes

MAX ATTEMPTS SEMANTICS: total attempts including the first;
max_attempts=1 means zero retries

BACKOFF FORMULA: min(max_delay, base * multiplier ** (attempt_count - 1)),
no jitter

CLOCK / DEADLINE MODEL: injectable monotonic clock, dict-based runtime-only
retry schedule, controller Event.wait(timeout=nearest_deadline)

RETRY SCHEDULE IDENTITY: keyed by queue_entry_id + attempt_count_snapshot,
never by task_id alone

A5 CONTROLLER INTEGRATION: due-retry processing runs before normal
planning on every wake; retry registration/exhaustion happens in the
worker thread under the shared state lock right after a RETRY_WAIT result

QUEUE BEHAVIOR DURING RETRY_WAIT: QueueEntry stays QUEUED, no slot/
reservation consumed while backing off

QUEUE PAUSED BEHAVIOR: task still becomes READY at deadline; scheduler
still refuses to dispatch until resumed

TRANSFER SLOT BEHAVIOR: released immediately on RETRY_WAIT, exactly like
any other terminal-ish worker outcome

EXHAUSTION BEHAVIOR: task.fail(real last_failure) + queue.remove(),
separate EXHAUSTED event, never more than max_attempts real attempts

STALE TIMER POLICY: discarded (STALE event, no mutation) on removed queue
entry, task-identity mismatch, task no longer RETRY_WAIT, or attempt-
generation mismatch

RETRY EVENT MODEL: SCHEDULED/READY/EXHAUSTED/STALE, drained via
drain_retry_events(), no infrastructure leakage

TESTS ADDED: 36 test functions (15 + 18 + 3) across 3 new files, plus a
fixture-server route addition

TESTS RUN: 473/473 passing

REAL HTTP TRANSIENT RETRY E2E: PASS

REAL RETRY EXHAUSTION E2E: PASS

ARTIFACT CONTINUITY: PASS

EXISTING TEST REGRESSIONS: NONE

BUGS FOUND: none in retry policy/runtime code; two test-authoring
mistakes found and fixed in this phase's own new tests (see "Bugs found"
above)

KNOWN LIMITATIONS: no durable retry deadlines, no Retry-After, no
HTTP-status-specific policy, no jitter, no per-host policy, no
connectivity-awareness, no manual GUI retry, no real pause/resume, no
persistence/crash recovery — all deferred

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt A7 — Download Progress / Speed / ETA
Runtime Model

COMMIT: (recorded after this phase's commit)
