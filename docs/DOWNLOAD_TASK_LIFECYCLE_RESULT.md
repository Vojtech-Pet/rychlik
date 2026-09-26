# Download Task Lifecycle — Result (Prompt A2)

No scheduler exists. No task automatically starts. No networking is
performed by `DownloadTask`. No automatic retry exists. No real
pause/resume implementation exists. No GUI integration exists.

## Baseline commit

`d5abb66` (verified via `git rev-parse HEAD` before starting; worktree
was clean).

## Files changed

```text
src/rychlik/core/download_task.py          (new)
tests/test_download_task.py                (new)
docs/DOWNLOAD_TASK_LIFECYCLE.md            (new)
docs/DOWNLOAD_TASK_LIFECYCLE_RESULT.md     (new, this file)
```

No existing file was modified. `download_task.py`'s only imports are
`dataclasses`, `datetime`, `enum` — verified structurally by a dedicated
test (`test_module_has_no_forbidden_imports`) that greps its own source
for `PySide6`/`requests`/`yt_dlp`.

## Domain types

```text
DownloadTaskState (Enum): CREATED, RESOLVING, READY, TRANSFERRING, PAUSED,
  RETRY_WAIT, VERIFYING, POST_PROCESSING, COMPLETED, FAILED, CANCELLED
DownloadTaskFailure (frozen dataclass): code, message, retryable
DownloadTask (frozen dataclass)
DownloadTaskDomainError (base)
├── InvalidTaskTransitionError
├── InvalidRetryFailureError
└── InvalidTaskOperationError
create_task(task_id, *, now) -> DownloadTask
```

## State machine

See `docs/DOWNLOAD_TASK_LIFECYCLE.md` for the complete transition table.
Summary: `QUEUED` is never a task state (kept in a separate module
entirely); `PAUSED` only reachable from `TRANSFERRING`; three terminal
states (`COMPLETED`/`FAILED`/`CANCELLED`), none resurrectable.

## Terminal states

`COMPLETED`, `FAILED`, `CANCELLED`. Same-operation-same-state calls are
idempotent no-ops (e.g. `cancel()` on an already-`CANCELLED` task);
calling a *different* terminal-producing operation on an already-terminal
task (e.g. `fail()` on `COMPLETED`) raises `InvalidTaskTransitionError`.

## Failure model

`DownloadTaskFailure(code, message, retryable)` — pure value object, never
holds raw exceptions/tracebacks/responses. `FAILED` state structurally
requires `last_failure is not None` (enforced in `__post_init__`, cannot
even construct an invalid instance). `wait_for_retry()` requires
`failure.retryable`; a non-retryable failure raises
`InvalidRetryFailureError` and must go through `fail()` instead.

## Attempt semantics

`attempt_count` increments only on `READY -> TRANSFERRING` (a genuinely
new attempt). Pause/resume of the same attempt never increments it. A
retry cycle (`RETRY_WAIT -> READY -> TRANSFERRING`) does increment it.
`RESOLVING`/`VERIFYING`/`POST_PROCESSING` never affect it.

## Pause/resume semantics

`pause_transfer()` only from `TRANSFERRING`; `READY -> PAUSED` is
rejected by design (that's the queue's job, via `QueueEntry.pause()`, a
completely separate domain). `resume_transfer()` only from `PAUSED`,
preserves `attempt_count` and `started_at`.

## Retry semantics

`wait_for_retry(failure)` from `RESOLVING | TRANSFERRING | VERIFYING`
only; no delay/backoff/timer/max-attempts — those are explicitly out of
scope, deferred to a future retry-policy runtime that decides *when* to
call `mark_ready()`.

## Timestamp semantics

`created_at` set once, never mutated. `updated_at` bumped only by real
mutations. `started_at` set once on the *first* entry into `TRANSFERRING`,
survives pause/resume/retry. `finished_at` set exactly once, only on
entering a terminal state. All timezone-aware; naive datetimes rejected.
`now` is always an explicit required argument (no hidden global clock),
matching `ShareLink`'s Prompt 06 convention.

## Queue/task separation

Verified by a dedicated structural test
(`test_task_ready_and_queue_paused_is_a_valid_independent_combination`):
a `READY` `DownloadTask` paired with a `PAUSED` `QueueEntry` for the same
`task_id` is valid and each domain's operations leave the other
completely untouched — resuming the queue entry doesn't change the task,
and pausing the task's transfer doesn't change the queue entry.

## Tests added

62 tests in `tests/test_download_task.py`: basic state transitions, all
four optional pipeline shapes (direct / verify / post-process / full),
pause/resume attempt-count and started_at invariants, the full retry
cycle (attempt count, started_at preservation, last_failure retention),
non-retryable-failure rejection, failure from every nonterminal phase
(parametrized), cancellation from every nonterminal phase (parametrized,
proving no fabricated failure), terminal-state immutability against
`mark_ready`/`start_transfer`/`resume_transfer`/`start_verification`/
`cancel`/`fail`/`complete` cross-attempts, timestamp timezone-awareness
and idempotency behavior, attempt-count non-increment for
resolving/verifying/post-processing, the FAILED-requires-last_failure
construction invariant, query-helper properties, the queue/task
independence test, the structural no-forbidden-imports test, the full
§68 end-to-end scenario, and the §69 invalid-transition matrix.

One test bug was found and fixed during this phase (not a domain-model
bug): a parametrized cancellation test asserted `last_failure is None`
across all builds, but one build legitimately carries a real
pre-existing `last_failure` from an earlier `wait_for_retry()` step before
cancellation — the assertion was corrected to compare
`cancelled.last_failure == task.last_failure` (cancel must not fabricate
or alter existing failure history, not that failure history must always
be absent).

## Tests run

347/347 passing (62 new; all 285 pre-existing tests remain green,
unchanged).

## Existing test regressions

NONE.

## Known limitations

See `docs/DOWNLOAD_TASK_LIFECYCLE.md` "Known limitations" — scheduler,
dispatch/claim, worker ownership, concurrency limits, actual HTTP
pause/resume, safe Range resume, retry delay/backoff/max-attempts,
progress/speed/ETA, persistence/crash recovery, automatic
DownloadQueue↔DownloadTask coordination, GUI: all explicitly deferred,
none solved here.

---

PHASE: Prompt A2 — Download Task Lifecycle Domain Model

STATUS: DONE

BASELINE COMMIT: d5abb66

FILES CHANGED: src/rychlik/core/download_task.py (new),
tests/test_download_task.py (new), docs/DOWNLOAD_TASK_LIFECYCLE.md (new),
docs/DOWNLOAD_TASK_LIFECYCLE_RESULT.md (new)

DOMAIN TYPES: DownloadTaskState, DownloadTaskFailure, DownloadTask,
DownloadTaskDomainError + 3 subtypes, create_task()

TASK STATES: CREATED, RESOLVING, READY, TRANSFERRING, PAUSED, RETRY_WAIT,
VERIFYING, POST_PROCESSING, COMPLETED, FAILED, CANCELLED

STATE TRANSITIONS: see full table in docs/DOWNLOAD_TASK_LIFECYCLE.md

TERMINAL STATES: COMPLETED, FAILED, CANCELLED — none resurrectable;
same-state idempotent, cross-terminal rejected

FAILURE MODEL: DownloadTaskFailure(code, message, retryable); FAILED
structurally requires last_failure; RETRY_WAIT requires retryable=True

PAUSE SEMANTICS: task-pause only from TRANSFERRING, independent of
QueueEntryState.PAUSED (dedicated test proves independence)

RETRY SEMANTICS: wait_for_retry from RESOLVING/TRANSFERRING/VERIFYING;
no delay/backoff/max-attempts modeled

ATTEMPT COUNT SEMANTICS: increments only on READY->TRANSFERRING;
pause/resume and resolving/verifying/post-processing never increment

TIMESTAMP SEMANTICS: created_at immutable; updated_at on real mutation
only; started_at set once on first TRANSFERRING; finished_at once on
terminal; all timezone-aware; explicit `now` argument throughout

QUEUE/TASK SEPARATION: verified structurally; READY task + PAUSED queue
entry is valid and neither domain mutates the other

TESTS ADDED: 62

TESTS RUN: 347/347 passing

EXISTING TEST REGRESSIONS: NONE

KNOWN LIMITATIONS: scheduler, dispatch/claim, concurrency, actual
pause/resume, retry policy, persistence, GUI — all deferred to A3+

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt A3 — Scheduler / Dispatch Domain Policy

COMMIT: (recorded after this phase's commit)
