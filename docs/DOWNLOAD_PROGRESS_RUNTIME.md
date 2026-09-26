# Download Progress / Speed / ETA Runtime Model (Prompt A7)

No GUI integration exists. No Qt dependency exists in the progress
runtime. No persistence exists. No crash recovery exists. No safe
transfer resume exists. No bandwidth limiting exists. Progress telemetry
is not durable resume state.

## Architecture boundary

```text
AcquisitionService (Prompt 04.5, unmodified callback contract)
        │ progress_callback(bytes_downloaded, total_bytes=None)
        ▼
ProgressReporter (bound to one TransferAttemptKey)
        │
        ▼
ProgressRegistry                     -- raw telemetry, own lock, no lifecycle awareness
        │
        ▼
TransferProgressSnapshot             -- immutable, attempt-local raw facts
        │
        ▼
build_view_snapshot() / build_manager_snapshot()   -- combines with DownloadQueue + DownloadTask
        │
        ▼
DownloadViewSnapshot / DownloadManagerSnapshot     -- flat, GUI-safe, immutable
```

`DownloadTask` (A2) gained **no** new fields. It remains purely the
lifecycle domain model — no bytes, speed, ETA, worker reference, or
`Future`. Progress lives entirely in a separate module
(`rychlik/core/progress.py`), never imported by `download_task.py` or
`download_queue.py`.

## Existing acquisition progress contract (found, reused unchanged)

`DirectHttpAcquisition.acquire()` already calls
`progress_callback(bytes_written: int, total_size: int | None)` once per
internal 64 KiB chunk read, with `total_size` from the response's
`Content-Length` header (`None` if absent). No final "done" call is
guaranteed beyond the natural last chunk. Cancellation is checked before
each chunk write; `DownloadCancelled` is raised without a further
callback. This signature already matched exactly what A7 needed — **no
change to the acquisition backend was required**, and no second,
incompatible callback API was introduced. `ProgressReporter.__call__` has
the identical `(bytes_downloaded, total_bytes=None)` signature, so it can
be passed directly as `progress_callback=`.

## Attempt identity

```text
TransferAttemptKey(task_id, queue_entry_id, attempt_number)
```

Never keyed by `task_id` alone. `ProgressRegistry` internally keys its
records by `queue_entry_id` (mirroring A5/A6's own reservation/retry-
schedule identity choice) and stores `task_id` + `attempt_number` inside
each record for validation — a `report()` call is honored only if all
three match the record's *current* generation; otherwise it is silently
ignored as stale telemetry (recorded as a `STALE_ATTEMPT`
`ProgressTelemetryIssue`, never an error, never a mutation).

## Attempt-local byte semantics

`bytes_downloaded` means bytes transferred in the **current acquisition
attempt only** — not a cumulative resumable total. No safe Range-based
resume exists yet (Prompt 04.5 explicitly deferred it); a future resume
phase may need to add a baseline-offset extension. Telemetry is not
resume state — `bytes_downloaded = 100 MB` does not mean Rýchlik can
restart from 100 MB after a crash or cancellation.

## Retry reset semantics

Every `begin_attempt()` call **replaces** the record for that
`queue_entry_id` wholesale — prior sample history and byte count are
discarded entirely, never combined across a retry's full-restart attempts.
`DispatchCoordinator.dispatch()` calls `begin_attempt(task_id,
queue_entry_id, task.attempt_count)` immediately after `start_transfer()`
succeeds, so a retried attempt automatically gets fresh telemetry with
zero extra code in A6 — the attempt-number match against A2's own
`DownloadTask.attempt_count` is what makes this correct, not a second
counter.

## Speed estimator

Bounded recent-window rate: samples within the last
`speed_window_seconds` (default 5.0) are kept, capped at
`max_speed_samples` (default 64) regardless of window — both bounds
apply together, so a long download's memory footprint never grows
unbounded. `speed_bps = (newest.bytes - oldest.bytes) / (newest.time -
oldest.time)` using the two extreme samples currently retained; requires
at least two samples, a positive time delta, and a non-negative byte
delta, otherwise `None`. A full-elapsed-time-since-start average was
deliberately rejected (§25 of the prompt) — it becomes increasingly
insensitive to the current rate as a long download progresses.

## Stale speed policy

`speed_stale_after_seconds` (default 4.0): if the most recent sample is
older than this relative to snapshot time, `speed_bps` is reported as
`None` even though the raw byte count is preserved — a stalled worker
never shows a falsely-positive rate forever. This check happens entirely
inside `snapshot()` at read time, comparing monotonic timestamps; there is
no background timer or polling thread dedicated to staleness.

## ETA model

```text
eta_seconds = (total_bytes - bytes_downloaded) / speed_bps
```

only if `total_bytes` is known, `speed_bps > 0`, and
`bytes_downloaded < total_bytes`; otherwise `None`. Raw units throughout
(bytes, bytes/second, seconds) — no formatted strings, no "3 minutes
left", no percentage strings. Presentation formatting is explicitly a
future GUI-layer concern.

## Unknown-total behavior

`total_bytes = None` (never `0` or `-1`) whenever the acquisition backend
doesn't know the size. `progress_fraction` and `eta_seconds` are `None`
in that case; `speed_bps` is still computed normally from raw byte deltas.
A total that becomes known partway through an attempt is accepted. A
total that changes to a *different* known value mid-attempt is accepted
(the new value wins) but recorded as a `TOTAL_CHANGED`
`ProgressTelemetryIssue` — never fails the download.

## Malformed telemetry handling

Negative bytes, a same-attempt byte regression, and stale-attempt
callbacks are all detected, **ignored** (the record's previous valid state
is preserved), and recorded as a bounded `ProgressTelemetryIssue`
(`deque(maxlen=200)`, its own dedicated lock,
`drain_telemetry_issues()`). None of these can ever cause the underlying
acquisition to fail — a telemetry-only anomaly is structurally incapable
of touching `DownloadTask`/`DownloadQueue` state; `ProgressRegistry`
never calls into either.

## Lifecycle-driven presentation overrides (flat snapshot only)

`ProgressRegistry`/`TransferProgressSnapshot` know nothing about
`DownloadTaskState`. The override rules live entirely in
`build_view_snapshot()` (`rychlik/core/download_view.py`):

```text
COMPLETED           -> speed=0.0, eta=0.0, fraction=1.0 (if total known)
FAILED / CANCELLED  -> speed=0.0, eta=None (bytes_downloaded preserved)
RETRY_WAIT          -> speed=0.0, eta=None (retry backoff != download ETA)
anything else       -> raw ProgressRegistry values (already stale-aware)
```

A `QueueEntry.PAUSED` + `DownloadTask.READY` combination needs no special
casing at all: no attempt was ever begun for that occurrence, so
`ProgressRegistry.snapshot()` simply returns `None` and the flat view
naturally shows no active transfer.

## Threading / lock model

`ProgressRegistry` owns **two** small, independent locks of its own — a
`threading.Lock` protecting the attempt records (used on the hot,
high-frequency per-chunk `report()` path) and a separate `threading.Lock`
protecting the bounded telemetry-issue deque. **Neither is ever the same
lock as `ConcurrentDownloadRuntime._state_lock`** (A5), and network I/O,
file writes, hashing, or any GUI callback never run while either progress
lock is held.

## Lock ordering

The two lock domains (A5's state lock, A7's progress lock) are **never
nested** — `ConcurrentDownloadRuntime.manager_snapshot()` acquires the
state lock only long enough to copy `QueueEntry`/`DownloadTask` facts,
releases it completely, and only then queries `ProgressRegistry` (which
takes its own lock internally, entirely independently). This is the
simplest possible ordering policy: rather than defining "state lock before
progress lock" and having to prove no code path reverses it, the two are
structurally never held simultaneously by the same thread. Proven directly
by a test where a (subclassed) slow `ProgressRegistry.snapshot()` call
successfully acquires the runtime's own state lock from a second thread
while the composition is still in progress.

## Flat snapshot contract

```text
DownloadViewSnapshot(task_id, queue_entry_id, display_name, task_state,
  queue_state, priority, position, attempt_count, bytes_downloaded,
  total_bytes, progress_fraction, speed_bps, eta_seconds, last_failure_code)
```

Contains **no** `DownloadTask`/`QueueEntry`/`Future`/`Thread`/`Lock`/
`DownloadRequest`/worker reference — only flat values and enums,
structurally verified by a field-set test. `display_name` comes only from
`DownloadRequest.filename_hint` (never the raw URL, which may carry query
tokens/credentials, and never the local filesystem path) — if no hint was
given, `display_name` is `None` rather than guessing from an unsafe
source. **Known limitation**: the final server-resolved filename after a
successful download (`CompletedDownload.display_name`) is not yet wired
back into the manager snapshot — only available today via
`WorkerCompletion.result.completed_download.display_name` from
`drain_completions()`. `last_failure_code` exposes only the stable string
code, never the raw exception/message-with-internals.

`DownloadManagerSnapshot(items, active_transfer_count, aggregate_speed_bps)`
preserves the exact canonical `DownloadQueue` order (A1) for `items` —
never resorted by speed/filename/progress. `active_transfer_count` counts
`DownloadTaskState.TRANSFERRING` directly (never inferred from
`speed > 0`, since a stalled-but-still-transferring task is still active).
`aggregate_speed_bps` sums the effective (already staleness-aware) speed
of currently-`TRANSFERRING` items only, treating `None` contributions as
zero/unknown rather than breaking the sum; `None` overall if no
transferring item has a known speed.

By default, `manager_snapshot()` includes only `DownloadQueue.active_entries()`
(`QUEUED`+`PAUSED`, i.e. non-`REMOVED`). An explicit
`include_removed: tuple[str, ...]` parameter allows a caller to request
specific historical `REMOVED` occurrences too (A1 already retains them for
audit) — no general download-history ordering system was built.

## A4 integration

`DispatchCoordinator.dispatch()` gained one new optional parameter,
`progress_registry: ProgressRegistry | None = None` (default `None` =
exactly Prompt A4 behavior, all original A4 tests pass unmodified). When
supplied, a `ProgressReporter` is created right after `start_transfer()`
succeeds (so `attempt_number` always matches the just-incremented
`DownloadTask.attempt_count`) and used as the effective acquisition
progress callback — composed with any caller-also-supplied
`progress_callback`, never silently dropping one or the other.

## A5 integration

`ConcurrentDownloadRuntime` gained one new optional constructor parameter,
`progress_registry: ProgressRegistry | None = None` (default `None` =
exactly A5/A6 behavior). Every worker passes it straight through to
`DispatchCoordinator.dispatch()`. `manager_snapshot()` is the new
copy-then-compose entry point described above.

## A6 integration

**No code changes to `retry_policy.py` were needed.** The only required
interaction ("a new transfer attempt begins a new progress attempt")
already happens automatically: A6's retry runtime calls
`task.mark_retry_ready()` then the normal A3/A5 selection flow calls
`start_transfer()` again through the *same* A4 `dispatch()` path, which
already calls `progress_registry.begin_attempt()` unconditionally on every
successful `start_transfer()` — first attempt or retry, no distinction
needed in the code.

## Known limitations

```text
no GUI integration -- no PySide6 dependency anywhere in the progress
  runtime, structurally verified by an AST-based import test
no persistence -- speed samples, ETA, and recent sample history are all
  naturally recomputable runtime telemetry, not durable-persistence
  candidates; a future persistence phase concerns itself with durable
  byte/resume state instead, which is a genuinely separate question
no crash recovery -- all telemetry is lost on process exit, by design
no safe transfer resume -- bytes_downloaded is attempt-local, never a
  cumulative resumable offset
no bandwidth limiting/throttling
display_name does not yet reflect the final server-resolved filename
  after a successful download (only the pre-download filename_hint) --
  available via WorkerCompletion instead, not yet wired into snapshots
no global/manager-level ETA aggregation (only per-item ETA and aggregate
  current speed)
```
