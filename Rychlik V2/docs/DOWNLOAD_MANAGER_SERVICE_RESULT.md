PHASE:
Prompt A10 — Download Manager Application Service / Backend Facade

STATUS:
COMPLETE

BASELINE COMMIT:
2c71f1b

ACTUAL BASELINE HEAD:
2c71f1b59664fad6432024be02d8d6b66cf2de8b (worktree was clean at start)

FILES CHANGED:
  new:
    src/rychlik/core/download_manager_service.py
    tests/test_download_manager_service_lifecycle.py
    tests/test_download_manager_service_commands.py
    tests/test_download_manager_service_e2e.py
    tests/test_download_manager_service_events.py
    docs/DOWNLOAD_MANAGER_SERVICE.md
    docs/DOWNLOAD_MANAGER_SERVICE_RESULT.md
    docs/OPEN_VALIDATION_DEBT.md
  modified:
    src/rychlik/core/concurrent_runtime.py (+state_lock property, +request_cancel,
      +discard_resume_request, +discard_retry_schedule, unconditional
      per-reservation cancel_event wiring)

SERVICE TYPES:
  DownloadManagerService, DownloadManagerConfig, ManagerState,
  ManagerCommandResult, CommandStatus, AddDownloadResult, ManagerEvent,
  ManagerEventKind, DownloadManagerError / ManagerNotRunningError /
  UnknownDownloadError (reserved) / PersistenceCommandError /
  ManagerFaultedError.

SERVICE CONFIG:
  DownloadManagerConfig(max_active_transfers=2, retry_policy_config=
  RetryPolicyConfig(), resume_checkpoint_bytes_threshold=8MiB,
  database_path=None -> default_state_db_path(),
  progress_event_interval_seconds=0.25). All fields reuse existing
  subsystem configuration, nothing duplicated. Constructor also accepts
  acquisition_service/clock/monotonic/executor_factory/task_id_factory/
  failure_mapper overrides for testability (no hard-coded real home path
  in any test).

PUBLIC API:
  start()/stop()/add_download()/hold()/release_hold()/pause_transfer()/
  resume_transfer()/cancel()/retry_now()/set_priority()/move_before()/
  move_after()/snapshot()/item_snapshot()/subscribe()/unsubscribe()/
  state/last_recovery_report. Full table in
  docs/DOWNLOAD_MANAGER_SERVICE.md.

SERVICE LIFECYCLE:
  ManagerState: NEW/RUNNING/STOPPING/STOPPED/FAULTED (distinct from
  DownloadTaskState). start() idempotent on RUNNING; stop() idempotent on
  STOPPED/NEW. No constructor side effects -- start()/stop() are explicit.
  Optional context-manager sugar (__enter__/__exit__) over start()/stop().

STARTUP SEQUENCE:
  open+validate/migrate schema (A8) -> RestartRecovery (A8/A9) ->
  replace_all() the recovered canonical state -> rebuild runtime-only
  state (DownloadQueue.restore(), fresh ProgressRegistry, recovered retry
  seeds) -> construct DispatchCoordinator+ConcurrentDownloadRuntime
  (enable_pause_resume=True always) -> runtime.start() -> start the event
  pump thread -> RUNNING. Any exception -> FAULTED, re-raised.

SHUTDOWN SEQUENCE:
  STOPPING -> runtime.stop(timeout) (graceful) -> stop event pump thread
  -> mark_clean_shutdown() (skipped if the service was FAULTED) -> close
  the state store -> STOPPED.

ADD DOWNLOAD FLOW:
  create READY DownloadTask + DownloadRequest -> enqueue (A1) -> durable
  checkpoint of task+request+queue_entry inside the runtime's own
  state_lock -> only on success: notify_state_changed() + emit
  DOWNLOAD_ADDED -> return AddDownloadResult(task_id, queue_entry_id).

ADD DURABILITY MODEL:
  On checkpoint failure: in-memory queue/task/request mutation is rolled
  back (dequeue + dict deletion) before PersistenceCommandError is
  raised; no AddDownloadResult is ever returned and the runtime is never
  woken for an uncommitted download (proven by
  test_add_download_persistence_failure_rolls_back, which asserts
  snapshot().items stays empty after the injected failure).

QUEUE HOLD / RELEASE:
  hold()/release_hold() delegate to DownloadQueue.pause()/resume() only
  -- never touch DownloadTaskState. Idempotent (NO_OP on already-
  held/already-queued). REMOVED occurrence -> REJECTED. Rollback on
  checkpoint failure via the inverse queue operation.

TRANSFER PAUSE:
  pause_transfer() delegates entirely to
  ConcurrentDownloadRuntime.request_pause() (A9) -- never sets
  task.state directly. Returns ACCEPTED (async, §79) or REJECTED
  ("not currently transferring") based on PauseRequestOutcome.

TRANSFER RESUME:
  resume_transfer() validates Task PAUSED, then delegates to
  ConcurrentDownloadRuntime.request_resume() (A9, runtime-only intent,
  never persisted). Returns ACCEPTED; normal A3/A5 priority/capacity/
  queue-hold rules still apply afterward (proven live: capacity and
  queue-hold-blocks-resume E2E tests).

CANCEL:
  Waiting tasks (CREATED/RESOLVING/READY/RETRY_WAIT/PAUSED/VERIFYING/
  POST_PROCESSING) cancel synchronously: task.cancel() + queue.remove() +
  durable checkpoint (clearing any retry schedule/partial state too),
  APPLIED, no network. TRANSFERRING delegates to the new
  ConcurrentDownloadRuntime.request_cancel() (added this phase --
  previously the runtime had no cancel API at all; cancel_event already
  existed on the acquisition contract since Prompt 04.5, so this wiring
  needed no gating flag, unlike pause). Already-CANCELLED -> NO_OP;
  COMPLETED/FAILED -> REJECTED ("task is terminal").

RETRY NOW:
  Validates Task RETRY_WAIT, removes the in-memory A6 schedule
  (new ConcurrentDownloadRuntime.discard_retry_schedule() helper), calls
  task.mark_retry_ready(), durably checkpoints READY + schedule deletion,
  wakes the runtime -- never dispatches directly. Cannot bypass
  RetryPolicyConfig.max_attempts because A6 itself never leaves a task in
  RETRY_WAIT once its budget is exhausted (verified: retry_now() on a
  non-RETRY_WAIT task is REJECTED; the exhaustion path is A6's own,
  unchanged). Respects queue hold (task -> READY, QueueEntry stays
  PAUSED, no dispatch) -- dedicated test.

PRIORITY / REORDER:
  set_priority()/move_before()/move_after() delegate entirely to A1.
  Every entry in the affected priority band(s) is re-checkpointed (not
  just the moved entry) so DownloadQueue.restore()'s contiguous-position
  invariant never sees a stale sibling row after a restart -- proven by
  test_reorder_persists_across_restart. Cross-priority reorder ->
  REJECTED (A1's own CrossPriorityReorderError).

SNAPSHOT API:
  snapshot() returns A7's unmodified DownloadManagerSnapshot;
  item_snapshot() returns A7's DownloadViewSnapshot or None. No second
  GUI model invented. No mutable domain/runtime object ever returned.

EVENT / NOTIFICATION MODEL:
  ManagerEvent(kind, task_id=None, queue_entry_id=None), pure Python
  (subscribe()/unsubscribe(), no Qt Signal). Two sources: synchronous
  command-driven events (DOWNLOAD_ADDED/QUEUE_CHANGED/RESUME_REQUESTED/
  CANCELLED/RETRY_READY) emitted right after each command's checkpoint
  commits, and a background event-pump thread translating
  drain_completions()/drain_retry_events() into COMPLETED/FAILED/
  CANCELLED/PAUSED/RETRY_SCHEDULED/RETRY_READY/RETRY_EXHAUSTED, plus one
  coalesced PROGRESS_CHANGED tick per progress_event_interval_seconds
  while anything is TRANSFERRING.

EVENT BACKPRESSURE:
  No unbounded queue -- events dispatch synchronously and directly.
  Progress coalescing is bounded by construction (one tick per pump
  interval, proven by test_progress_events_are_coalesced_not_flooded:
  <=30 events over a ~0.6s transfer with a 0.05s interval, not one per
  64KiB chunk). Lifecycle-critical events are drained exactly once each
  from A5's own bounded lists, never dropped.

THREAD-SAFETY MODEL:
  Every public command synchronizes through
  ConcurrentDownloadRuntime.state_lock (new public property this phase,
  the same reentrant RLock A5's controller/workers already use).
  Subscriber callbacks run OUTSIDE both that lock and the subscribers'
  own bookkeeping lock (test_callback_can_safely_call_snapshot_no_deadlock
  proves snapshot() from inside a callback does not hang; a raising
  subscriber is caught and isolated,
  test_bad_subscriber_does_not_break_others_or_runtime).

PERSISTENCE COORDINATION:
  Every durable command: acquire state_lock -> validate -> mutate ->
  checkpoint -> release -> notify+emit (outside the lock). No caller ever
  needs to call state_store.checkpoint_task_state() or
  runtime.notify_state_changed() itself.

RUNTIME WAKE COORDINATION:
  Every command that can create new eligible work calls
  notify_state_changed() itself, always after (never before) its durable
  checkpoint succeeds.

FAULT MODEL:
  DownloadManagerError base; ManagerNotRunningError for any command
  outside RUNNING; ManagerFaultedError once start() has failed once
  (verified: further start()/any command both raise it, the database is
  never auto-reset); PersistenceCommandError for a checkpoint failure
  mid-command (in-memory state rolled back where a clean inverse exists).

TESTS ADDED: 46
  test_download_manager_service_lifecycle.py: 9
  test_download_manager_service_commands.py: 19
  test_download_manager_service_e2e.py: 10
  test_download_manager_service_events.py: 8

TESTS RUN: 690 passed, 0 failed, 0 skipped (644 baseline + 46 new). Full
  suite run three times for stability; the new E2E and event test files
  individually re-run 3-4 times during development with no flakiness.

REAL FACADE CONCURRENT HTTP E2E:
  PASS -- test_real_concurrent_downloads_through_facade: two real HTTP
  downloads (one plain, one with a real Content-Disposition-resolved
  filename) driven ONLY through add_download()/snapshot(), both complete,
  correct files on disk. test_facade_snapshot_shows_real_progress_during_
  transfer additionally proves live bytes_downloaded > 0 via snapshot()
  alone during a real in-flight transfer.

REAL FACADE PAUSE/RESUME E2E:
  PASS -- test_facade_real_pause_resume: real TRANSFERRING -> real
  pause_transfer() -> PAUSED -> real resume_transfer() -> real Range
  resume -> byte-exact ~510KB file, using ONLY the facade API.
  test_facade_resume_blocked_by_queue_hold proves hold() still blocks a
  pending resume_transfer() through the facade, exactly like the raw A9
  runtime.

REAL FACADE CANCEL E2E:
  PASS -- test_facade_cancel_waiting_no_network (a held, never-dispatched
  download cancels synchronously with no HTTP request) and
  test_facade_cancel_active_transfer (a real in-flight transfer is
  cooperatively cancelled via the new request_cancel() wiring, final file
  absent).

REAL FACADE RETRY E2E:
  PASS -- test_facade_real_retry_completes_automatically: a real 503-then-
  200 flaky endpoint, a retryable failure_mapper injected via the
  constructor, and ONLY add_download() called -- A6's automatic retry
  runs entirely inside the composed backend and the file completes.

REAL FACADE RESTART E2E:
  PASS -- test_facade_restart_preserves_state_and_completes (a fresh
  service instance against the same database recovers a never-dispatched
  download and completes it) plus
  test_recovered_start_restores_durable_state /
  test_reorder_persists_across_restart, all driving recovery exclusively
  through service.start() -- no test calls RestartRecovery directly.

A9 CRASH->RANGE VALIDATION DEBT:
OPEN
  Not executed during A10 either (A10 does not touch acquisition/resume
  mechanics). Tracked explicitly in docs/OPEN_VALIDATION_DEBT.md
  (A9-CRASH-RANGE-E2E) with blocking scope: not blocking A10, not
  blocking A11 (GUI), blocking the eventual beta/release gate.

EXISTING TEST REGRESSIONS:
NONE -- all 644 pre-A10 tests pass unmodified.

BUGS FOUND:
  1. (Discovered during design, not a test failure) The runtime had no
     cancel API at all before this phase -- `cancel_event` was already
     part of the acquisition contract since Prompt 04.5, but
     ConcurrentDownloadRuntime never created or wired one per reservation,
     so an ACTIVE transfer could only ever be cancelled by calling
     DispatchCoordinator.dispatch() directly (as A4/A9's own tests already
     did), never through the runtime a facade would actually use. Fixed
     by adding a `_cancel_events` map populated unconditionally in
     `_reserve_and_submit_locked` (safe because `cancel_event` was already
     part of every acquisition test double's declared signature, unlike
     the newer `pause_event`) and a new public `request_cancel()`.
  2. (Self-caught while writing the reorder tests) The initial
     set_priority()/move_before()/move_after() implementation only
     checkpointed the single moved entry's row. Since A1's `set_priority`/
     `_move` renumber every sibling's `position` within a band as a side
     effect, this would have left stale sibling rows in the database --
     harmless until a restart, at which point `DownloadQueue.restore()`'s
     contiguous-position validation could reject the persisted band as
     corrupt. Fixed with `_checkpoint_priority_band()`, re-checkpointing
     every live entry in the affected band(s); verified with a dedicated
     restart test (test_reorder_persists_across_restart).
  3. (Caught by a test's own TypeError, not a design flaw) An early draft
     of test_progress_events_are_coalesced_not_flooded passed
     progress_event_interval_seconds as both a positional default inside
     the shared `_manager()` helper and an explicit override, causing a
     duplicate-keyword TypeError. Fixed by removing the redundant explicit
     override at the call site.

KNOWN LIMITATIONS:
  move_before()/move_after() does not attempt exact in-memory rollback on
    a persistence failure (add_download/hold/release_hold/set_priority
    all do)
  pause_transfer()/cancel(active)/resume_transfer() are asynchronous by
    design; no bounded "wait until physically applied" call exists
  no dedicated TRANSFER_STARTED event kind (observable via the coalesced
    PROGRESS_CHANGED tick or a direct snapshot() poll instead)
  A9's combined SIGKILL -> Range-resume real subprocess E2E remains OPEN
    (see docs/OPEN_VALIDATION_DEBT.md) -- not closed by this phase
  no GUI wiring, no yt-dlp backend, no bandwidth limiting, no download-
    history model, no Share-by-Link ownership

GATE:
PASS -- one headless DownloadManagerService composes A1-A9 with no Qt
  dependency; startup/recovery ordering and graceful shutdown are fully
  owned by the service; add_download is durable-before-dispatch with
  proven rollback; queue hold and real transfer pause/resume remain
  architecturally distinct and both work through the facade with A9's
  ordering/capacity guarantees intact; cancel works for both waiting and
  active occurrences; retry_now cannot bypass the attempt budget;
  priority/reorder use A1 semantics and persist correctly across a
  restart; snapshots expose only the existing immutable A7 models with no
  object/credential/path leakage; the notification mechanism is
  framework-neutral, callback-reentrant-safe for snapshot(), isolates a
  bad subscriber, and bounds progress-event volume; persistence failures
  never publish false success and never let unpersisted work reach the
  network; five distinct real, non-mock HTTP E2E scenarios pass using
  ONLY the public facade API; no thread leaks after stop(); all previous
  tests remain green; worktree clean (pending this commit).

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A11 — Functional Download Manager GUI Integration

COMMIT:
5696741
