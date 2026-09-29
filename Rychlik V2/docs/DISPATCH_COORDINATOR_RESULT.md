# Dispatch Coordinator / Acquisition Runtime Bridge — Result (Prompt A4)

No concurrency exists. No worker pool exists. No scheduler loop exists.
No automatic retry exists. No actual transfer pause/resume exists. No
persistence/crash recovery exists.

## Baseline commit

`c5c6994` (verified via `git rev-parse HEAD` before starting; worktree
was clean).

## Files changed

```text
src/rychlik/core/dispatch_coordinator.py     (new)
tests/test_dispatch_coordinator.py           (new)
tests/test_dispatch_coordinator_e2e.py       (new)
docs/DISPATCH_COORDINATOR.md                 (new)
docs/DISPATCH_COORDINATOR_RESULT.md          (new, this file)
```

No existing file was modified — `SchedulerPolicy` remains untouched and
pure. `dispatch_coordinator.py` imports `rychlik.acquisition.*` and
`rychlik.core.{download_queue,download_task,scheduler_policy}` beyond
stdlib — no `PySide6`, `ShareLink`, `SharePreview`, or `LocalShareOrigin`,
verified by an AST-based structural test.

## Coordinator API

```text
DispatchCoordinator(acquisition_service, failure_mapper=default_failure_mapper)
  .dispatch(candidate, *, queue, tasks, requests, now,
            progress_callback=None, cancel_event=None) -> DispatchExecutionResult

DispatchOutcome: COMPLETED, RETRY_WAIT, FAILED, CANCELLED, STALE
DispatchExecutionResult: queue_entry_id, task_id, outcome,
  completed_download, failure, detail
DispatchCoordinatorError
├── DispatchConsistencyError   (structurally inconsistent candidate)
└── DispatchExecutionError     (unexpected exception, __cause__ chained)
default_failure_mapper(AcquisitionError) -> DownloadTaskFailure(retryable=False)
```

## Dispatch sequence

See `docs/DISPATCH_COORDINATOR.md` for the full numbered sequence:
resolve queue entry → verify identity → resolve task → revalidate
QUEUED+READY → resolve request → `start_transfer()` → `acquire()` → map
outcome.

## Revalidation rules

`STALE` (no mutation, no acquisition call): queue entry not `QUEUED`, task
not `READY`, old removed occurrence after re-enqueue, already-terminal
occurrence dispatched twice. `DispatchConsistencyError` (raised, not
returned): unknown `queue_entry_id`, unknown `task_id`, queue-entry/
candidate task_id mismatch, no registered `DownloadRequest`.

## Outcome model

`COMPLETED` / `RETRY_WAIT` / `FAILED` / `CANCELLED` / `STALE`. Terminal
outcomes (`COMPLETED`/`FAILED`/`CANCELLED`) remove the `QueueEntry`.
`RETRY_WAIT` deliberately does not — see queue-entry policy below.

## Queue-entry policy (as locked in before this phase)

`QueueEntry` stays `QUEUED` for the entire duration of `TRANSFERRING` —
no new `CLAIMED`/`RUNNING` queue state was introduced, because A3's
candidate rule (`QUEUED` + `READY`) already prevents a `TRANSFERRING` task
from being re-selected. Removed only on `COMPLETED`/`FAILED`/`CANCELLED`.
For `RETRY_WAIT`, the same queue occurrence (same `queue_entry_id`, same
position, same priority) survives — proven end-to-end
(`test_retry_preserves_position_among_siblings`, `test_retry_composition`).

## Acquisition bridge

Reuses the existing `AcquisitionService`/`DirectHttpAcquisition` (Prompt
04.5) completely unmodified. The coordinator never touches `.part`
internals, temp-file handling, or atomic rename — that remains entirely
`AcquisitionService`'s responsibility. `progress_callback`/`cancel_event`
are passed straight through, reusing the existing contract.

## Failure mapper

`default_failure_mapper` maps any `AcquisitionError` to
`DownloadTaskFailure(code="ACQUISITION_ERROR", message=str(exc),
retryable=False)` — a conservative default per §25 (no text-matching
heuristics on exception messages to guess retryability). Callers may
supply a different mapper (tested with a custom retryable-mapping
function) once a real failure taxonomy exists.

## Tests added

27 tests in `tests/test_dispatch_coordinator.py` (fake, deterministic
acquisition service) plus 5 real, non-mock end-to-end tests in
`tests/test_dispatch_coordinator_e2e.py` against the existing local HTTP
fixture server and the real `AcquisitionService`/`DirectHttpAcquisition`.
Covers every §80 required group: successful dispatch, state-during-
acquisition ordering proof, cancellation, non-retryable and retryable
failure mapping, retry position preservation across siblings, terminal-
vs-retry queue cleanup, unexpected-exception safety (task never stuck
TRANSFERRING, no raw exception stored), stale-queue-paused,
stale-removed-entry, re-enqueue identity safety, task-changed-after-plan,
candidate identity mismatch, unknown task/queue-entry/request, double-
dispatch prevention, retry-as-new-attempt (not double dispatch),
scheduler→coordinator composition, retry composition, queue-pause
composition, and the structural no-forbidden-imports test.

## Tests run

404/404 passing (32 new; all 372 pre-existing tests remain green,
unchanged).

## Real HTTP success E2E

`test_real_http_success_e2e`: real `AcquisitionService` against the local
fixture server's `/normal.mp4` route through the full
`DispatchCoordinator.dispatch()` path — `COMPLETED`, physical file bytes
verified byte-for-byte, `QueueEntry` removed. Artifact continuity proven
separately (`test_real_http_success_e2e_artifact_continuity`): the
returned `CompletedDownload` still feeds `Artifact.from_completed_download()`
exactly as Prompt 04.5 established, without the coordinator taking on
Artifact-creation responsibility.

## Real HTTP failure E2E

`test_real_http_404_failure_e2e` and `test_real_http_500_failure_e2e`:
both real HTTP error responses map through the real
`AcquisitionService`/`DirectHttpAcquisition` → `AcquisitionError` →
`default_failure_mapper` (retryable=False) → `FAILED`, `QueueEntry`
removed, no partial file left behind.

## Real cancellation E2E

`test_real_cancellation_e2e`: **PASS**, using the existing (already-
supported since Prompt 04.5) `cancel_event` contract against the `/slow`
fixture route, with a background thread flipping the cancel signal mid-
transfer — no worker thread was introduced inside the coordinator itself,
only in the test to simulate a concurrent cancel request.

## Existing test regressions

NONE.

## Known limitations

See `docs/DISPATCH_COORDINATOR.md` "Known limitations" — synchronous
one-candidate execution only, no parallelism, no background worker/
scheduler loop, no automatic retry timer/backoff, no per-host/bandwidth
scheduling, no real mid-transfer pause/resume, no persistence/crash
recovery, no Artifact-creation ownership in the coordinator (deliberate),
no GUI integration: all explicitly deferred, none solved here.

---

PHASE: Prompt A4 — Dispatch Coordinator / Acquisition Runtime Bridge

STATUS: DONE

BASELINE COMMIT: c5c6994

FILES CHANGED: src/rychlik/core/dispatch_coordinator.py (new),
tests/test_dispatch_coordinator.py (new),
tests/test_dispatch_coordinator_e2e.py (new),
docs/DISPATCH_COORDINATOR.md (new), docs/DISPATCH_COORDINATOR_RESULT.md (new)

COORDINATOR TYPES: DispatchCoordinator, DispatchOutcome,
DispatchExecutionResult, DispatchCoordinatorError + 2 subtypes,
default_failure_mapper

DISPATCH SEQUENCE: resolve queue entry → verify identity → resolve task →
revalidate QUEUED+READY → resolve request → start_transfer() → acquire()
→ map outcome (see full sequence in docs/DISPATCH_COORDINATOR.md)

STALE-PLAN POLICY: STALE outcome (no mutation, no acquisition call) for
ordinary state drift; DispatchConsistencyError raised for structurally
inconsistent candidates

QUEUE-ENTRY POLICY: stays QUEUED throughout TRANSFERRING and RETRY_WAIT;
removed only on COMPLETED/FAILED/CANCELLED

ACQUISITION BRIDGE: existing AcquisitionService/DirectHttpAcquisition
(Prompt 04.5) reused unmodified; coordinator never touches .part internals

SUCCESS PATH: READY+QUEUED → TRANSFERRING → CompletedDownload → COMPLETED
→ QueueEntry REMOVED

CANCELLATION PATH: DownloadCancelled → CANCELLED (no fabricated failure)
→ QueueEntry REMOVED

RETRYABLE FAILURE PATH: AcquisitionError (retryable=True) → RETRY_WAIT →
QueueEntry preserved at same position/priority

PERMANENT FAILURE PATH: AcquisitionError (retryable=False, the
conservative default) → FAILED → QueueEntry REMOVED

UNEXPECTED EXCEPTION PATH: task forced to FAILED + QueueEntry REMOVED
before DispatchExecutionError is raised with __cause__ chained; raw
exception never stored in DownloadTaskFailure

FAILURE MAPPER: default_failure_mapper, conservative retryable=False, no
text-matching heuristics; pluggable via constructor

TESTS ADDED: 32 (27 + 5 real E2E)

TESTS RUN: 404/404 passing

REAL HTTP SUCCESS E2E: PASS (byte-exact file, Artifact continuity proven)

REAL HTTP FAILURE E2E: PASS (404 and 500, both FAILED, no partial file)

REAL CANCELLATION E2E: PASS (existing cancel_event contract, background
thread only in the test, not inside the coordinator)

EXISTING TEST REGRESSIONS: NONE

KNOWN LIMITATIONS: synchronous single-candidate execution, no
parallelism, no worker/scheduler loop, no retry timer, no real
pause/resume, no persistence, no GUI — all deferred to A5+

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt A5 — Concurrent Worker Runtime / Scheduler
Loop

COMMIT: (recorded after this phase's commit)
