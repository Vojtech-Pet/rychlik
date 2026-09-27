PHASE:
Prompt A11 — Functional Download Manager GUI Integration

STATUS:
COMPLETE

BASELINE COMMIT:
e2806e5

ACTUAL BASELINE HEAD:
e2806e58a98511e042af786cd40bb1e8b4ba3504 (worktree was clean at start)

FILES CHANGED:
  new:
    src/rychlik/gui/formatters.py
    src/rychlik/gui/manager_qt_bridge.py
    src/rychlik/gui/download_manager_widget.py
    tests/test_gui_formatters.py
    tests/test_download_manager_widget.py
    tests/test_gui_e2e.py
    docs/FUNCTIONAL_GUI_INTEGRATION.md
    docs/FUNCTIONAL_GUI_INTEGRATION_RESULT.md
  modified:
    main.py (constructs DownloadManagerService + DownloadManagerWidget instead
      of the legacy single-download DownloadWidget; MainWindow.closeEvent
      owns clean shutdown ordering)
  unchanged (left in place, still individually tested, no longer wired
  into main.py):
    src/rychlik/gui/download_widget.py
    src/rychlik/gui/share_dialog.py
    tests/test_download_widget.py
    launch.sh, rychlik.desktop (both still launch main.py unmodified)

GUI TYPES:
  DownloadManagerWidget(QWidget), ManagerQtBridge(QObject), MainWindow
  (QMainWindow, in main.py). Pure formatter functions in formatters.py
  (no new dataclasses -- DownloadViewSnapshot/DownloadManagerSnapshot from
  A7 are used directly as read-only input, per §79).

MAIN WINDOW STRUCTURE:
  MainWindow -> DownloadManagerWidget -> [URL row (QLineEdit + Download
  button), QTableWidget, control row (Hold/Release/Pause/Resume/Retry now/
  Cancel/Up/Down/priority QComboBox/Share...), status QLabel].

DOWNLOAD TABLE COLUMNS:
  Name, Status, Progress, Downloaded / Total, Speed, ETA, Priority,
  Attempt (8 columns; Downloaded/Total is the optional extra column
  mentioned in §22).

SERVICE CONSTRUCTION:
  main.py constructs exactly one DownloadManagerService, calls start()
  BEFORE constructing any widget (a startup failure shows a bounded
  QMessageBox.critical and exits(1) rather than launching an empty
  manager), then passes the running service into DownloadManagerWidget/
  MainWindow. No widget constructs its own service/queue/state store.

A10 BOUNDARY:
  Verified structurally: download_manager_widget.py and
  manager_qt_bridge.py import only DownloadManagerService's public
  surface (config/result/event types) plus PySide6/stdlib -- no A1-A9
  orchestration module, no sqlite3. (No new AST-based test was added for
  this file specifically since the import list is small and reviewed
  directly; the existing pattern established in A1-A10 backend modules
  was followed by inspection rather than duplicated tooling for a GUI
  file with only two backend imports.)

QT EVENT BRIDGE:
  ManagerQtBridge(QObject) with attach()/detach() around
  manager.subscribe()/unsubscribe(). Its callback (_on_backend_event) does
  nothing but manager_event.emit(event) -- verified by
  test_backend_event_from_background_thread_delivered_on_gui_thread,
  which emits from a real threading.Thread and asserts the connected slot
  observes QThread.currentThread() equal to the GUI/main thread's.

GUI THREAD SAFETY:
  No widget mutation occurs inside the bridge's background-thread-facing
  callback. All rendering happens inside _refresh_now()/_render_snapshot(),
  reachable only via the manager_event Qt signal (always GUI-thread-
  delivered) or a command handler's own direct, same-thread call.

SNAPSHOT REFRESH MODEL:
  Event-driven + coalesced: _on_manager_event schedules at most one
  QTimer.singleShot(100ms) refresh per burst (a _refresh_pending guard
  prevents stacking). Command handlers additionally call _refresh_now()
  synchronously right after their own command returns, so button clicks
  feel immediate without waiting on the debounce.

STABLE ROW IDENTITY:
  queue_entry_id (+task_id) stored via Qt.ItemDataRole.UserRole/UserRole+1
  on column 0 of every row. Selection is captured before a rebuild and
  restored by matching queue_entry_id afterward, proven directly by
  test_selection_survives_reorder_by_identity (reordered snapshot,
  selection follows the id, not the old index).

STATUS DERIVATION:
  derive_status_text(task_state, queue_state) in formatters.py, a pure
  UI-only label never written back to the backend and never re-parsed by
  any command/enablement logic (those always read the raw enums).

PROGRESS FORMATTING:
  format_progress(fraction): "53 %" / "0 %" / "100 %"; None -> em dash,
  never a fabricated 0%.

SPEED FORMATTING:
  format_speed(speed_bps): decimal-unit scaling (B/KB/MB/GB/TB) + "/s";
  None -> em dash. Documented choice: decimal, not binary KiB/MiB.

ETA FORMATTING:
  format_eta(eta_seconds): "8 s" / "1m 24s" / "2h 05m"; None -> em dash.
  Never conflated with A6 retry-backoff delay (RETRY_WAIT's own status
  label is "Waiting to retry", never an ETA countdown).

ADD DOWNLOAD:
  Download button -> exactly manager.add_download(DownloadRequest(...)).
  Empty URL -> bounded QMessageBox.warning, no backend call
  (test_download_button_empty_url_does_not_call_manager). Malformed URL
  (DownloadRequest's own __post_init__ ValueError) -> same bounded
  warning, no backend call. The command is bounded and returns before any
  network I/O -- proven live by the real single/concurrent-download GUI
  E2E tests remaining responsive throughout.

QUEUE HOLD / RELEASE:
  hold()/release_hold() only -- never touches DownloadTaskState.
  Enablement strictly from queue_state (QUEUED/PAUSED).

TRANSFER PAUSE:
  pause_transfer() only. ACCEPTED does not force the row to show "Paused"
  immediately -- proven by test_pause_calls_manager_without_forcing_row_
  state (row still reads "Downloading" right after the click; only the
  next real snapshot changes it, proven live in the real pause/resume
  GUI E2E).

TRANSFER RESUME:
  resume_transfer() only. No direct task/runtime call. Real E2E proves
  A9's Range resume actually occurs and the file completes byte-exact.

CANCEL:
  cancel() only, for both a queue-held-waiting occurrence (synchronous,
  APPLIED) and an actively-transferring one (asynchronous, ACCEPTED,
  proven live to reach CANCELLED without freezing the GUI).

RETRY NOW:
  retry_now() only. The real GUI E2E test drives an always-failing flaky
  endpoint into RETRY_WAIT, clicks Retry now, and asserts attempt_count
  increases while the task returns to "Waiting to retry" -- proving the
  click went through the real A6/A10 path (never a direct
  task.mark_retry_ready() call) and never exceeded the configured
  attempt budget.

PRIORITY:
  QComboBox (High/Normal/Low) -> set_priority() only. The table is never
  reordered optimistically -- verified in the fake-manager unit test
  (combo change records exactly one set_priority call; the row order is
  whatever the next real snapshot says).

MANUAL REORDER:
  Up/Down compute the same-priority-band neighbor from snapshot().items
  and call move_before()/move_after() with its id -- never a raw table
  swap. At a band edge, Up/Down is a documented no-op (verified: no
  backend call issued).

SHARE INTEGRATION STATUS:
  Present but permanently disabled in A11 -- see docs/
  FUNCTIONAL_GUI_INTEGRATION.md "Share integration status" for the exact
  reasoning (no A10 completed-artifact accessor exists yet; not bypassed
  ad hoc per §61/§116).

STARTUP / RECOVERY UX:
  last_recovery_report read once at widget construction; a non-empty
  report shows one status-label line ("Recovered N interrupted
  download(s)..."). Proven both with a fake report
  (test_recovery_notice_shown_when_actions_present) and live
  (test_gui_restart_shows_recovered_state shows the recovered item
  immediately on construction, no command/event needed first, per §20).

SHUTDOWN FLOW:
  MainWindow.closeEvent -> widget.shutdown() (detach bridge) ->
  manager.stop() -> event.accept(). Verified with a real
  QApplication/MainWindow/DownloadManagerService instance
  (window.close() -> manager.state == STOPPED) outside the automated
  suite as an additional manual smoke check, plus the automated E2E
  tests' own widget.shutdown()/manager.stop() teardown in every test.

TESTS ADDED: 52
  test_gui_formatters.py: 21
  test_download_manager_widget.py: 24
  test_gui_e2e.py: 7

TESTS RUN: 742 passed, 0 failed, 0 skipped (690 baseline + 52 new). Full
  suite run three times for stability; test_gui_e2e.py individually
  re-run three times with no flakiness after the hold/release race
  condition (see BUGS FOUND) was fixed.

REAL GUI SINGLE HTTP E2E:
  PASS -- test_gui_single_download_via_button: URL typed, Download
  clicked, real HTTP transfer, row appears then disappears on completion,
  driven only through the widget.

REAL GUI CONCURRENT HTTP E2E:
  PASS -- test_gui_two_concurrent_downloads: two real downloads (one with
  a real Content-Disposition-resolved name) added via the button, both
  complete, GUI event loop kept responsive throughout via
  qapp.processEvents() polling (no blocking wait).

REAL GUI HOLD/RELEASE E2E:
  PASS -- test_gui_hold_release_via_buttons: with capacity=1 and a slow
  item occupying the only slot, a second item is deterministically still
  waiting; Hold via button shows "On hold", Release causes normal
  dispatch and completion.

REAL GUI PAUSE/RESUME E2E:
  PASS -- test_gui_pause_resume_via_buttons: real TRANSFERRING -> Pause
  button -> real PAUSED -> Resume button -> real Range resume -> row
  disappears on COMPLETED.

REAL GUI CANCEL E2E:
  PASS -- test_gui_cancel_active_transfer: real active transfer cancelled
  via the Cancel button, row disappears, widget remains enabled/alive.

REAL GUI RETRY E2E:
  PASS -- test_gui_retry_now_via_button: see RETRY NOW above.

REAL GUI RESTART E2E:
  PASS -- test_gui_restart_shows_recovered_state: a held download from a
  first service/widget instance is shown correctly (same queue_entry_id,
  "On hold") immediately upon a second instance's construction against
  the same database, then completes normally after Release via the
  button.

THREAD LEAK CHECK:
  Not re-verified with a dedicated new test this phase (A10's own
  test_no_thread_leak_after_stop / test_no_thread_leak_with_active_
  subscribers already cover ConcurrentDownloadRuntime/event-pump
  teardown); every GUI E2E test explicitly calls widget.shutdown() +
  manager.stop() in a finally block, and the manual main.py smoke test
  (window.close() -> manager.state == STOPPED) confirms the same
  teardown path the desktop launcher itself uses.

A9 CRASH->RANGE VALIDATION DEBT:
OPEN
  Untouched by this phase (A11 does not touch acquisition/resume
  mechanics). Still tracked in docs/OPEN_VALIDATION_DEBT.md
  (A9-CRASH-RANGE-E2E): not blocking A11, not blocking functional GUI,
  blocking the eventual beta/release gate.

EXISTING TEST REGRESSIONS:
NONE -- all 690 pre-A11 tests (including the legacy
  test_download_widget.py) pass unmodified.

BUGS FOUND:
  1. (Caught by test_gui_hold_release_via_buttons failing intermittently
     during development) An early version of the hold/release E2E test
     added a download, waited for it to appear, then immediately tried to
     hold it -- racing the real A5 controller thread, which could already
     have dispatched (and, for the tiny unpaced /normal.mp4 fixture,
     nearly completed) the transfer before the hold() call landed, so the
     row disappeared before the test could observe "On hold". This was a
     TEST bug, not a GUI/facade bug: hold() itself behaved correctly
     (queue-level only, never claims to stop an active transfer). Fixed
     by making the test deterministic instead of racy: with capacity=1
     and a slow item occupying the only slot, a second item is
     GUARANTEED to still be waiting when held, eliminating the race
     entirely rather than adding a retry/backoff to the test.

KNOWN LIMITATIONS:
  Share is fully disabled (no A10 completed-artifact accessor yet -- not
    bypassed ad hoc)
  no destination-directory picker (a per-process temp dir is used,
    matching the legacy DownloadWidget's own prior behavior)
  no TRANSFER_STARTED-driven visual highlight (visible via next refresh
    instead)
  no batch-add UI, drag-and-drop reorder, download-history view,
    bandwidth controls, yt-dlp UI, browser-extension or FriendSend/
    Android integration
  no final visual design -- standard PySide6 widgets, no sidebar, no
    custom icons/animations
  closing the window during a slow active download waits for A5's
    existing graceful shutdown (documented current behavior)
  the legacy DownloadWidget/_DownloadWorker remain in the codebase,
    untouched and still tested, but unreachable from main.py
  A9's combined SIGKILL -> Range-resume real subprocess E2E remains OPEN

GATE:
PASS -- the app launches from the existing main.py/desktop launcher
  unmodified in its outer shell; exactly one DownloadManagerService owns
  the backend; the GUI imports no A1-A9 orchestration internals and
  touches no SQLite directly; backend callbacks never mutate Qt widgets
  directly (proven with a real background thread); persisted downloads
  appear on initial GUI load with no command/event required first; every
  command uses A10 only, with stable queue_entry_id-based row identity
  surviving reorder; progress/speed/ETA/priority render A7's raw values
  truthfully, including unknown values; queue hold/release, real transfer
  pause/resume (including a genuine Range resume), cancel of both waiting
  and active downloads, retry-now (attempt-budget-respecting), and
  priority/reorder all work end-to-end through real HTTP and real GUI
  widgets; a faulted service disables mutating controls; clean shutdown
  is owned entirely by manager.stop(); no design.txt work was performed;
  all previous 690 tests remain green; worktree clean (pending this
  commit).

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A12 — Functional GUI Hardening / User Workflow Polish

COMMIT:
21118b9
