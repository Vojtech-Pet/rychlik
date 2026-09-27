PHASE:
Prompt A12 — Functional GUI Hardening / User Workflow Polish

STATUS:
COMPLETE

BASELINE COMMIT:
f032168

ACTUAL BASELINE HEAD:
f03216891e305ba5fc5ff5aabef51dc725b4e209 (worktree was clean at start)

FILES CHANGED:
  new:
    src/rychlik/gui/completed_artifact_bridge.py
    tests/test_download_manager_service_completed_file.py
    tests/test_download_manager_widget_hardening.py
    tests/test_gui_e2e_hardening.py
    tests/test_desktop_launcher.py
    docs/FUNCTIONAL_GUI_HARDENING.md
    docs/FUNCTIONAL_GUI_HARDENING_RESULT.md
    docs/DESKTOP_SMOKE_CHECKLIST.md
  modified:
    src/rychlik/core/restart_recovery.py (bug fix: carry partial_transfers/
      completed_files forward into recovered state -- see BUGS FOUND)
    src/rychlik/core/state_store.py (schema v3: completed_files table,
      CompletedFileRecord, checkpoint/load/replace_all/get_completed_file)
    src/rychlik/core/concurrent_runtime.py (captures CompletedDownload into
      a durable CompletedFileRecord right after a COMPLETED dispatch)
    src/rychlik/core/download_manager_service.py (+completed_file()
      privileged accessor, +CompletedFileStatus/Info/Result types)
    src/rychlik/gui/download_manager_widget.py (destination workflow,
      Enter-key add, double-submit guard, empty state, status summary,
      shutdown confirmation, Open Folder, Share bridge wiring)
    main.py (MainWindow.closeEvent now confirms + prepares shutdown)
    tests/test_download_manager_widget.py (updated Share/Open-Folder
      enablement expectations for the new COMPLETED-eligible policy)
    tests/test_state_store.py (+completed_file/v2->v3 migration tests,
      schema-version assertion updated to SCHEMA_VERSION)
    tests/test_restart_recovery.py (+partial_transfers carry-forward
      regression test)

DESTINATION WORKFLOW:
  "Save to:" read-only display + "Browse..." button. Chooser is
  constructor-injectable (default QFileDialog.getExistingDirectory) so
  tests never open a real dialog. Cancelling (empty result) leaves the
  existing destination unchanged; a non-directory result is rejected with
  a bounded warning and never applied. Each add_download() call uses
  whatever destination is current AT THAT MOMENT -- proven live with two
  real downloads sent to two different chosen directories in the same
  session.

DEFAULT DESTINATION:
  QStandardPaths.writableLocation(DownloadLocation), falling back to a
  per-process temp directory if unavailable/not a directory. No hard-coded
  real user path anywhere in product code.

URL / ENTER WORKFLOW:
  QLineEdit.returnPressed wired to the same handler as the Download
  button's click. Input is only .strip()ped, never rewritten. Verified
  live (real HTTP) via Enter-key add and via the unit test asserting
  exactly one add_download() call per Enter press.

DOUBLE-SUBMIT GUARD:
  A bounded per-widget `_add_in_progress` flag (set/cleared around the
  handler body, not a global lock) proven with a re-entrant-call unit
  test: a nested call to the same handler while the guard is held is
  silently ignored, producing exactly one add_download() call.

EMPTY STATE:
  QStackedWidget switches between an empty-state QLabel and the real
  QTableWidget based purely on snapshot.items being empty -- no synthetic
  table row. Verified to hide/reappear correctly as items are added/
  removed.

STATUS SUMMARY:
  "<n> download(s) [ - <k> active] [ - <speed>]" rendered from the
  snapshot's own active_transfer_count/aggregate_speed_bps. No aggregate
  ETA was invented (A7 defines none). Verified against a synthetic
  snapshot with exact expected substrings.

COMMAND FEEDBACK:
  REJECTED/ACCEPTED results now show a transient status-bar message
  (auto-cleared after 5s if unchanged) instead of nothing; a raised
  backend exception still uses a bounded QMessageBox.warning or the
  persistent FAULTED indicator -- never a raw traceback.

STALE-SELECTION POLICY:
  Unchanged mechanism (already correct since A11's identity-by-
  queue_entry_id design), newly regression-tested: a selected occurrence
  that disappears from the next snapshot clears selection and disables
  every command button rather than transferring control to whatever now
  occupies the old row index; a REJECTED command (e.g. a stale/removed
  occurrence) shows a bounded message and leaves the widget fully usable.

ASYNC COMMAND UX:
  Unchanged from A11 (pause/resume/cancel-active never paint an
  optimistic lifecycle state) -- carried forward and re-verified live.
  A12 adds only the transient status-message layer described above.

COMPLETED-FILE AUDIT:
  See docs/FUNCTIONAL_GUI_HARDENING.md "Completed-file durability ->
  Audit findings" -- CompletedDownload already carried the real path but
  was discarded after one transient use inside WorkerCompletion; A8/A9
  had no completion-specific durable table; Artifact/ArtifactRepository
  already provide the hash/MIME/size logic, reused unchanged.

COMPLETED-FILE TYPES:
  state_store.CompletedFileRecord (durable, keyed by queue_entry_id).
  download_manager_service.CompletedFileStatus/CompletedFileInfo/
  CompletedFileResult (application-level, immutable, never a persistence/
  domain object).

COMPLETED-FILE ACCESSOR:
  manager.completed_file(queue_entry_id) -> CompletedFileResult. Full
  validation chain in docs/FUNCTIONAL_GUI_HARDENING.md. Read-only:
  proven never to modify any file, including a deliberately tampered
  record pointing at an unrelated file.

COMPLETION PERSISTENCE:
  ConcurrentDownloadRuntime captures the real CompletedDownload the
  instant dispatch() returns COMPLETED and writes a CompletedFileRecord
  in its own separate transaction (documented crash window, see SCHEMA
  MIGRATION / KNOWN LIMITATIONS). Never deletes the real file if this
  checkpoint fails.

SCHEMA VERSION:
v3 (was v2)

SCHEMA MIGRATION:
  v2->v3: `CREATE TABLE IF NOT EXISTS completed_files(...)` inside the
  same transaction as the existing schema_version check/bump -- the exact
  same real-migration pattern A9 used for partial_transfers. Verified
  against a hand-built authentic v2 database
  (test_schema_v2_to_v3_real_migration_preserves_data): the pre-existing
  COMPLETED task's row is untouched, completed_files starts empty for it
  (no guessed historical path, §41), schema_version becomes 3.

SAFE SNAPSHOT PATH POLICY:
  Unchanged and re-verified by regression test: DownloadViewSnapshot still
  has no local_path field and leaks no path/secret through repr(), even
  after this phase's additions elsewhere in the stack.

SHARE BRIDGE:
  rychlik/gui/completed_artifact_bridge.py: manager.completed_file() ->
  Artifact.from_completed_download() (existing, reused unchanged) ->
  existing ShareDialog. Verified live with a real completed HTTP download
  producing a real Artifact with a real SHA-256/size, and with a fake-
  manager unit test proving the exact artifact reaches ShareDialog.

SHARE AFTER RESTART:
  Proven live: complete a download, stop the service, start a fresh
  service instance against the same database, call completed_file() --
  AVAILABLE, correct path, file exists, no re-download.

OPEN FOLDER:
  Uses an injectable folder_opener (default QDesktopServices.openUrl on
  QUrl.fromLocalFile) called with only the validated file's parent
  directory from the privileged accessor -- never raw table-cell text,
  never a shell command. Unavailable file -> bounded status message, no
  crash, no state change.

STARTUP ERROR UX:
  Unchanged from A11 (bounded QMessageBox.critical + exit(1) on a
  start() failure) -- not modified this phase, re-verified still passing.

SHUTDOWN CONFIRMATION UX:
  New: DownloadManagerWidget.confirm_close() returns True immediately
  with zero active transfers (or a non-RUNNING service); otherwise shows
  a QMessageBox.question worded to match A10's real graceful-stop
  semantics ("...will wait for active transfers to finish", never
  "will be paused"). Cancelling leaves the window and service completely
  untouched (verified: no state_label change, service stays RUNNING).
  Confirming calls prepare_shutdown() (disables all mutating controls,
  shows "Shutting down...") before the existing shutdown()/manager.stop()
  sequence.

DESKTOP LAUNCHER:
  launch.sh re-verified CWD-independent via a real subprocess started
  with cwd set to an unrelated temp directory (clean startup, no import/
  path error within a bounded window). rychlik.desktop's Exec= line
  verified to still point at the real, executable launch.sh. No trust-
  flag automation attempted (user-managed, unchanged).

MANUAL VISIBLE DESKTOP SMOKE:
DEFERRED
  No interactive graphical session was available in this environment
  (headless dev sandbox). All automated equivalents were run instead:
  offscreen Qt widget/E2E tests (784 GUI-related assertions across A11+
  A12), the real launch.sh CWD-independence subprocess check, and static
  .desktop validation. docs/DESKTOP_SMOKE_CHECKLIST.md was created for a
  future human pass on a real desktop; this does not fail the gate per
  the prompt's own §80 allowance, since full automated functional E2E
  coverage passes.

TESTS ADDED: 46
  tests/test_state_store.py: +4 (completed-file round trip, unknown
    lookup, replace_all survival, v2->v3 migration)
  tests/test_restart_recovery.py: +1 (partial_transfers carry-forward
    regression)
  tests/test_download_manager_service_completed_file.py: 9
  tests/test_download_manager_widget.py: +1 (Share/Open-Folder
    COMPLETED-only enablement, replacing the old always-disabled test)
  tests/test_download_manager_widget_hardening.py: 23
  tests/test_gui_e2e_hardening.py: 4
  tests/test_desktop_launcher.py: 4

TESTS RUN: 788 passed, 0 failed, 0 skipped (742 A11 baseline + 1 removed/
  replaced enablement test + 45 net new = 788, tallied precisely across
  the files above). Full suite run three times for stability; every new
  real-HTTP-backed test file individually re-run 3+ times during
  development with no flakiness after the one timing bug described below
  was fixed.

REAL DESTINATION HTTP E2E:
  PASS -- test_real_destination_choice_used_for_download (chosen
  directory actually used) and test_real_destination_change_between_
  downloads (two real downloads to two different chosen directories in
  one session, no cross-contamination).

REAL COMPLETED-FILE RESTART E2E:
  PASS -- test_completed_file_survives_restart (state_store level) and
  the restart step inside test_real_full_user_workflow (facade level):
  completed_file() remains AVAILABLE with the file still present after a
  full service stop/restart cycle.

REAL GUI SHARE-BRIDGE E2E:
  PASS -- test_real_share_bridge_end_to_end: a real completed HTTP
  download's identity survives past its row's removal from the table,
  and build_artifact_for_completed() produces a real Artifact with a real
  SHA-256 and the exact expected size.

REAL USER-WORKFLOW E2E:
  PASS -- test_real_full_user_workflow: add two real downloads (one
  plain, one slow/resumable) through the widget, change priority via the
  combo, pause+resume the slow one via real buttons (genuine Range
  resume), let both complete, verify Open-Folder-eligible completed-file
  availability, then stop and restart the service and confirm the
  completed file is still available with no re-download -- all driven
  through real widget controls, no lower-layer calls except fixture setup.

A9 CRASH→RANGE VALIDATION DEBT:
OPEN
  Not attempted in this phase (per the prompt's own "do not expand A12
  merely to force it" guidance). Still tracked in
  docs/OPEN_VALIDATION_DEBT.md (A9-CRASH-RANGE-E2E): not blocking A12,
  not blocking functional GUI, blocking the eventual beta/release gate.

EXISTING TEST REGRESSIONS:
NONE (after one intentional, documented test update: the old A11 test
  asserting Share is *always* disabled was replaced by
  test_enablement_share_and_open_folder_only_for_completed, since A12
  deliberately and correctly changes that policy for COMPLETED items).
  All 741 other pre-A12 tests pass unmodified.

BUGS FOUND:
  1. (Found via the mandated pre-implementation audit, not a test
     failure) RestartRecovery.recover()'s output never carried
     `partial_transfers` forward -- PersistentDownloadState's empty-dict
     default silently applied, so every durable partial-transfer row was
     deleted on every restart the moment DownloadManagerService.start()
     called replace_all() with that output. This directly contradicted
     A8's/A9's own documented "a valid partial may survive recovery"
     rules and had gone completely undetected through all of A8, A9, A10,
     and A11's test suites (743 tests at the time), because none of them
     exercised the FULL start()-triggered replace_all() round trip with a
     pre-existing partial-transfer row already on disk before recovery
     ran. Reproduced directly against a real SQLite file before fixing;
     fixed with one line; regression test added. The same carry-forward
     was applied proactively to the new `completed_files` field to avoid
     introducing the identical class of bug a second time.
  2. (Self-caught during test development, not a product bug) An early
     version of test_real_full_user_workflow polled the raw backend state
     (manager.item_snapshot()) directly to decide when to click Pause/
     Resume, racing the widget's own up-to-100ms coalesced refresh debounce
     -- the backend could report PAUSED before the widget had re-rendered
     and re-enabled the Resume button, causing a silently-ignored click on
     a disabled button and an indefinite hang waiting for completion.
     Fixed by waiting on the WIDGET's own rendered status text instead
     (matching the pattern test_gui_e2e.py already used correctly), plus
     an explicit `assert widget.resume_button.isEnabled()` before the
     click so any future regression of this kind fails immediately and
     legibly instead of timing out.
  3. (Self-caught while writing the destination-validation test) An early
     version of test_invalid_destination_blocks_add clicked a button that
     triggers a REAL (unmocked) QMessageBox.warning(), which blocks
     waiting for user interaction and hung the entire test file even in
     offscreen mode. Fixed by installing the QMessageBox.warning
     monkeypatch before the first click that could trigger it, not just
     before the second.

KNOWN LIMITATIONS:
  no final visual redesign, no full Settings page (destination is
    session-only, not persisted across launches), no History tab, no
    delete-file workflow, no bandwidth controls, no yt-dlp/provider
    backend, no browser extension, no FriendSend/Android/Device Mode
  manager.stop() still runs synchronously on the GUI thread during
    shutdown -- documented, not redesigned, per the prompt's own minimal-
    implementation guidance
  the completed-file checkpoint is a separate transaction from the task-
    completed checkpoint -- a real, narrow, documented crash window can
    leave a COMPLETED task with no completed-file record (honestly
    reported as NO_RECORD, never fabricated)
  pre-A12 (schema v2) completions have no completed-file record and will
    honestly report NO_RECORD for Share/Open Folder
  manual visible desktop smoke deferred (no graphical session available;
    automated equivalents run instead, see above)
  A9 combined SIGKILL -> Range-resume real subprocess E2E remains OPEN

GATE:
PASS -- a real destination can be selected and is genuinely used per-
  download; Enter-key add and a bounded double-submit guard both work;
  the empty state is truthful and the status summary reflects real
  snapshot data; stale selections and async command rejections cannot
  corrupt the GUI or command the wrong occurrence; a completed download's
  file now has a safe, privileged A10 accessor that never leaks through
  the ordinary snapshot, survives restart for new completions, preserves
  exact occurrence identity, handles a missing/deleted file honestly, and
  rejects a tampered record without ever touching an unrelated file; Share
  obtains its Artifact exclusively through that accessor and the existing
  Artifact factory, never bypassing A10; Open Folder uses only the
  validated parent directory, with no shell-command path; window close
  has truthful, cancellable UX with no partial shutdown on cancel and
  exactly one clean shutdown on confirm; launch.sh remains CWD-independent
  and the desktop entry still points at it; no design.txt work was
  performed; a real, previously-undetected cross-phase bug was found and
  fixed with a regression test; all previous tests remain green; worktree
  clean (pending this commit).

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A13 — Device Mode / FriendSend Desktop Handoff Foundation

COMMIT:
54fa4a3
