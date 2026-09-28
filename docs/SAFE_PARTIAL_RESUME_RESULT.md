PHASE:
Prompt A9 — Safe Partial Resume / Real Transfer Pause-Resume

STATUS:
COMPLETE (with explicitly scoped-down items -- see KNOWN LIMITATIONS)

BASELINE COMMIT:
b10fdca

ACTUAL BASELINE HEAD:
b10fdca6cf66ca86887aba0555aff06409bb4ac9 (worktree was clean at start)

FILES CHANGED:
  new:
    src/rychlik/core/partial_transfer.py
    tests/test_partial_transfer.py
    tests/test_direct_http_resume.py
    tests/test_scheduler_policy_resume.py
    tests/test_dispatch_coordinator_resume.py
    tests/test_runtime_pause_resume_e2e.py
    docs/SAFE_PARTIAL_RESUME.md
    docs/SAFE_PARTIAL_RESUME_RESULT.md
  modified:
    src/rychlik/acquisition/contracts.py        (+DownloadPaused, +ResumeRequest)
    src/rychlik/acquisition/direct_http.py       (pause/resume mechanics)
    src/rychlik/acquisition/acquisition_service.py (thin passthrough)
    src/rychlik/core/scheduler_policy.py         (+DispatchKind, RESUME candidates)
    src/rychlik/core/dispatch_coordinator.py     (kind-aware, PAUSED outcome, resume bridge)
    src/rychlik/core/concurrent_runtime.py       (request_pause/request_resume, enable_pause_resume)
    src/rychlik/core/progress.py                 (+begin_attempt(initial_bytes=0))
    src/rychlik/core/state_store.py              (schema v2, partial_transfers table)
    src/rychlik/core/restart_recovery.py         (PAUSED-with-valid-partial rule)
    tests/http_fixture_server.py                 (+/resumable/<key> Range/ETag/206/416/drop fixture)
    tests/test_state_store.py                    (+4 partial/migration tests)
    tests/test_restart_recovery.py               (+2 PAUSED-recovery tests)
    tests/test_progress_registry.py              (+3 initial_bytes tests)
    docs/PERSISTENT_DOWNLOAD_STATE.md            (PAUSED recovery rule updated, §154)
    docs/DOWNLOAD_PROGRESS_RUNTIME.md            (resume offset seeding, §155)

SCHEMA VERSION:
  2 (was 1). Real transactional v1->v2 migration verified against a
  hand-built authentic v1 database (test_schema_v1_to_v2_real_migration_
  preserves_data): all four original A8 tables/rows untouched, the new
  partial_transfers table created empty, schema_version bumped to 2, all
  inside one transaction. Future-schema refusal (UnsupportedStateSchemaError)
  still checked before any table is touched.

V1->V2 MIGRATION:
  CREATE TABLE IF NOT EXISTS partial_transfers(...) inside the same
  transaction as the schema_version check/bump -- a real structural
  addition, not a relabel. Rollback-on-failure preserves the original v1
  database byte-for-byte (inherited from A8's existing transaction
  machinery, exercised freshly for this migration path).

PARTIAL TRANSFER TYPES:
  rychlik.core.partial_transfer: ValidatorKind (NONE/STRONG_ETAG/
  LAST_MODIFIED), Validator, PartialTransferState, ResumeDecisionKind
  (NO_PARTIAL/FULL_RESTART/ATTEMPT_RANGE), ResumeDecision,
  PartialTransferConsistencyError. rychlik.acquisition.contracts:
  DownloadPaused (AcquisitionError sibling, not subclass, of
  DownloadCancelled), ResumeRequest.

DURABLE PARTIAL FIELDS:
  queue_entry_id (primary occurrence identity, never task_id alone),
  task_id, attempt_count_snapshot, temp_path, final_path, durable_bytes,
  expected_total_bytes, validator_kind, validator_value, prefix_sha256,
  created_at, updated_at.

CHECKPOINT POLICY:
  Periodic during transfer (default 8 MiB threshold, configurable via
  DispatchCoordinator(resume_checkpoint_bytes_threshold=...)); forced
  unconditionally on confirmed pause; cleared on COMPLETED/CANCELLED/
  non-retryable FAILED; preserved across RETRY_WAIT. Never per A7
  progress chunk.

CHECKPOINT ORDER:
  write chunk -> flush -> os.fsync() -> THEN persist durable_bytes/
  prefix_sha256 to SQLite. A durable offset is never committed before the
  corresponding file bytes are durable on disk.

PREFIX INTEGRITY MODEL:
  Incremental SHA-256 maintained live during download (no re-read per
  checkpoint). On resume, the already-locally-validated prefix is
  re-hashed once (accepted O(partial size) cost) to seed the running
  digest before new bytes are appended. On local validation
  (validate_local_partial), the on-disk prefix is independently
  recomputed and compared against the persisted prefix_sha256; mismatch
  -> FULL_RESTART.

PATH OWNERSHIP POLICY:
  temp_path must resolve to a plain file directly inside the expected
  destination_dir with the expected .part suffix, and must not be a
  symlink. Violations raise PartialTransferConsistencyError rather than
  being silently rejected/ignored -- proven never to touch/delete/modify
  an out-of-scope file (test_path_ownership_rejects_outside_destination,
  test_path_ownership_rejects_symlink_escape both assert the victim file
  is untouched afterward).

TRANSFER CONTROL:
  Plain stdlib threading.Event pair (cancel_event, pause_event), checked
  cooperatively once per 64 KiB chunk inside DirectHttpAcquisition's
  existing read loop -- no thread suspension, no Qt dependency. Cancel is
  checked before pause each iteration, so a simultaneous cancel+pause
  request always produces CANCELLED, never PAUSED (real HTTP E2E test:
  test_cancel_wins_and_clears_partial).

PAUSE SEMANTICS:
  DownloadPaused raised only after the loop has genuinely stopped and (if
  resume tracking is enabled) the forced durable checkpoint write has
  completed. .part + durable partial state preserved (never cleared) on
  pause, unlike cancel. Task transitions TRANSFERRING -> PAUSED only
  after this; QueueEntry stays QUEUED (never removed). Cooperative pause
  latency is bounded by the current blocking read + chunk size, documented
  as non-instantaneous.

RESUME REQUEST MODEL:
  ConcurrentDownloadRuntime.request_resume(queue_entry_id) records
  runtime-only intent in an in-memory set (never persisted -- safely lost
  on restart, task stays PAUSED). Consumed the moment SchedulerPolicy
  actually selects it as a RESUME candidate (reservation already prevents
  duplicate submission). Idempotent: requesting twice before consumption
  is one pending intent.

DISPATCH KIND MODEL:
  DispatchCandidate.kind: DispatchKind (START default / RESUME), additive.
  SchedulerPolicy.plan(resume_requested_queue_entry_ids=frozenset())
  additive. RESUME candidates require QueueEntry.QUEUED + Task PAUSED +
  queue_entry_id in the resume-requested set; QueueEntry.PAUSED still
  blocks selection either way. DispatchCoordinator requires Task READY
  for START (start_transfer()) vs. Task PAUSED for RESUME
  (resume_transfer()).

RESUME CAPACITY / ORDERING:
  RESUME candidates are interleaved in DownloadQueue's exact canonical
  order (queue.eligible_entries()) and consume the same
  max_active_transfers/reservation accounting as START -- no hidden
  priority, no extra slots (real E2E: test_resume_waits_for_free_capacity,
  test_resume_does_not_bypass_priority_real).

ATTEMPT COUNT SEMANTICS:
  START (fresh or crash-recovered TRANSFERRING->READY) increments
  attempt_count via start_transfer(); manual RESUME (PAUSED->TRANSFERRING
  via resume_transfer()) leaves attempt_count unchanged. Verified by a
  real E2E test asserting attempt_count stays 1 across a full manual
  pause+resume+complete cycle, and separately that a retry (RETRY_WAIT ->
  READY -> new attempt) increments to 2 even when it reuses validated
  partial bytes.

VALIDATOR POLICY:
  classify_validator(etag, last_modified): strong ETag preferred, then
  Last-Modified, else NONE. A weak ETag (W/"...") is never treated as
  strong. NONE means no Range attempt is made at all -- full restart is
  the only path (real E2E: test_no_validator_forces_full_restart).

CONTENT-ENCODING POLICY:
  A resumed 206 response with Content-Encoding other than identity/absent
  is treated as not safely resumable and falls back to full restart. This
  code path exists and is checked in _validate_content_range's sibling
  encoding_ok check, but is NOT exercised by a dedicated real-gzip E2E
  test (the fixture server never sends non-identity encoding) -- verified
  by code review only, flagged honestly rather than silently claimed.

RANGE REQUEST:
  Range: bytes=<durable_bytes>-, If-Range: <validator>,
  Accept-Encoding: identity -- sent only when durable_bytes > 0 and a
  non-NONE validator exists.

206 VALIDATION:
  Content-Range start must exactly equal the requested durable_bytes; end
  >= start; total (if given) must be consistent with any previously-known
  expected_total_bytes. Validator in the 206 response must match the
  persisted validator exactly. Any failure -> ONE clean full restart
  request (never zero, never more than one), offset reset to 0, filename
  re-resolved from the new response.

200 FALLBACK:
  A 200 response to a Range attempt (server ignored Range or If-Range
  didn't match) is used directly as a fresh full download -- never
  appended to the old prefix (real E2E:
  test_server_ignores_range_falls_back_to_full_restart, proves byte-exact
  final output even when server ETag changed mid-test).

416 POLICY:
  Never inferred as "already complete." Triggers the identical
  one-clean-full-restart fallback as a rejected/malformed 206 (real E2E:
  test_416_falls_back_to_full_restart).

LOCAL PARTIAL VALIDATION:
  validate_local_partial(): ownership check (raises on violation) -> size
  check (shorter than durable_bytes -> invalid; longer -> truncated in
  place then hashed) -> SHA-256 comparison. All three failure modes have
  dedicated real-filesystem tests, including one proving an out-of-scope
  file is never touched.

RETRY PARTIAL RESUME:
  A real E2E test (test_real_retry_reuses_validated_partial_bytes) drops
  a real HTTP connection after 100,000 of ~510,000 bytes (deterministic,
  once, via the fixture's drop_after_bytes/drop_once_key), with a 50,000-
  byte checkpoint threshold so a durable partial IS written before the
  drop. The retry's own request is asserted to start at or after that
  durable offset (never from 0), and the final file is byte-exact with
  attempt_count == 2.

CRASH PARTIAL RESUME:
  NOT covered by a combined real-subprocess-crash + Range-resume E2E test
  in this phase -- see KNOWN LIMITATIONS for the explicit reasoning. The
  underlying recovery logic (RestartRecovery's PAUSED-with-valid-partial
  path, and plan_resume()'s attempt-generation safety check) IS covered
  by focused non-subprocess tests.

PAUSED RESTART POLICY:
  Updated A8 rule (docs/PERSISTENT_DOWNLOAD_STATE.md, §154): persisted
  PAUSED recovers to PAUSED unchanged only if its queue occurrence has a
  partial that passes the exact same local validation real dispatch would
  apply; otherwise recovers to READY, exactly like every other transient
  state. Recovery never issues a network request either way. Two
  dedicated tests: valid-partial-stays-paused, no-partial-recovers-ready.

A3 INTEGRATION:
  DispatchKind + resume_requested_queue_entry_ids, both additive
  (frozenset() default = exact prior behavior). 7 new tests, all 29
  pre-A9 SchedulerPolicy tests pass unmodified.

A4 INTEGRATION:
  candidate.kind branching in _prepare(); DispatchOutcome.PAUSED added;
  DownloadPaused handling; load_partial/save_partial/clear_partial +
  pause_event all additive optional parameters, all None/absent by
  default. acquire_kwargs built conditionally so a test double
  implementing only the pre-A9 acquire() signature is never broken by an
  unconditionally-passed new kwarg (a real regression caught and fixed
  during this phase -- see BUGS FOUND). All 42 pre-A9
  DispatchCoordinator/checkpoint tests pass unmodified.

A5 INTEGRATION:
  request_pause()/request_resume(), enable_pause_resume flag (default
  False = exact prior behavior -- no pause_event ever constructed unless
  explicitly enabled), per-occurrence pause_event map, resume-requested
  set, _load_partial/_save_partial/_clear_partial bridging methods. All
  pre-A9 ConcurrentDownloadRuntime/retry-runtime tests pass unmodified.

A7 INTEGRATION:
  ProgressRegistry.begin_attempt(initial_bytes=0) additive. A resumed
  attempt seeds one sample at (now, initial_bytes) so speed history never
  counts pre-resume bytes as an instant rate; a fallback-to-zero resume is
  just begin_attempt(initial_bytes=0), naturally showing progress reset.
  3 new dedicated tests plus a real E2E test asserting cumulative resumed
  progress values.

A8 INTEGRATION:
  RestartRecovery's PAUSED handling now consults partial_transfers +
  requests via plan_resume() (local-only, no network) instead of always
  normalizing to READY. state_store.py's schema bump/migration described
  above. Zero changes to A8's TRANSFERRING/RESOLVING/VERIFYING/
  POST_PROCESSING/RETRY_WAIT/terminal recovery rules.

TESTS ADDED: 61
  test_partial_transfer.py: 20
  test_direct_http_resume.py: 11
  test_scheduler_policy_resume.py: 7
  test_dispatch_coordinator_resume.py: 6
  test_runtime_pause_resume_e2e.py: 8
  test_state_store.py: +4
  test_restart_recovery.py: +2
  test_progress_registry.py: +3

TESTS RUN: 644 passed, 0 failed, 0 skipped (full suite; 583 baseline + 61
  new). Direct-HTTP resume tests and runtime pause/resume E2E tests each
  re-run 3-4 times during development with no flakiness observed.

REAL MANUAL PAUSE E2E: PASS
  test_real_manual_pause_e2e -- real HTTP (~510KB body, paced writes),
  real pause mid-transfer, Task PAUSED, QueueEntry stays QUEUED,
  reservation released, durable partial row with 0 < durable_bytes <
  total, .part present, final file absent.

REAL MANUAL RESUME E2E: PASS
  test_real_manual_resume_e2e -- resumes the above via request_resume(),
  real Range/If-Range observed server-side starting at a nonzero offset,
  attempt_count stays 1 throughout, byte-exact final file, partial
  cleared on completion.

REAL RETRY RESUME E2E: PASS
  test_real_retry_reuses_validated_partial_bytes -- see RETRY PARTIAL
  RESUME above.

REAL RANGE-REJECTION FALLBACK E2E: PASS
  test_server_ignores_range_falls_back_to_full_restart (200 fallback),
  test_wrong_206_start_falls_back_to_full_restart (malformed 206),
  test_416_falls_back_to_full_restart (416) -- all three produce a
  byte-exact final file via exactly one clean full restart.

REAL VALIDATOR-CHANGE E2E: PASS
  test_server_ignores_range_falls_back_to_full_restart doubles as the
  validator-change proof: the partial remembers an old ETag, the server
  now serves a different one, If-Range mismatch -> full 200 -> byte-exact
  new-representation output, no old/new concatenation.

REAL PROCESS-CRASH PARTIAL-RESUME E2E:
  NOT PERFORMED as a combined test in this phase -- see CRASH PARTIAL
  RESUME and KNOWN LIMITATIONS above for the explicit scoping rationale.
  A8's own real SIGKILL crash-recovery E2E (docs/PERSISTENT_DOWNLOAD_STATE
  _RESULT.md) remains valid and unmodified; it exercises the
  TRANSFERRING->READY path, not the new PAUSED-with-partial path.

ARTIFACT CONTINUITY:
  Not re-tested as a dedicated A9 test (no change to CompletedDownload/
  Artifact.from_completed_download() -- final_path/display_name/size are
  produced identically whether or not a resume occurred). Existing A7/A8
  Artifact continuity tests remain valid and pass unmodified.

EXISTING TEST REGRESSIONS:
NONE -- all 583 pre-A9 tests pass unmodified after two intermediate
  regressions were caught and fixed during development (see BUGS FOUND);
  full suite re-run repeatedly, stable at 644/644 throughout.

BUGS FOUND:
  1. (Caught by test_cancel_does_not_produce_completed_download and
     test_real_cancellation_e2e failing after the direct_http.py rewrite)
     The rewritten exception handling initially treated DownloadCancelled
     the same as DownloadPaused (preserving the .part file). This
     silently reversed Prompt 04.5's existing cancel behavior (which
     always deletes the .part). Fixed by giving DownloadCancelled its own
     except clause that deletes the .part and clears any durable partial
     state, distinct from DownloadPaused's deliberate preservation.
  2. (Caught by ~30 test failures across test_concurrent_runtime.py,
     test_dispatch_coordinator.py, test_retry_runtime.py, and
     test_dispatch_checkpoint.py after wiring pause_event/resume through
     DispatchCoordinator.dispatch()) Initially `pause_event=pause_event,
     resume=resume_request` were passed unconditionally to
     self._acquisition_service.acquire(...). Every pre-A9 test double
     implementing only the historical acquire(request, *,
     progress_callback=None, cancel_event=None) signature raised
     TypeError, which the coordinator's own `except Exception` clause
     silently converted into a DispatchExecutionError, masking the real
     cause. Root-caused to the missing conditional; fixed by building
     `acquire_kwargs` and only including `pause_event`/`resume` when
     non-None -- restoring the exact pre-A9 call shape whenever the
     feature is unused. A parallel issue in ConcurrentDownloadRuntime
     (pause_event was constructed unconditionally for every reservation)
     was fixed with the `enable_pause_resume` opt-in flag rather than
     relying solely on the None-check, since a real (non-test-double)
     AcquisitionService would otherwise always receive a live Event.
  3. (Self-caught, not a test failure) The initial fixture server's
     forced-416 config applied regardless of whether the request actually
     carried a Range header, so the "discard resume, do ONE full restart"
     retry request (deliberately sent without Range) ALSO got 416,
     causing an infinite-looking single failure instead of a successful
     fallback. Fixed by gating force_status==416 on `range_header` being
     present, matching real server behavior (416 only makes sense as a
     response to an actual Range request).
  4. (Self-caught while writing the pause/resume tests) The initial pause
     test used the existing ~17KB NORMAL_BODY with the unchanged 64 KiB
     acquisition chunk size -- reproducing the exact same A7-documented
     "iter_content buffers a small body whole" property, so a "pause"
     always landed after the single, only chunk had already been fully
     received (durable_bytes == 0 every time). Fixed by using a ~510KB
     body with tuned (16KB writes / 20ms delay) fixture pacing so a pause
     request genuinely lands between several real 64KB chunks, and
     documenting the underlying chunk-size property in the test file
     itself rather than re-discovering it silently.

A17 ADDENDUM (2026-09-28):
  The "no combined real-process-crash + partial-resume E2E test" limitation
  noted below was closed during Prompt A17 (Device Mode Release Hardening),
  not in this phase -- validation debt closed during A17, not retroactively
  claimed here. See tests/test_a9_crash_range_e2e.py
  (test_a9_crash_range_e2e) and docs/DEVICE_MODE_RELEASE_HARDENING_RESULT.md
  for the full combined-chain evidence (real SIGKILL against a real
  Range/ETag-capable fixture route, durable checkpoint observed before the
  kill, restart recovery, and a real subsequent HTTP Range/If-Range request
  using the persisted durable_bytes/validator). docs/OPEN_VALIDATION_DEBT.md
  now records A9-CRASH-RANGE-E2E as CLOSED.

KNOWN LIMITATIONS:
  no exactly-once claim -- SQLite + filesystem + HTTP are not one atomic
    transaction (inherited from A8, restated here for this phase's
    checkpoint-ordering guarantees specifically)
  no segmented/multipart/parallel-range downloading
  pause latency bounded by the current blocking read + 64 KiB chunk size,
    never instantaneous
  Content-Encoding safety exists in code but is not exercised by a real
    non-identity-encoding E2E test (fixture never sends gzip)
  no combined real-process-crash + partial-resume E2E test -- explicitly
    scoped out of this phase; the underlying PAUSED-with-valid-partial
    recovery logic is covered by focused non-subprocess tests instead
    (CLOSED during Prompt A17 -- see A17 ADDENDUM above)
  request_resume() while QueueEntry is queue-paused has no expiry/timeout
    (matches the prompt's own "simple and deterministic" suggested policy)
  no GUI Pause/Resume controls
  resume checkpoint threshold is per-DispatchCoordinator-instance only,
    not configurable per individual download

GATE:
PASS -- no new QueueEntry/DownloadTask states; pause is cooperative and
  confirmed-before-PAUSED; resume respects queue/priority/capacity and
  never increments attempt_count; schema migrates transactionally with
  rollback-safety proven against a real v1 database; partial metadata is
  durable with durable_bytes distinct from raw file size and SHA-256
  integrity evidence; path ownership is enforced and proven not to touch
  out-of-scope files; .part existence alone never enables resume; a
  validator is required and a weak ETag is insufficient; Range starts
  only from validated durable_bytes with If-Range sent; 206/200/416 are
  all handled per spec with a byte-exact final file in every case; retry
  and (locally) crash-recovered generations may reuse validated partial
  bytes while an old/stale generation cannot; PAUSED survives restart
  only with a locally-validated partial; A7 progress supports resume
  offset without fake speed; real manual pause/resume and retry-resume
  HTTP E2E all pass; all previous tests remain green; worktree clean
  (pending this commit).

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A10 — Download Manager Application Service / Backend Facade

COMMIT:
68cbf6e
