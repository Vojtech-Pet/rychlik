# Scheduler / Dispatch Domain Policy (Prompt A3)

## Purpose

`SchedulerPolicy` composes `DownloadQueue` (A1) and `DownloadTask` (A2)
plus a transfer-capacity limit to answer exactly one question,
deterministically:

> Which READY queued tasks should receive the currently available
> transfer slots?

It must not: start HTTP downloads, mutate `DownloadTask`, mutate
`DownloadQueue`, create threads/workers, call `AcquisitionService`,
pause/resume sockets, retry automatically, persist state, or touch the
GUI. A3 only *decides*; a future coordinator phase (A4) *executes* the
resulting `DispatchPlan`.

## Inputs

```text
queue:  DownloadQueue   (A1)
tasks:  Mapping[task_id, DownloadTask]   (A2 snapshot)
config: SchedulerConfig  (max_active_transfers only)
```

## Outputs

```text
DispatchPlan
├── selected: tuple[DispatchCandidate, ...]   (queue_entry_id + task_id only)
├── active_transfer_count: int
├── available_slots_before_selection: int
└── remaining_slots_after_selection: int
```

## The candidate rule

```text
QueueEntry.state == QUEUED
and
DownloadTask.state == READY
and
an available transfer slot exists
=>
dispatch candidate
```

Deliberately **not** a new persistent lifecycle state — eligibility is a
computed relationship between two existing domains plus capacity, not a
third state machine. No `RUNNABLE`/`WAITING_FOR_SLOT`/`SCHEDULED` state was
added to either `QueueEntryState` or `DownloadTaskState`.

## What occupies a transfer slot

**Only `DownloadTaskState.TRANSFERRING` occupies an A3 transfer slot.**

```text
VERIFYING          does NOT occupy a slot
POST_PROCESSING    does NOT occupy a slot
DownloadTaskState.PAUSED   does NOT occupy a slot
CREATED/RESOLVING/READY/RETRY_WAIT/terminal states   do NOT occupy a slot
```

`active_transfer_count` is computed from the **entire task snapshot**, not
just tasks referenced by the queue — a `TRANSFERRING` task whose
`QueueEntry` has already been removed still consumes a slot (tested
explicitly: `test_transferring_task_without_queue_entry_still_consumes_slot`).

**Deferred, documented, not solved here**: because task-`PAUSED` doesn't
occupy a slot, but A3 also never authorizes *resuming* a paused transfer,
a future runtime resume operation must independently obey transfer-
capacity policy — A3 does not coordinate that.

## Capacity calculation

```text
available_slots = max(0, max_active_transfers - active_transfer_count)
```

Never negative. `max_active_transfers == 0` is a valid, explicit
"accept no new transfers" configuration, not an error. Over-capacity
(`active_transfer_count > max_active_transfers`) also just yields `0`
available slots — A3 never cancels or preempts an existing transfer.

## Queue/task composition and ordering

Candidate order comes **entirely** from `queue.eligible_entries()`
(A1's own QUEUED-only, canonical-order projection: priority descending,
then manual/stable position). `SchedulerPolicy` never re-sorts, never
assigns scheduling scores, and never touches `QueuePriority`. It only
*filters* that already-ordered sequence down to entries whose task is
`READY`, then takes the first `available_slots` of what remains.

## Missing-task policy

Any **non-REMOVED** `QueueEntry` (i.e. `QUEUED` or `PAUSED` —
`queue.active_entries()`) whose `task_id` is absent from the supplied
`tasks` snapshot raises `SchedulerSnapshotError` — silently skipping it
could starve that download indefinitely. A historical `REMOVED` entry
referencing a task no longer in the snapshot does **not** raise (A1 never
requires REMOVED entries to resolve).

A `READY` task with no `QueueEntry` at all is simply never a candidate —
A3 never auto-enqueues anything.

## Purity / no-mutation guarantee

`plan()` never assigns to any `QueueEntry` or `DownloadTask` field.
Verified by a dedicated test that snapshots relevant `QueueEntry` and
`DownloadTask` values before calling `plan()` and asserts they are
identical afterward (`test_planning_does_not_mutate_queue_or_tasks`).
Calling `plan()` twice on the same unmodified snapshot always returns an
equal `DispatchPlan` (`test_planning_is_deterministic`) — no timestamps,
no randomness, no hidden counters anywhere in this module.

## Known limitations (intentionally deferred)

```text
no real dispatch execution (A4)
no worker ownership, no threads
no networking, no AcquisitionService invocation
no pause/resume execution
no resume-capacity coordinator (see "what occupies a slot" above)
no retry timer/backoff/max-attempts (RETRY_WAIT->READY happens externally)
no per-host concurrency (A3 never inspects URLs/hostnames)
no bandwidth-aware scheduling
no CPU/GPU/post-processing resource scheduling
no persistence, no crash recovery
no scheduler loop/timer -- plan() is called on demand, once, per snapshot
no GUI
no fairness aging -- HIGH can starve NORMAL/LOW indefinitely by design;
  revisit only if real usage demonstrates a problem
```
