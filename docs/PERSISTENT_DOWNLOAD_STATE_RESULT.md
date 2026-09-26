PHASE:
Prompt A8 — Persistent Download State / Restart Recovery

STATUS:
COMPLETE

BASELINE COMMIT:
0da90e8

ACTUAL BASELINE HEAD:
0da90e84bf549fb19fef9e70443c68f8052fa335 (worktree was clean at start)

FILES CHANGED:
  new:
    src/rychlik/core/state_store.py
    src/rychlik/core/restart_recovery.py
    tests/test_state_store.py
    tests/test_restart_recovery.py
    tests/test_dispatch_checkpoint.py
    tests/test_runtime_persistence_integration.py
    tests/test_real_process_crash_e2e.py
    tests/_a8_crash_worker.py           (subprocess helper, not a test module)
    tests/test_download_queue_restore.py
    docs/PERSISTENT_DOWNLOAD_STATE.md
    docs/PERSISTENT_DOWNLOAD_STATE_RESULT.md
  modified:
    src/rychlik/core/download_queue.py    (+DownloadQueue.restore(), additive)
    src/rychlik/core/download_task.py     (+restore_task() factory, additive)
    src/rychlik/core/dispatch_coordinator.py  (+checkpoint param, +DispatchCheckpointError)
    src/rychlik/core/concurrent_runtime.py    (+state_store, +initial_retry_schedule,
                                                +checkpoint_task(), retry-schedule persistence)

STATE STORE TYPES:
  SqliteDownloadStateStore, PersistentDownloadState, PersistedRetrySchedule
  PersistentStateError / UnsupportedStateSchemaError / PersistentStateCorruptionError

DATABASE LOCATION:
  default_state_db_path() -> $XDG_DATA_HOME/rychlik/state.db, falling back
  to ~/.local/share/rychlik/state.db. All tests use tmp_path; nothing in
  the test suite touches the real user home directory.

SCHEMA VERSION:
  1 (SCHEMA_VERSION constant), stored in `metadata`, validated before any
  row is interpreted. A future version raises UnsupportedStateSchemaError
  without modifying the database (verified: byte-identical file before/after).

SQLITE DURABILITY SETTINGS:
  PRAGMA foreign_keys = ON
  PRAGMA journal_mode = WAL
  PRAGMA synchronous = FULL
  PRAGMA busy_timeout = 5000
  State directory: 0700, database file: 0600 (best-effort chmod).

TABLES:
  metadata, download_tasks, download_requests, queue_entries, retry_schedules

DURABLE TASK FIELDS:
  task_id, state, attempt_count, created_at, updated_at, started_at,
  finished_at, last_failure_code, last_failure_message, last_failure_retryable

DURABLE QUEUE FIELDS:
  queue_entry_id, task_id, state, priority, position, enqueued_at,
  updated_at, paused_at

DURABLE REQUEST FIELDS:
  task_id, url, destination_dir, filename_hint

DURABLE RETRY FIELDS:
  queue_entry_id, task_id, attempt_count_snapshot, delay_seconds,
  scheduled_at_utc, not_before_utc   (due_monotonic deliberately excluded)

RUNTIME-ONLY FIELDS EXCLUDED:
  A5 reservations/in-flight Futures, A7 bytes_downloaded/speed_bps/
  eta_seconds/sample history/last-sample timestamp, A6 due_monotonic.
  Verified by a dedicated test asserting these column names are absent
  from download_tasks/queue_entries, plus two E2E tests that populate
  live A7 telemetry/A5 reservations, checkpoint, and confirm nothing of
  that shape appears in the persisted rows.

CHECKPOINT MODEL:
  checkpoint_task_state(...) -- one transaction per durable lifecycle
  event (task + optional request/queue-entry/retry-schedule upsert or
  retry-schedule delete). Never called per A7 progress chunk.
  replace_all(state) -- one transaction, whole-snapshot replace (initial
  save + post-recovery canonical persist); no ghost rows survive.

LOCK MODEL / ORDER:
  SqliteDownloadStateStore owns one internal threading.Lock (plus
  check_same_thread=False on the connection) serializing all connection
  use across A5 worker threads and the main/controller thread. It never
  reaches into the A5 state lock or A7 progress lock; a caller may hold
  the A5 state lock *around* a checkpoint call (acceptable per §73: a
  small bounded local commit, never around network I/O). The A7 progress
  lock never participates in any A8 checkpoint.

CLEAN SHUTDOWN MODEL:
  metadata.clean_shutdown: '0' immediately on every initialize()/
  mark_session_dirty() call (dirty-by-default, including on first
  creation); set to '1' only by an explicit mark_clean_shutdown() call
  made after runtime.stop() completes. get_previous_shutdown_clean() must
  be read BEFORE calling mark_session_dirty() to see the PREVIOUS
  session's outcome.

RECOVERY SERVICE:
  RestartRecovery.recover(persisted, now, monotonic_now,
  previous_shutdown_clean) -> RecoveryResult(state, retry_seeds, report).
  Never calls AcquisitionService or DispatchCoordinator.dispatch().

RECOVERY STATE TABLE:
  CREATED->CREATED, RESOLVING->CREATED, READY->READY,
  TRANSFERRING->READY, PAUSED(task)->READY, VERIFYING->READY,
  POST_PROCESSING->READY, RETRY_WAIT->RETRY_WAIT/READY/FAILED (see below),
  COMPLETED/FAILED/CANCELLED unchanged. Full rationale in
  docs/PERSISTENT_DOWNLOAD_STATE.md.

TRANSFERRING RECOVERY:
  -> READY, attempt_count preserved unchanged, finished_at forced None,
  updated_at bumped to recovery time. Verified live (real subprocess
  crash E2E) and in isolation.

TASK PAUSED RECOVERY:
  -> READY (never resume_transfer()) -- the old connection cannot survive
  process death. Distinguished by a dedicated test from QueueEntry.PAUSED,
  which is preserved verbatim.

QUEUE PAUSED RECOVERY:
  Preserved exactly as persisted (QUEUED/PAUSED/REMOVED all pass through
  unchanged except for terminal-queue reconciliation). Proven distinct
  from DownloadTask.PAUSED by a dedicated test using both combinations.

RETRY RESTORATION:
  Durable not_before_utc is authoritative when present; remaining delay
  (not full backoff) is used to compute a fresh due_monotonic for the new
  process. Already-due -> READY (schedule dropped, no direct dispatch).
  Exhausted (per RetryPolicy.decide) -> FAILED + queue REMOVED.

RETRY MISSING-SCHEDULE RECOVERY:
  Reconstructed via RetryPolicy.decide(attempt_count, last_failure) and
  task.updated_at when no retry_schedules row exists for a RETRY_WAIT
  task (the real crash-window case named in §57).

TERMINAL QUEUE RECONCILIATION:
  A terminal task with a still-live queue entry is reconciled to REMOVED
  (TERMINAL_QUEUE_RECONCILED), never redispatched.

STALE RETRY POLICY:
  Any retry_schedules row whose task is no longer RETRY_WAIT, whose queue
  entry is missing/REMOVED, or whose attempt_count_snapshot no longer
  matches the task's current generation is discarded
  (STALE_RETRY_DISCARDED). A retry_schedule/queue_entry task_id mismatch
  is treated as corruption (PersistentStateCorruptionError), never guessed.

.PART POLICY:
  No code change needed: DirectHttpAcquisition already opens its `.part`
  file with `Path.open("wb")` (truncate-on-open), so a stale `.part` left
  by a killed process cannot corrupt a fresh attempt. Verified by both an
  isolated regression test (pre-seeding a larger garbage `.part` file) and
  the real subprocess-crash E2E (byte-exact final file after a genuinely
  killed mid-transfer child).

A4 INTEGRATION:
  DispatchCoordinator.dispatch(checkpoint: Callable[[], None] | None =
  None). None preserves exact original behavior (all pre-A8 tests pass
  unmodified). Invoked under the caller's lock immediately after every
  durable mutation: pre-network TRANSFERRING (before any network call),
  CANCELLED, RETRY_WAIT, FAILED (both failure paths), COMPLETED. A
  pre-network checkpoint failure raises DispatchCheckpointError and the
  network transfer is never started.

A5 INTEGRATION:
  ConcurrentDownloadRuntime(state_store=None, initial_retry_schedule=()).
  Both default to exactly A5/A6/A7 behavior. New checkpoint_task(task_id,
  queue_entry_id=None) public method bridges durable queue/task mutations
  performed outside the dispatch loop. Every worker's dispatch() call now
  passes a checkpoint closure built fresh per call.

A6 INTEGRATION:
  Retry-schedule persistence added directly in concurrent_runtime.py
  (_handle_retry_wait_locked, _promote_retry_locked) since that is where
  A6 already computes real UTC/monotonic timing. due_monotonic is never
  persisted; scheduled_at_utc/not_before_utc are.

A7 INTERACTION:
  None. state_store.py never imports or references ProgressRegistry;
  verified by a structural AST import test and by a dedicated E2E test
  that populates live progress telemetry, checkpoints, and confirms no
  progress-shaped columns exist in the persisted schema.

TESTS ADDED: 58
  test_state_store.py: 21
  test_restart_recovery.py: 18
  test_dispatch_checkpoint.py: 6
  test_runtime_persistence_integration.py: 6
  test_real_process_crash_e2e.py: 1
  test_download_queue_restore.py: 6

TESTS RUN: 583 passed, 0 failed, 0 skipped (full suite, run three times
  for stability: 583/583 every time, ~19.8s each)

REAL CLEAN RESTART E2E: PASS
  test_real_clean_restart_preserves_identity_order_and_pause -- real
  SQLite file, mixed priorities + one queue-paused entry, closed and
  reopened as a fresh store instance; same queue_entry_ids, same
  canonical order, queue-pause preserved, clean-shutdown marker correct.

REAL RETRY RESTART E2E: PASS
  test_real_retry_persists_schedule_and_clears_it_on_promotion -- real
  HTTP /flaky fixture route + real RetryPolicy; retry_schedules row
  observed live in the database while the task is genuinely RETRY_WAIT,
  then confirmed cleared once the retry is promoted and the redownload
  completes (attempt_count == 2).

REAL PROCESS-CRASH E2E: PASS
  test_real_crash_recovery_e2e -- a real subprocess (tests/_a8_crash_worker.py)
  opens the real SQLite file, starts a real ConcurrentDownloadRuntime, and
  begins a real HTTP download against the shared fixture server's /slow
  route. The parent polls the real database file until it observes a
  genuinely durable TRANSFERRING row, then SIGKILLs the child (no graceful
  shutdown). The parent then opens the same database, confirms
  previous_shutdown_clean == False, runs RestartRecovery, confirms
  TRANSFERRING -> READY with attempt_count preserved, builds a brand-new
  runtime in-process, and proves a full real redownload completes with
  attempt_count == 2 and a byte-exact final file. Run 4 times during
  development with no flakiness observed.

STALE .PART E2E: PASS
  test_stale_part_file_does_not_corrupt_fresh_download (isolated
  regression) + the byte-exact assertion inside the real crash E2E above.

EXISTING TEST REGRESSIONS:
NONE -- all 525 pre-A8 tests pass completely unmodified.

BUGS FOUND:
  1. (Self-caught during implementation, not a test failure) The initial
     SqliteDownloadStateStore implementation opened its sqlite3 connection
     with the default check_same_thread=True. Since A5 worker threads call
     checkpoint_task_state() while the main/controller thread calls
     load()/the shutdown markers, this silently raised inside the A8
     checkpoint closure -- caught by DispatchCoordinatorError's broad
     except in _run_worker(), so the runtime never crashed but the
     checkpoint simply never happened. Two E2E tests
     (test_real_download_checkpoints_pre_network_and_terminal,
     test_real_retry_persists_schedule_and_clears_it_on_promotion) caught
     this on first run (assertions timed out waiting for a row that was
     silently never written). Fixed by opening the connection with
     check_same_thread=False and adding one dedicated internal lock
     serializing all connection use across threads.

KNOWN LIMITATIONS:
  no safe partial-transfer resume -- every recovered interrupted attempt
    restarts from byte zero; attempt_count is preserved as history only
  no exactly-once claim -- a crash between physical file completion and
    the final durable checkpoint may cause a conservative redownload
  SQLite and the filesystem are not one atomic transaction -- a completed
    file is never deleted just to fake transactionality on checkpoint
    failure
  no encryption at rest (documented, not attempted)
  Share by Link state (ShareLink/SharePreview/local origin/tunnel) is not
    made durable by A8
  no GUI wiring -- the bootstrap DownloadWidget is not connected to
    SQLite/A1-A8
  no CompletedDownload/download-history persistent model; terminal task
    lifecycle persistence is what A8 provides

GATE:
PASS -- SQLite store with explicit schema versioning, future-schema
  refusal, transactional checkpoints with proven rollback, clean/unclean
  shutdown detection, conservative transient-state recovery matching the
  full state table, retry restoration using remaining (not full) delay,
  terminal-queue reconciliation, stale-.part safety verified against a
  real killed process, and a real (non-simulated) subprocess crash E2E --
  all green, zero regressions.

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A9 — Safe Partial Resume / Real Transfer Pause-Resume

COMMIT:
bc254f8
