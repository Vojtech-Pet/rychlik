# Safe Partial Resume / Real Transfer Pause-Resume (Prompt A9)

No exactly-once claim. No segmented/multipart downloading. No GUI
pause/resume controls. No bandwidth limiting. No provider-specific
(YouTube/Drive/Dropbox) resume logic — generic HTTP Range resume only.

## The central safety principle

**A `.part` file existing is never, by itself, proof that it is safe to
resume.** Every resume requires ALL of:

```text
an owned partial file          -- inside the expected destination,
                                   not a symlink escape, not an
                                   arbitrary persisted path
+ a durable byte checkpoint    -- never the raw on-disk file size
+ local prefix integrity       -- SHA-256 of exactly the checkpointed
                                   prefix, recomputed at resume time
+ a remote validator           -- strong ETag, else Last-Modified,
                                   else none (no Range attempt at all)
+ a valid HTTP Range/206 response consistent with all of the above
```

If any single piece of evidence is missing or inconsistent, the only safe
answer is `FULL_RESTART` — never an optimistic append. This is
intentionally conservative: a validated partial resume with a full-restart
fallback, not "exactly-once downloading" and not a distributed
transaction. SQLite, the filesystem, and the network are not one atomic
unit — see docs/PERSISTENT_DOWNLOAD_STATE.md for the corresponding
disclaimer on durable checkpoints.

## Pure decision layer: `rychlik/core/partial_transfer.py`

```text
ValidatorKind: NONE | STRONG_ETAG | LAST_MODIFIED
Validator(kind, value)                     -- If-Range header value
PartialTransferState                        -- durable checkpoint, keyed
                                                by queue_entry_id (never
                                                task_id alone)
ResumeDecisionKind: NO_PARTIAL | FULL_RESTART | ATTEMPT_RANGE
plan_resume(partial, expected_dir, task_attempt_count) -> ResumeDecision
validate_local_partial(partial, expected_dir) -> (is_valid, reason)
classify_validator(etag, last_modified) -> Validator
```

`classify_validator()` prefers a strong ETag, then Last-Modified, else
`NONE`. A weak ETag (`W/"..."`) is **never** treated as strong — it either
falls back to Last-Modified or to no validator at all.

`validate_local_partial()` is local-only (no network): it re-resolves the
persisted `temp_path` and rejects it (raising
`PartialTransferConsistencyError`, never silently) unless it resolves to a
plain file directly inside the expected destination directory with the
expected `.part` suffix, and is not a symlink. Only after ownership is
proven does it compare `durable_bytes` against the actual file size —
larger is truncated back to `durable_bytes` in place (discarding
uncheckpointed crash-window bytes), shorter is rejected outright — and
finally recompute the SHA-256 of exactly that prefix, comparing it against
the persisted `prefix_sha256`. Any mismatch anywhere in this chain
produces `FULL_RESTART`, never a partial/inconsistent append.

`plan_resume()` additionally rejects a partial whose
`attempt_count_snapshot` is a newer generation than the task's current
`attempt_count` (structurally impossible except via a corrupted database)
and rejects a `ValidatorKind.NONE` partial outright — no validator means
no Range attempt is ever made.

This module does real file I/O (open/truncate/hash) but no network client
and no threading — it is the layer both `rychlik/core` (durable state) and
`rychlik/acquisition` (HTTP mechanics) import, by design (see "Known
architectural note" below).

## Durable schema (v1 → v2 migration)

`state_store.py`'s `SCHEMA_VERSION` is now `2`. The `partial_transfers`
table is added via `CREATE TABLE IF NOT EXISTS` inside the SAME
transaction that reads/validates the existing `schema_version` row — this
is a REAL migration (not a relabel): an authentic v1 database gains
exactly the new table and its `schema_version` metadata row is updated to
`2`; none of the four original A8 tables' rows are touched, copied, or
dropped. If anything in that transaction raises, `ROLLBACK` restores the
original v1 database byte-for-byte (proven by a dedicated migration test
that hand-builds a real v1 schema first). A future schema version is still
refused (`UnsupportedStateSchemaError`) before any table is even created.

```text
partial_transfers(
    queue_entry_id PRIMARY KEY,   -- occurrence identity, never task_id alone
    task_id,
    attempt_count_snapshot,
    temp_path, final_path,
    durable_bytes,
    expected_total_bytes,
    validator_kind, validator_value,
    prefix_sha256,
    created_at, updated_at
)
```

`SqliteDownloadStateStore.checkpoint_task_state()` gained additive
`partial_transfer=`/`delete_partial_transfer_id=` parameters, following
the exact same pattern A8 already established for `retry_schedule=`/
`delete_retry_schedule_id=`. A new narrow `load_partial_transfer(
queue_entry_id)` query fetches one row without a full `load()` — the live
dispatch path calls this once per attempt, never per network chunk.

## Checkpoint cadence and ordering

Durable partial checkpoints happen at three points only, never per A7
progress chunk:

```text
1. periodically during an active transfer, every
   `checkpoint_bytes_threshold` bytes (default 8 MiB, configurable via
   DispatchCoordinator(resume_checkpoint_bytes_threshold=...) for tests)
2. forced, unconditionally, the instant a pause is observed -- even if
   the byte threshold has not been reached
3. cleared entirely on COMPLETED/CANCELLED/FAILED (non-retryable);
   preserved across RETRY_WAIT so the next attempt can resume from it
```

Order within `DirectHttpAcquisition.acquire()`: write chunk → flush →
`os.fsync()` → THEN persist the new `durable_bytes`/`prefix_sha256` — a
durable offset is never committed to SQLite before the corresponding file
bytes are durable on disk (§46). The running SHA-256 is maintained
incrementally as chunks arrive (never re-reading the whole file per
checkpoint); the one-time cost of re-reading and re-hashing the
already-validated prefix happens exactly once, at the start of a resumed
attempt, to seed the running digest before new bytes are appended (an
accepted, documented `O(partial size)` cost — see §101 of the prompt).

## Pause: cooperative, never thread suspension

`DownloadPaused` (a new `AcquisitionError` subclass, sibling to
`DownloadCancelled`, not a subclass of it) is raised by
`DirectHttpAcquisition.acquire()` only once the transfer loop has actually
stopped between chunks and (if a `ResumeRequest` was supplied) the forced
durable checkpoint has been written. `pause_event`/`cancel_event` are both
plain `threading.Event`s, checked once per chunk inside the same loop —
**cancel wins**: if both are set, `cancel_event` is checked first each
iteration, so a simultaneous cancel+pause request always produces
`CANCELLED`, never `PAUSED`. Pause latency is therefore bounded by the
current blocking network read and the chunk size (64 KiB) — never
instantaneous, and documented as such.

On `DownloadPaused`, the `.part` file and its durable checkpoint are
deliberately preserved (unlike `DownloadCancelled`, which still deletes
the `.part` and clears any durable partial state exactly as it always
has).

## `DispatchKind`: START vs. RESUME

`rychlik.core.scheduler_policy.DispatchCandidate` gained an additive
`kind: DispatchKind = DispatchKind.START` field. `SchedulerPolicy.plan()`
gained an additive `resume_requested_queue_entry_ids: frozenset[str] =
frozenset()` parameter (empty default = byte-for-byte prior behavior):

```text
QueueEntry.QUEUED + DownloadTask.READY                          -> START
QueueEntry.QUEUED + DownloadTask.PAUSED + resume-requested       -> RESUME
QueueEntry.PAUSED (queue-level) + anything                       -> never selected
```

Both kinds are interleaved in `queue.eligible_entries()`'s exact canonical
order (priority, then position) and consume the exact same
`max_active_transfers`/reservation accounting — a RESUME candidate is
never given hidden priority over a START candidate, and reservations
protect a RESUME candidate from double-submission exactly like a START
candidate.

`DispatchCoordinator._prepare()` requires `Task READY` for `START`
(`start_transfer()`, `attempt_count += 1`) and `Task PAUSED` for `RESUME`
(`resume_transfer()`, `attempt_count` **unchanged** — A2's sole attempt
counter is never duplicated). A crash-recovered `TRANSFERRING → READY`
task's next dispatch is still a normal `START` (a new attempt, even if it
reuses validated partial bytes) — attempt-count truthfulness from A2/A8 is
preserved unconditionally.

## Runtime pause/resume API (`ConcurrentDownloadRuntime`)

```text
request_pause(queue_entry_id) -> PauseRequestOutcome.REQUESTED | NOT_ACTIVE
request_resume(queue_entry_id) -> None   (records runtime-only intent)
```

Both are additive; the entire feature is gated behind a new constructor
flag `enable_pause_resume: bool = False` (default off = byte-for-byte A5-
A8 behavior, so every existing `AcquisitionService`/coordinator test
double that only implements the pre-A9 `acquire()` signature is
completely unaffected — the pre-A9 default never even constructs a
`pause_event`). `request_pause()` is idempotent (setting an already-set
`Event` is a no-op) and returns `NOT_ACTIVE` rather than silently
succeeding for a `READY`/`RETRY_WAIT`/already-`PAUSED`/unknown occurrence.
`request_resume()` only records intent in a runtime-only
`set[queue_entry_id]` (§17 — safely lost on restart, the task simply stays
`PAUSED`); the intent is consumed the moment `SchedulerPolicy` actually
selects it as a `RESUME` candidate, not merely on request.

`checkpoint_task_state()`-style bridging is symmetric with A8's
`checkpoint`: `load_partial`/`save_partial`/`clear_partial` are optional
callables threaded from `ConcurrentDownloadRuntime` (via its
`state_store`) down through `DispatchCoordinator.dispatch()` into
`AcquisitionService.acquire()`'s `resume: ResumeRequest | None` parameter
— `rychlik/acquisition` never imports `sqlite3`/`state_store` directly.
`load_partial(queue_entry_id)` fetches the raw persisted row;
`DispatchCoordinator` is where the actual local re-validation happens
(via `plan_resume()`) before the result is ever handed to the acquisition
backend as `resume.initial_partial` — a `.part` file's mere existence
never reaches the network layer as "safe to resume" without that check.

`save_partial`/`clear_partial` run on the SAME worker thread as the
acquisition call, deliberately **without** `ConcurrentDownloadRuntime`'s
state lock held (§112/§113) — a local prefix hash/fsync must never block
the scheduler or other workers, mirroring how A7's progress callbacks
already run fully unlocked on this same hot path. Reservation release
after a `PAUSED` result needs no special code: the existing `finally`
block already pops the reservation and calls `notify_state_changed()` for
every outcome, so a paused transfer's freed slot is picked up by the
controller's very next planning pass exactly like any other completion.

## HTTP mechanics (`DirectHttpAcquisition`)

```text
offset > 0 and a validator exists
    -> Range: bytes=<offset>-
       If-Range: <validator>
       Accept-Encoding: identity
    -> 206 + valid Content-Range (start == offset, end >= start,
       total consistent with any previously-known total) + matching
       validator + identity encoding  => append only the new bytes
    -> any other outcome (200, wrong/malformed 206, 416, validator
       mismatch, non-identity encoding) => ONE clean full restart
       request, offset reset to 0, filename re-resolved from the NEW
       response (never combining old prefix + new representation)
offset == 0 or no validator
    -> plain full request, exactly like pre-A9 behavior
```

`416 Range Not Satisfiable` is never interpreted as "the file must already
be complete" — it triggers the same one-clean-full-restart fallback as
every other rejected resume. At most one resume attempt is made per
acquisition call; a genuine network failure after that still goes through
the normal A4/A6 retry path, not a resume-specific retry loop.

Progress reported during a resumed transfer is **cumulative** (`offset +
bytes received this session`), matching A7's `bytes_downloaded`
attempt-local-but-resume-aware contract (see the A7 doc's "Resume offset
seeding" section).

## A3/A4/A5/A7/A8 integration summary

```text
A3 (SchedulerPolicy)      -- DispatchKind, resume_requested_queue_entry_ids
A4 (DispatchCoordinator)  -- kind-aware _prepare(), PAUSED outcome,
                             load_partial/save_partial/clear_partial bridge,
                             pause_event passthrough
A5 (ConcurrentDownloadRuntime) -- request_pause/request_resume,
                             enable_pause_resume flag, per-occurrence
                             pause_event map, resume-requested set
A7 (ProgressRegistry)     -- begin_attempt(initial_bytes=0) additive
A8 (RestartRecovery)      -- PAUSED recovers to PAUSED only with a
                             locally-validated partial, else READY
                             (network validation still deferred to normal
                             dispatch, never performed during recovery)
```

## Known architectural note

`rychlik/acquisition/contracts.py` (`ResumeRequest`) references
`rychlik.core.partial_transfer.PartialTransferState` under
`TYPE_CHECKING` only (no runtime import cycle), and
`rychlik/acquisition/direct_http.py` imports `rychlik.core.partial_transfer`
directly at runtime. This is a deliberate exception to the general
"acquisition is a lower layer than core" convention established since
Prompt 04.5/A4: `PartialTransferState` is a durable checkpoint concept
(like `DownloadTask`), so it is owned by `core`, and the acquisition
backend needs its concrete shape to build/consume `ResumeRequest`.
`rychlik.core.partial_transfer` itself imports nothing from
`rychlik.acquisition` or `rychlik.core.state_store`, so no cycle exists.

## Known limitations

```text
no exactly-once claim -- SQLite + filesystem + HTTP are not one atomic
  transaction; a crash between a physical file write and its durable
  checkpoint can still produce a conservative redownload (inherited from
  A8, documented there too)

no segmented/multipart/parallel-range downloading -- one sequential
  stream per attempt, always

pause latency is bounded by the current blocking network read and the
  64 KiB chunk size, never instantaneous

Content-Encoding safety (§35): the code checks
  `response.headers.get("Content-Encoding", "identity") == "identity"`
  and falls back to full restart if a resumed 206 response is encoded,
  but this is NOT exercised by a dedicated real-gzip E2E test -- the
  fixture server never sends a non-identity encoding. Verified by code
  review only; flagged here rather than silently claimed as fully proven.

no combined real-process-crash + partial-resume E2E test exists (Prompt
  A8 already has a real SIGKILL crash-recovery E2E without a resumable
  Range-capable endpoint; A9 already has real pause/resume/retry-resume
  E2E tests without a process crash). Combining both in one test was
  judged not to add materially different coverage over the two existing
  real-process/real-resume proofs given the time this phase already took,
  and is called out here explicitly rather than silently skipped -- the
  underlying mechanism (RestartRecovery's PAUSED-with-valid-partial path)
  IS covered by focused non-subprocess tests in test_restart_recovery.py.

request_resume() while the queue entry is QueueEntry.PAUSED (queue-level
  hold) leaves the intent pending indefinitely with no expiry/timeout --
  matches the prompt's own suggested "simple and deterministic" policy,
  not flagged as a defect

no GUI Pause/Resume controls -- backend semantics only, proven headlessly

DownloadPaused/pause_event only exist on the DirectHttpAcquisition path;
  no other acquisition backend exists to extend

resume checkpoint threshold is per-DispatchCoordinator-instance, not
  configurable per-download
```
