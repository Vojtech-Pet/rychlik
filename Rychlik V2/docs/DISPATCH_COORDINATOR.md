# Dispatch Coordinator / Acquisition Runtime Bridge (Prompt A4)

No concurrency exists. No worker pool exists. No scheduler loop exists.
No automatic retry exists. No actual transfer pause/resume exists. No
persistence/crash recovery exists.

## Responsibility boundary: A3 vs A4

```text
DownloadQueue + DownloadTask + capacity
        │
        ▼
SchedulerPolicy.plan()   -- A3, pure, zero side effects
        │
        ▼
DispatchPlan / DispatchCandidate
        │
        ▼
DispatchCoordinator.dispatch()  -- A4, real side effects, ONE candidate
        │
        ▼
DownloadTask READY -> TRANSFERRING
        │
        ▼
AcquisitionService (Prompt 04.5, unchanged)
        │
        ▼
CompletedDownload / AcquisitionError / DownloadCancelled
        │
        ▼
DownloadTask lifecycle update + DownloadQueue cleanup
```

`SchedulerPolicy` was not modified and is never imported for execution —
`DispatchCoordinator` only consumes an already-produced `DispatchCandidate`.
It does not decide which candidate should run, does not rescore or
reorder, and does not recompute `available_slots`/`max_active_transfers`
(that remains entirely A3's job).

## Required contract gap closed here

No existing relationship connected a `DownloadTask` to the
`DownloadRequest` it should download — `DownloadTask.task_id` is fully
opaque by A2's own design. Rather than inventing a second competing
request/task model, `dispatch()` accepts an explicit
`requests: Mapping[task_id, DownloadRequest]` snapshot, mirroring the
`tasks: Mapping[task_id, DownloadTask]` shape `SchedulerPolicy` already
takes. No persistent registry/database was introduced.

## Dispatch sequence

```text
1. resolve QueueEntry by queue_entry_id           (unknown -> DispatchConsistencyError)
2. verify entry.task_id == candidate.task_id      (mismatch -> DispatchConsistencyError)
3. resolve DownloadTask by task_id                (unknown -> DispatchConsistencyError)
4. revalidate: entry.state == QUEUED               (else -> STALE, no mutation)
5. revalidate: task.state == READY                 (else -> STALE, no mutation)
6. resolve DownloadRequest by task_id              (unknown -> DispatchConsistencyError)
7. task.start_transfer()                           (attempt_count += 1, task -> TRANSFERRING)
8. AcquisitionService.acquire(request, ...)        (real side effect, synchronous, blocking)
9. map result -> DownloadTask transition + DownloadQueue cleanup (see paths below)
```

Step 7 happens strictly before step 8 — tested directly
(`test_task_is_transferring_during_acquisition`, via a fake acquisition
service that inspects task state from inside `acquire()`).

## Stale-plan revalidation

A `DispatchPlan` is a decision over a past snapshot. Anything that drifted
between planning and dispatch is `STALE`, not an error: no domain
mutation, `AcquisitionService` never called. Covered explicitly:

```text
queue entry paused after planning
queue entry removed after planning
task cancelled/changed after planning
old (removed) queue occurrence after the same task was re-enqueued as a
  new occurrence -- resolved strictly by queue_entry_id, never by task_id
already-terminal occurrence dispatched a second time (double-dispatch
  prevention: AcquisitionService is not called again)
```

Structurally inconsistent candidates (unknown `queue_entry_id`, unknown
`task_id`, a queue entry that belongs to a *different* task than the
candidate claims, or a task with no registered `DownloadRequest`) raise
`DispatchConsistencyError` instead — these are not ordinary state drift,
they indicate the candidate itself cannot be trusted.

## Queue-entry policy (locked in before this phase started)

**`QueueEntry` stays `QUEUED` while `DownloadTask` is `TRANSFERRING`.** No
new queue state (`CLAIMED`/`RUNNING`) was added. This works because A3's
candidate rule already requires *both* `QueueEntry == QUEUED` *and*
`DownloadTask == READY` — a `TRANSFERRING` task can never be re-selected,
so keeping its `QueueEntry` alive cannot cause a double dispatch.

`QueueEntry` is removed (`queue.remove()`) only on a terminal task outcome:
`COMPLETED`, `FAILED`, or `CANCELLED`. For `RETRY_WAIT` it remains active,
preserving priority and manual position exactly — a retried task returns
to A3 planning at the same queue position it already held, not at the
back of the line. Proven end-to-end
(`test_retry_preserves_position_among_siblings`,
`test_retry_composition`).

## Success path

```text
READY + QUEUED -> start_transfer() -> TRANSFERRING
  -> AcquisitionService.acquire() -> CompletedDownload
  -> task.complete() -> COMPLETED
  -> queue.remove() -> REMOVED
  -> DispatchExecutionResult(COMPLETED, completed_download=...)
```

`CompletedDownload` is returned unchanged in the result — the coordinator
does not create an `Artifact`, does not touch `ArtifactRepository`, and
does not touch Share by Link. A later orchestration layer is expected to
do `Artifact.from_completed_download(result.completed_download.final_path,
...)`, exactly as Prompt 04.5 already established; proven not to have
regressed by `test_real_http_success_e2e_artifact_continuity`.

## Retryable failure path

```text
AcquisitionError, mapped with retryable=True
  -> task.wait_for_retry(failure) -> RETRY_WAIT
  -> QueueEntry NOT removed, stays QUEUED at its existing position
  -> DispatchExecutionResult(RETRY_WAIT, failure=...)
```

No automatic retry is scheduled. A future retry-policy runtime decides
when to call `task.mark_retry_ready()`.

## Permanent failure path

```text
AcquisitionError, mapped with retryable=False (the conservative default)
  -> task.fail(failure) -> FAILED
  -> queue.remove() -> REMOVED
  -> DispatchExecutionResult(FAILED, failure=...)
```

## Cancellation path

```text
DownloadCancelled (a subtype of AcquisitionError, caught first)
  -> task.cancel() -> CANCELLED (no fabricated failure, last_failure untouched)
  -> queue.remove() -> REMOVED
  -> DispatchExecutionResult(CANCELLED)
```

## Unexpected exception path

Any exception outside the documented `AcquisitionError` contract:

```text
1. build a DownloadTaskFailure(code="ACQUISITION_RUNTIME_ERROR",
   message=str(exc), retryable=False) -- never the raw exception object
2. task.fail(failure) -> FAILED
3. queue.remove() -> REMOVED
4. raise DispatchExecutionError(...) from exc
```

The task is never left `TRANSFERRING` — verified directly
(`test_unexpected_exception_fails_task_and_removes_queue_then_raises`).
The original exception is reachable via `DispatchExecutionError.__cause__`
for logging/debugging, but `DownloadTask.last_failure` structurally cannot
hold it (`DownloadTaskFailure` has exactly three fields: `code`,
`message`, `retryable` — verified by a dedicated field-set assertion).

## Queue cleanup ordering and non-atomicity

```text
task becomes terminal  ->  queue.remove()
```

is **not** an atomic transaction. If `queue.remove()` were to fail after a
successful acquisition and `task.complete()`, the physically downloaded
file and the `COMPLETED` task state both already exist and must not be
discarded to manufacture atomicity — that would be worse than a stale
queue occurrence. A4 does not implement automatic reconciliation for this
window; it is documented, not solved, deferred to a future
persistence/crash-recovery phase.

## Crash windows (documented, not solved)

```text
task marked TRANSFERRING -> process crashes before acquisition begins
physical file finalized -> process crashes before task.complete()
task COMPLETED -> process crashes before queue.remove()
```

None of these are recoverable today — all state is in-memory only. This
will inform the design of a future persistence/crash-recovery phase.

## Partial file ownership

The coordinator never touches `.part` file internals — `AcquisitionService`
/ `DirectHttpAcquisition` (Prompt 04.5) exclusively owns the temporary
file, atomic `os.replace`, and `CompletedDownload` construction. A4 only
orchestrates lifecycle transitions around that existing, unmodified
acquisition boundary.

## Progress and cancellation

`dispatch()` passes an optional `progress_callback` and `cancel_event`
straight through to `AcquisitionService.acquire()` — reusing the existing
Prompt 04.5 contract rather than inventing a new one. While a progress
callback executes, `DownloadTask.state` is guaranteed `TRANSFERRING`. No
GUI dependency exists in this module (no `PySide6` import — verified by an
AST-based structural test).

## Synchronous execution (explicit limitation)

`dispatch()` blocks for the full duration of one acquisition attempt. This
is intentional for A4 — it proves the domain/runtime bridge correctly —
and is explicitly **not** the final application execution model. No
threads, no `asyncio`, no worker pool exist in this module.

## Known limitations

```text
synchronous, one-candidate-at-a-time execution only
no parallelism across multiple selected candidates
no background worker / scheduler loop
no automatic retry timer or exponential backoff
no per-host concurrency limits
no bandwidth management
no real mid-transfer pause/resume (DownloadTaskState.PAUSED exists in A2
  but nothing in A4 can actually reach it -- every synchronous attempt
  here ends in COMPLETED/RETRY_WAIT/FAILED/CANCELLED)
no persistence, no crash recovery
no Artifact creation ownership inside the coordinator (deliberate)
no GUI integration
```
