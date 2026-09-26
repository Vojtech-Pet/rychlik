# Queue Domain Model (Prompt A1)

## Purpose

`DownloadQueue` answers exactly one question, deterministically:

> Which download tasks are in the queue, in what order, with what
> priority, and which of them are currently eligible?

It does **not** answer: which one should start now, how many may run
concurrently, how a task is downloaded, or what happens when it fails.

## Boundary vs. download lifecycle

`QueueEntry` references a future download/task object only through an
opaque `task_id` string. The queue never resolves, inspects, or mutates
that task — no URL, no HTTP transfer, no Artifact, no acquisition code is
imported by `rychlik/core/download_queue.py`. `QueueEntryState` therefore
has nothing to do with transfer state: it only answers "does this entry
belong to the queue, and is it currently held?"

```text
QueueEntry
     │
     │ task_id (opaque)
     ▼
(future) DownloadTask / Task-Lifecycle domain  — not part of A1
```

## QueueEntry fields

```text
queue_entry_id   — distinct from task_id (§5); a UUID
task_id          — opaque; validated only for non-empty string
state             — QueueEntryState
priority          — QueuePriority
position          — int, contiguous 0..n-1 within its priority band
enqueued_at       — timezone-aware UTC datetime
updated_at        — timezone-aware UTC datetime
paused_at         — timezone-aware UTC datetime | None
```

No speculative fields: no download speed, retry count, URL, destination
path, progress, ETA, worker_id, or scheduler score.

## State machine

```text
QUEUED  → PAUSED   (pause)
QUEUED  → REMOVED  (remove)
PAUSED  → QUEUED   (resume)
PAUSED  → REMOVED  (remove)
REMOVED → (terminal, no outgoing transitions)
```

| From | Operation | To | Allowed |
|---|---|---|---|
| QUEUED | pause | PAUSED | yes |
| QUEUED | remove | REMOVED | yes |
| QUEUED | resume | QUEUED | yes (idempotent no-op) |
| PAUSED | resume | QUEUED | yes |
| PAUSED | remove | REMOVED | yes |
| PAUSED | pause | PAUSED | yes (idempotent no-op) |
| REMOVED | resume | — | no (`InvalidQueueTransitionError`) |
| REMOVED | pause | — | no (`InvalidQueueTransitionError`) |
| REMOVED | remove | REMOVED | yes (idempotent no-op) |

A removed entry can never be resurrected. To queue the same `task_id`
again, call `enqueue()` again — it produces a brand-new `queue_entry_id`.

## Priority semantics

Named enum only: `LOW` (100) `< NORMAL` (200) `< HIGH` (300). Canonical
ordering is `HIGH` before `NORMAL` before `LOW`. No dynamic aging, no
deadline priority, no bandwidth-aware priority, no scheduler weights —
all deferred.

## Canonical ordering

```text
1. priority rank descending
2. position ascending
3. enqueued_at ascending
4. queue_entry_id (tie-break)
```

Positions are kept contiguous and unique within a band by the aggregate
(`_renumber`), so (3) and (4) are a safety net for malformed/imported
state, not the primary ordering mechanism.

## Manual reorder semantics

`move_before(entry_id, target_entry_id)` / `move_after(...)` operate only
**within the same priority band**. A cross-priority attempt raises
`CrossPriorityReorderError` — to move an entry to a different band, call
`set_priority()` instead. This keeps manual position and priority ordering
from interacting ambiguously. Moving relative to oneself, or involving a
REMOVED entry on either side, raises `InvalidQueueOperationError`.

## Duplicate-task policy

At most one **live** (non-REMOVED) `QueueEntry` may exist per `task_id` at
a time. `enqueue()` for a `task_id` that already has a QUEUED or PAUSED
entry raises `DuplicateQueuedTaskError`.

## Re-enqueue policy

Valid: `task-42` → entry A → REMOVED → later, `task-42` → entry B →
QUEUED. `A.queue_entry_id != B.queue_entry_id` always. The old REMOVED
entry stays in `all_entries()` history but never affects the duplicate
check or ordering.

## Idempotency policy

- `pause()` on an already-PAUSED entry: success, no mutation, `updated_at`
  unchanged.
- `resume()` on an already-QUEUED entry: success, no mutation, `updated_at`
  unchanged.
- `remove()` on an already-REMOVED entry: success, no mutation, `updated_at`
  unchanged.
- `set_priority()` to the entry's current priority: success, no mutation,
  no move to end of band, `updated_at` unchanged.
- Any other operation against a REMOVED entry (`resume`, `pause`,
  `set_priority`, `move_before`/`move_after`) is rejected, not silently
  accepted.

`updated_at` is bumped only by the entry an operation directly targets.
When removing/moving an entry shifts its siblings' `position` to keep a
band contiguous, those siblings' `updated_at` is **not** touched — they
were not the target of an explicit operation.

## Active vs. eligible projections

- `active_entries()` — QUEUED + PAUSED, canonical order. What a future
  queue UI may display.
- `eligible_entries()` — QUEUED only, canonical order. Read-only
  projection for a future scheduler; calling it dispatches nothing.
- `all_entries()` — every entry ever created, including REMOVED. History/
  audit only, never used for scheduling.

All three return an immutable `tuple` of frozen `QueueEntry` instances —
external code cannot mutate the projection or corrupt aggregate state by
holding a reference to it.

## Known limitations (intentionally deferred to A2+)

```text
scheduler
dispatch/claim semantics
parallelism / concurrency limits
running-task lifecycle
actual HTTP transfer pause/resume
retry / retry-wait
dependencies between downloads
scheduled (time-delayed) downloads
bandwidth allocation
queue persistence / crash recovery
drag-and-drop GUI
priority aging / automatic priority
thread safety (locks, asyncio) — single-process domain logic only
```
