# Queue Domain Model — Result (Prompt A1)

No scheduler exists. No download execution is triggered by `DownloadQueue`.
No persistence exists. No GUI exists for the queue.

## Baseline commit

`22ed6b3` (verified via `git rev-parse HEAD` before starting; worktree was
clean).

## Files changed

```text
src/rychlik/core/download_queue.py  (new)
tests/test_download_queue.py        (new)
docs/QUEUE_DOMAIN_MODEL.md          (new)
docs/QUEUE_DOMAIN_RESULT.md         (new, this file)
```

No existing file was modified. `DownloadQueue` imports nothing from
`rychlik.acquisition`, `rychlik.share`, or PySide6 — verified by
construction (the module's only imports are `uuid`, `dataclasses`,
`datetime`, `enum`, `typing`) and by the fact that `tests/test_download_queue.py`
never touches Qt or the acquisition layer.

## Domain types

```text
QueueEntryState (Enum): QUEUED, PAUSED, REMOVED
QueuePriority (Enum): LOW=100, NORMAL=200, HIGH=300
QueueEntry (frozen dataclass)
DownloadQueue (aggregate root)
QueueDomainError (base)
├── UnknownQueueEntryError
├── DuplicateQueuedTaskError
├── InvalidQueueTransitionError
├── CrossPriorityReorderError
└── InvalidQueueOperationError
generate_queue_entry_id()
```

## State machine

See `docs/QUEUE_DOMAIN_MODEL.md` for the full transition table. Summary:
`QUEUED ↔ PAUSED`, both `→ REMOVED` (terminal). No transition out of
REMOVED is permitted; same-state calls are idempotent no-ops, not errors.

## Priority implementation

Bounded named enum only (`LOW`/`NORMAL`/`HIGH`); `set_priority()` and
`enqueue()` both reject non-`QueuePriority` values via
`InvalidQueueOperationError`. No dynamic/aging priority.

## Ordering implementation

Per-priority-band position lists (`dict[QueuePriority, list[queue_entry_id]]`)
kept contiguous via `_renumber()` after every band-affecting mutation
(enqueue, remove, move, priority change). `active_entries()` /
`eligible_entries()` sort by `(-priority.value, position, enqueued_at,
queue_entry_id)` — deterministic, no reliance on dict/hash ordering.

## Manual reorder implementation

`move_before()` / `move_after()`, same-priority-band only;
`CrossPriorityReorderError` otherwise. Moving relative to self or
involving a REMOVED entry raises `InvalidQueueOperationError`.

## Duplicate-task behavior

`_has_live_entry()` scans for any non-REMOVED entry with the same
`task_id` before every `enqueue()`; violation raises
`DuplicateQueuedTaskError`. Re-enqueue after REMOVED is allowed and
produces a new `queue_entry_id`.

## Tests added

46 tests in `tests/test_download_queue.py`, covering every §45–53
requirement: state transitions (incl. idempotency and rejected
resurrection), priority ordering and change semantics (incl. same-priority
no-op, paused-state preservation), FIFO/manual ordering (incl. paused
retains position, cross-priority rejection), duplicate-task policy and
re-enqueue, eligible-vs-active projection correctness, timestamp
timezone-awareness and idempotent-no-op-preserves-`updated_at`,
encapsulation (immutable tuple + frozen dataclass), the exact multi-step
scenario from §52, and a 100-task bounded stress scenario from §53
(no duplicate active `task_id`, canonical ordering, eligible-subset
correctness, contiguous per-band positions).

## Tests run

285/285 passing (46 new; all 239 pre-existing tests remain green,
unchanged).

## Existing test regressions

NONE.

## Known limitations

See `docs/QUEUE_DOMAIN_MODEL.md` "Known limitations" — scheduler,
dispatch/claim, concurrency, actual transfer pause/resume, retry,
dependencies, scheduled downloads, bandwidth allocation, persistence/crash
recovery, drag-and-drop GUI, priority aging, thread safety: all explicitly
deferred to A2+, none solved here.

---

PHASE: Prompt A1 — Queue Domain Model

STATUS: DONE

BASELINE COMMIT: 22ed6b3

FILES CHANGED: src/rychlik/core/download_queue.py (new),
tests/test_download_queue.py (new), docs/QUEUE_DOMAIN_MODEL.md (new),
docs/QUEUE_DOMAIN_RESULT.md (new)

DOMAIN TYPES: QueueEntryState, QueuePriority, QueueEntry, DownloadQueue,
QueueDomainError + 5 subtypes, generate_queue_entry_id()

QUEUE STATES: QUEUED, PAUSED, REMOVED

STATE TRANSITIONS: QUEUED↔PAUSED, both→REMOVED (terminal); same-state
idempotent; REMOVED has no outgoing transitions

PRIORITIES: LOW=100, NORMAL=200, HIGH=300; HIGH before NORMAL before LOW

CANONICAL ORDERING: priority desc, position asc, enqueued_at asc,
queue_entry_id tie-break

MANUAL REORDER: move_before/move_after, same-priority-band only,
CrossPriorityReorderError otherwise

DUPLICATE TASK POLICY: at most one live (non-REMOVED) QueueEntry per
task_id; DuplicateQueuedTaskError otherwise

RE-ENQUEUE POLICY: allowed after REMOVED; always produces a new
queue_entry_id; old entry never resurrected

IDEMPOTENCY POLICY: pause/resume/remove same-state and set_priority
same-priority are no-ops (no mutation, updated_at unchanged); every other
operation against a REMOVED entry is rejected

ACTIVE PROJECTION: QUEUED + PAUSED, canonical order

ELIGIBLE PROJECTION: QUEUED only, canonical order, read-only (dispatches
nothing)

TESTS ADDED: 46

TESTS RUN: 285/285 passing

EXISTING TEST REGRESSIONS: NONE

KNOWN LIMITATIONS: scheduler, dispatch/claim, concurrency, actual transfer
pause/resume, retry, dependencies, scheduled downloads, bandwidth
allocation, persistence/crash recovery, GUI, priority aging, thread safety
— all deferred to A2+

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt A2 — Download Task Lifecycle Domain Model

COMMIT: (recorded after this phase's commit)
