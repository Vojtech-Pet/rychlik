# Persistent Download State / Restart Recovery (Prompt A8)

No GUI integration exists. No safe partial-transfer resume exists. No
Share by Link state is made durable by this phase. SQLite and the
filesystem are not one atomic transaction.

## Durable vs. runtime-only state

```text
DURABLE (survives restart)              RUNTIME-ONLY (dies with the process)
-----------------------------------     -----------------------------------
DownloadTask identity/lifecycle/        A5 reservations / in-flight Futures
attempt_count/timestamps/last_failure
DownloadRequest                         A7 progress samples/speed/ETA
QueueEntry identity/state/priority/     A6 in-process monotonic due times
position
A6 retry schedule (UTC not_before)      threads, sockets, HTTP responses
```

A process restart *reconstructs* runtime state from durable facts. It
never serializes a Python object graph (no `pickle`).

## Storage

Python stdlib `sqlite3` only -- no ORM, no external database. Default
path: `$XDG_DATA_HOME/rychlik/state.db`, falling back to
`~/.local/share/rychlik/state.db`. Tests always use a temporary path.

Permissions: state directory `0700`, database file `0600` (best-effort on
filesystems without POSIX permission bits). **No encryption at rest.** A
persisted `DownloadRequest.url` can carry sensitive query parameters; A8
does not widen this by persisting cookies, Authorization headers, or
session tokens -- only what the existing `DownloadRequest` contract
already carries.

PRAGMAs: `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=FULL`,
`busy_timeout=5000`. Chosen for correctness, not benchmark speed.

## Schema version

`schema_version=1`, stored in a `metadata` table, validated on every
`initialize()` **before** any row is interpreted. A database with a
version newer than this runtime supports raises
`UnsupportedStateSchemaError` and is never touched/downgraded.

## Tables

```text
metadata          -- schema_version, clean_shutdown
download_tasks    -- one row per DownloadTask
download_requests -- one row per task_id's DownloadRequest
queue_entries     -- one row per QueueEntry (including REMOVED, for history)
retry_schedules   -- one row per live A6 retry deadline
```

Enums are stored as their stable symbolic name (`"READY"`, not a
Python repr). An unknown name on load raises
`PersistentStateCorruptionError` -- it is never silently mapped to a
current state.

## One persistence owner

`SqliteDownloadStateStore` (`rychlik/core/state_store.py`) is the only
module that touches SQLite. It knows how to create/validate the schema,
checkpoint durable rows transactionally, and load them back — nothing
about scheduling, dispatch, or acquisition. A5's `ConcurrentDownloadRuntime`
and A4's `DispatchCoordinator` each accept it only as an optional
collaborator (`state_store=None` / `checkpoint=None` by default),
never importing `sqlite3` themselves.

## Checkpoint model

Two APIs:

- `checkpoint_task_state(...)` -- one SQLite transaction writing exactly
  the rows a single durable lifecycle event touches (a task upsert, plus
  optionally a request/queue-entry/retry-schedule upsert or delete). Used
  on every dispatch lifecycle transition and every retry-schedule event.
  **Never called per A7 progress chunk** -- only on lifecycle/queue events.
- `replace_all(state)` -- one transaction that replaces the *entire*
  durable snapshot; rows no longer present in `state` do not survive as
  ghosts. Used for the initial full save and to persist restart recovery's
  canonical post-recovery state.

A dedicated internal lock (`SqliteDownloadStateStore._lock`, plus
`check_same_thread=False`) serializes all connection use, since A5 worker
threads call `checkpoint_task_state()` concurrently with the controller/
main thread calling `load()`/the shutdown markers. This lock is entirely
internal to the store and participates in no other lock's ordering; A5's
state lock may be held by a caller *around* its call into the store (§73:
acceptable for a small bounded local commit, never around network I/O),
but the store never reaches back into A5/A7 locks.

## Lock ordering

```text
A5 state lock  ->  (optional) persistence lock, internal to the store
```

The A7 progress lock never participates in an A8 checkpoint -- A7
telemetry is never persisted, so no checkpoint ever needs to query
`ProgressRegistry`.

## A4 integration: pre-network checkpoint

`DispatchCoordinator.dispatch()` gained one new optional parameter,
`checkpoint: Callable[[], None] | None = None` (default `None` = exactly
prior behavior, byte-identical to every earlier phase). When supplied, it
is invoked, still under the caller's lock, immediately after every durable
mutation `dispatch()` makes:

```text
start_transfer()  -> checkpoint()   -- BEFORE any AcquisitionService call
CANCELLED         -> checkpoint()
RETRY_WAIT        -> checkpoint()
FAILED            -> checkpoint()   (both the mapped-failure and the
                                      unexpected-exception paths)
COMPLETED         -> checkpoint()
```

If the **pre-network** `checkpoint()` call raises, `DispatchCheckpointError`
propagates and the network transfer is never started -- an unpersisted
TRANSFERRING attempt never begins real I/O (§81). If a **terminal**
checkpoint call raises (e.g. after a real download physically completed),
the exception still propagates as `DispatchCheckpointError`, but the
already-finalized file is never deleted just to fake transactionality
(§129/§130) -- SQLite and the filesystem are not one atomic transaction.

## A5 integration

`ConcurrentDownloadRuntime` gained:

- `state_store: SqliteDownloadStateStore | None = None` (default `None` =
  exactly A5/A6/A7 behavior, no persistence at all).
- `initial_retry_schedule: tuple[RetrySeed, ...] = ()` -- seeds the
  runtime's in-memory `_retry_schedule` at construction from
  `RestartRecovery`'s output, so a restored future retry deadline resumes
  under normal A6 monotonic semantics without any special-casing in the
  controller loop.
- `checkpoint_task(task_id, *, queue_entry_id=None)` -- a public method for
  callers to persist durable facts after any queue/task mutation performed
  **outside** the dispatch loop (enqueue, pause, resume, `set_priority`,
  reorder, remove). A1/A2 themselves stay completely database-independent
  (§77/§78); this method is the only bridge.
- `manager_snapshot()` (A7) is untouched -- it still never touches the
  state store.

Every worker's call into `DispatchCoordinator.dispatch()` now passes a
`checkpoint` closure built fresh per dispatch, reading current facts
straight from the same `tasks`/`queue`/`requests` objects the coordinator
just mutated (`_checkpoint_dispatch_locked`).

## A6 integration: retry-schedule checkpoints

Retry-schedule persistence lives entirely in `concurrent_runtime.py`,
where A6 already computes real UTC/monotonic timing:

```text
RETRY scheduled   -> checkpoint_task_state(task, retry_schedule=...)
                     not_before_utc = scheduled_at_utc + delay_seconds
RETRY promoted    -> checkpoint_task_state(task=READY, delete_retry_schedule_id=...)
RETRY exhausted   -> checkpoint_task_state(task=FAILED, queue_entry=REMOVED,
                                            delete_retry_schedule_id=...)
```

`due_monotonic` is never persisted -- it has meaning only inside one
process lifetime. The durable row stores `scheduled_at_utc` +
`not_before_utc` instead; the in-process A6 loop keeps using
`time.monotonic()` exactly as before restart.

## A7 interaction

None, deliberately. Progress/speed/ETA/sample history are runtime-only
and are never read or written by `state_store.py`. A recovered `READY`
task starts with zero progress -- no attempt to make the GUI "look
continuous" across a restart.

## Restart recovery

`RestartRecovery` (`rychlik/core/restart_recovery.py`) never calls
`AcquisitionService` or `DispatchCoordinator.dispatch()` — it only ever
produces `READY`/`CREATED`/`RETRY_WAIT`/terminal tasks. Actual downloading
begins only once normal A3/A5 scheduling starts afterward.

### Recovery state table

| Persisted task state | Restart state | Why |
|---|---|---|
| CREATED | CREATED | untouched |
| RESOLVING | CREATED | no resumable resolver contract exists |
| READY | READY | untouched |
| TRANSFERRING | READY | no safe partial resume; attempt_count preserved |
| PAUSED (task-level) | READY | the old connection is dead; not `resume_transfer()` |
| RETRY_WAIT | RETRY_WAIT / READY / FAILED | see retry restoration below |
| VERIFYING | READY | no durable verification-continuation contract |
| POST_PROCESSING | READY | a half-finished remux/ffmpeg step is not assumed valid |
| COMPLETED / FAILED / CANCELLED | unchanged | terminal states never resurrect |

Every non-trivial normalization also clears `finished_at` to `None`
(nonterminal tasks must never carry a stale terminal timestamp) and bumps
`updated_at` to the recovery time, while `created_at`/`started_at`/
`attempt_count`/`last_failure` are preserved as-is.

### Queue recovery

`QUEUED`/`PAUSED`/`REMOVED` queue-entry state is preserved verbatim
(queue-level `PAUSED` is a hold decision, not a transfer state, and
survives restart unlike task-level `PAUSED`). The one reconciliation rule:
if a task ended up terminal but its queue entry is still live (a crash
window between two checkpoints), the queue entry is normalized to
`REMOVED` (`TERMINAL_QUEUE_RECONCILED`) -- the terminal task is never
redispatched.

### Retry restoration

For a persisted `RETRY_WAIT` task:

1. `last_failure` must exist and be `retryable`; otherwise
   `PersistentStateCorruptionError` (corrupted state, not a normal
   outcome).
2. If a durable `retry_schedules` row exists, its `not_before_utc` is
   authoritative -- **the full backoff duration is never restarted from
   application startup**; only the actual remaining time is used.
3. If no row exists (a real crash window between persisting `RETRY_WAIT`
   and persisting the schedule), it is reconstructed from
   `RetryPolicy.decide(attempt_count, last_failure)` and
   `task.updated_at`.
4. If the policy says attempts are exhausted, recovery terminalizes
   directly to `FAILED` + queue `REMOVED` (`RETRY_EXHAUSTED`) -- no new
   attempt.
5. If `not_before_utc <= now`, the task recovers straight to `READY`
   (`RETRY_ALREADY_DUE`) and the schedule row is dropped; A3/A5 decides
   when it actually runs, recovery never dispatches directly.
6. Otherwise the task stays `RETRY_WAIT` (`RETRY_RESTORED`) and a
   `RetrySeed(queue_entry_id, task_id, due_monotonic, attempt_count_snapshot)`
   is produced for the caller to seed the new runtime's in-memory
   schedule (`due_monotonic = monotonic_now + remaining_seconds`).

A separate stale-row pass discards any retry_schedules row whose owning
task is no longer `RETRY_WAIT`, whose queue entry is missing/removed, or
whose `attempt_count_snapshot` no longer matches the task's current
generation (`STALE_RETRY_DISCARDED`) -- an old timer can never affect a
newer attempt or a newer re-enqueued occurrence. A retry-schedule row
whose `task_id` does not match its queue entry's actual `task_id` is
treated as structural corruption and fails loudly rather than guessing.

## `.part` policy

`DirectHttpAcquisition` already opens its temp file with `Path.open("wb")`
(truncate-on-open), so a stale `.part` left behind by a killed process is
safely overwritten byte-for-byte by the next attempt -- **no code change
was needed here**; a real-crash E2E regression test proves it
(byte-exact final file after a genuinely killed mid-transfer child
process). A8 does not implement Range resume, ETag continuation, or any
partial-byte trust of an existing `.part` file, and does not globally
scan the filesystem for stray `.part` files.

## Known limitations

```text
no safe partial-transfer resume -- every recovered interrupted attempt
  restarts from byte zero; attempt_count is preserved as history, never
  as a resumable offset

no exactly-once claim -- a crash between physical completion and the
  final durable checkpoint may cause a conservative redownload; A8
  documents this as a known crash/durability edge, not a bug to silently
  paper over

SQLite and the filesystem are not one atomic transaction -- a completed
  file is never deleted just to fake transactionality if the final
  checkpoint fails

no encryption at rest

Share by Link state (ShareLink/SharePreview/local origin/tunnel) is not
  made durable by A8

no GUI wiring -- the bootstrap DownloadWidget is not connected to
  SQLite/A1-A8

CompletedDownload/download history is not given its own persistent model;
  terminal task lifecycle persistence is what A8 provides
```
