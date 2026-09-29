# Scheduler / Dispatch Domain Policy — Result (Prompt A3)

`SchedulerPolicy` does not mutate `QueueEntry`. `SchedulerPolicy` does not
mutate `DownloadTask`. `SchedulerPolicy` does not call `AcquisitionService`.
No workers or threads exist.

## Baseline commit

`f69d717` (verified via `git rev-parse HEAD` before starting; worktree
was clean).

## Files changed

```text
src/rychlik/core/scheduler_policy.py  (new)
tests/test_scheduler_policy.py        (new)
docs/SCHEDULER_POLICY.md              (new)
docs/SCHEDULER_POLICY_RESULT.md       (new, this file)
```

No existing file was modified. `scheduler_policy.py` imports only
`rychlik.core.download_queue` and `rychlik.core.download_task` beyond
stdlib (`dataclasses`, `typing`) — verified by a dedicated AST-based test
(`test_module_has_no_forbidden_imports`) that parses the module's actual
import statements (not a naive text grep, which would have false-positived
on the module's own docstring explicitly naming what must *not* be
imported).

## Scheduler types

```text
SchedulerConfig (frozen dataclass): max_active_transfers
DispatchCandidate (frozen dataclass): queue_entry_id, task_id
DispatchPlan (frozen dataclass): selected, active_transfer_count,
  available_slots_before_selection, remaining_slots_after_selection
SchedulerPolicy (stateless class): plan(*, queue, tasks, config) -> DispatchPlan
SchedulerDomainError (base)
├── InvalidSchedulerConfigError
└── SchedulerSnapshotError
```

## Candidate rule

`QueueEntry.state == QUEUED` and `DownloadTask.state == READY` and an
available transfer slot exists. No new persistent lifecycle state was
added to either A1 or A2 — eligibility is a computed relationship only.

## Transfer slot semantics

Only `DownloadTaskState.TRANSFERRING` occupies a slot, counted across the
**entire** task snapshot (not just tasks referenced by the queue) — a
`TRANSFERRING` task with no `QueueEntry` still consumes capacity.
`VERIFYING`, `POST_PROCESSING`, and task-`PAUSED` all explicitly do not
occupy a slot. See `docs/SCHEDULER_POLICY.md` for the deferred
resume-capacity-coordination limitation this implies.

## Max concurrency / active transfer count

`available_slots = max(0, max_active_transfers - active_transfer_count)`,
never negative. `max_active_transfers == 0` and over-capacity both
correctly yield zero selectable slots without error and without cancelling
anything.

## Ordering behavior

Entirely delegated to `queue.eligible_entries()` (A1's own canonical
QUEUED-only ordering: priority descending, then manual/stable position).
`SchedulerPolicy` filters by task readiness only — it never re-sorts,
never assigns scores, never touches `QueuePriority`.

## Missing-task behavior

Any non-REMOVED (`QUEUED` or `PAUSED`) `QueueEntry` referencing a
`task_id` absent from the supplied snapshot raises `SchedulerSnapshotError`
— fails loudly rather than silently starving a download. Historical
`REMOVED` entries never trigger this check.

## Tests added

25 tests in `tests/test_scheduler_policy.py`, covering every §47–68
requirement: basic dispatch order, priority order, manual A1 order,
queue-pause exclusion, exhaustive task-state filtering (all 10 non-READY
states plus READY, parametrized via one table), active-capacity limiting,
the critical "TRANSFERRING task without a QueueEntry still consumes a
slot" architectural test, VERIFYING/POST_PROCESSING/task-PAUSED
non-occupancy, zero-capacity and over-capacity (no negative slots, no
cancellation), negative-config rejection, missing-task snapshot errors
(live vs. historical REMOVED vs. PAUSED), extra-READY-task-without-queue-
entry exclusion, determinism, the no-mutation guarantee, priority-change-
between-plans and queue-pause/resume-between-plans reflecting correctly in
re-planned output, RETRY_WAIT exclusion until externally marked ready
again, the full §67 complex scenario, a 100-entry bounded stress scenario,
and the structural no-forbidden-imports test.

## Tests run

372/372 passing (25 new; all 347 pre-existing tests remain green,
unchanged).

## Existing test regressions

NONE.

## Known limitations

See `docs/SCHEDULER_POLICY.md` "Known limitations" — no real dispatch
execution, no worker/thread ownership, no networking, no pause/resume
execution or resume-capacity coordination, no retry timer/backoff, no
per-host or bandwidth-aware scheduling, no CPU/GPU/post-processing
resource scheduling, no persistence/crash recovery, no scheduler loop, no
GUI, no fairness aging: all explicitly deferred, none solved here.

---

PHASE: Prompt A3 — Scheduler / Dispatch Domain Policy

STATUS: DONE

BASELINE COMMIT: f69d717

FILES CHANGED: src/rychlik/core/scheduler_policy.py (new),
tests/test_scheduler_policy.py (new), docs/SCHEDULER_POLICY.md (new),
docs/SCHEDULER_POLICY_RESULT.md (new)

SCHEDULER TYPES: SchedulerConfig, DispatchCandidate, DispatchPlan,
SchedulerPolicy, SchedulerDomainError + 2 subtypes

CANDIDATE RULE: QueueEntry.QUEUED + DownloadTask.READY + available slot

TRANSFER SLOT SEMANTICS: only TRANSFERRING occupies a slot, counted across
the whole task snapshot; VERIFYING/POST_PROCESSING/task-PAUSED do not

MAX CONCURRENCY: SchedulerConfig.max_active_transfers, rejects negative
values via InvalidSchedulerConfigError

ACTIVE TRANSFER COUNT: computed from complete task snapshot, independent
of queue membership

CANONICAL ORDERING: delegated entirely to queue.eligible_entries(); no
re-sorting, no scoring

QUEUE/TASK COMPOSITION: computed relationship only, no new persistent
lifecycle state added to A1 or A2

MISSING TASK POLICY: SchedulerSnapshotError for any non-REMOVED QueueEntry
referencing an absent task_id; REMOVED entries exempt

PURITY / NO-MUTATION: verified by dedicated before/after snapshot test;
determinism verified by repeated-call equality test

TESTS ADDED: 25

TESTS RUN: 372/372 passing

EXISTING TEST REGRESSIONS: NONE

KNOWN LIMITATIONS: no dispatch execution, no threads/networking, no
resume-capacity coordination, no retry timer, no per-host/bandwidth
scheduling, no persistence, no GUI, no fairness aging — all deferred to A4+

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt A4 — Dispatch Coordinator / Acquisition
Runtime Bridge

COMMIT: (recorded after this phase's commit)
