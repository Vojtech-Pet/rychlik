# Functional Download Manager GUI Integration (Prompt A11)

This is functional GUI integration, not final GUI design. No `design.txt`
final redesign was performed. No final colors, brand palette, icon pack,
animations, pixel-perfect spacing, or typography were frozen. No
sidebar/navigation redesign, no full settings UI. The GUI depends
exclusively on `DownloadManagerService` (A10) — never on A1–A9 internals.

## A10-only backend boundary

`rychlik/gui/download_manager_widget.py` and
`rychlik/gui/manager_qt_bridge.py` import only `DownloadManagerService`
and its public result/event/config types, plus stdlib/PySide6. Neither
module imports `DownloadQueue`, `DownloadTask`, `SchedulerPolicy`,
`DispatchCoordinator`, `ConcurrentDownloadRuntime`, `RetryPolicy`,
`ProgressRegistry`, `SqliteDownloadStateStore`, `RestartRecovery`,
`TransferControl`, or `sqlite3`. Every render reads exactly
`manager.snapshot()`/`manager.item_snapshot()`; every command calls
exactly one `DownloadManagerService` method.

## Architecture diagram

```text
A10 event pump / synchronous command events
              |
              v
   ManagerQtBridge.manager_event  (Signal(object))
              | Qt auto-queued cross-thread delivery
              v
   DownloadManagerWidget._on_manager_event
              | coalesced (QTimer.singleShot, 100ms debounce)
              v
   DownloadManagerWidget._refresh_now()
              |
              +-- manager.snapshot() ---> render table
              |
   button click ---> manager.<command>(queue_entry_id) ---> refresh
```

No backend internals appear above the `DownloadManagerService` line.

## Main window structure

```text
MainWindow (QMainWindow)
└── DownloadManagerWidget (QWidget)
    ├── URL row: QLineEdit + "Download" QPushButton
    ├── QTableWidget (the download list)
    ├── Control row: Hold / Release / Pause / Resume / Retry now / Cancel /
    │                Up / Down / priority QComboBox / Share...
    └── status QLabel (recovery notice, command-rejection reasons)
```

The legacy single-download `DownloadWidget`/`_DownloadWorker`
(`rychlik/gui/download_widget.py`) is left in place, untouched, with its
own existing test file — it is simply no longer `main.py`'s central
widget. It called `AcquisitionService` directly and held its own
single-`Artifact` field as the sole source of truth, which is
structurally incompatible with a multi-download manager (§114) — rather
than adapt it in place, `DownloadManagerWidget` replaces it as the
production entry point.

## Service construction

`main.py` constructs exactly one `DownloadManagerService` instance,
calls `start()` before building any widget, and passes the already-
running service into `DownloadManagerWidget`/`MainWindow`. No widget
constructs its own service, state store, or queue.

```python
manager = DownloadManagerService()
manager.start()             # may raise -> bounded QMessageBox.critical, exit(1)
widget = DownloadManagerWidget(manager)
window = MainWindow(manager, widget)
window.show()
```

If `start()` raises (a startup/recovery failure), the application shows a
bounded `QMessageBox.critical` and exits with a non-zero status rather
than silently launching an empty manager (§11).

## Qt event bridge

`ManagerQtBridge(QObject)` is the ONLY place a background-thread A10
callback is touched. `attach()` calls `manager.subscribe(self._on_
backend_event)`; that callback runs on whatever thread produced the
event (the event-pump thread, or a command's calling thread) and does
nothing except `self.manager_event.emit(event)`. Because the `QObject`
lives on the GUI thread (constructed there) and emission happens from a
different thread, Qt auto-detects this and delivers the connected slot
via a queued connection on the GUI thread's own event loop — no manual
`QMetaObject.invokeMethod` plumbing was needed, and this is verified by
a dedicated test that emits from a real Python `threading.Thread` and
asserts the receiving slot observes `QThread.currentThread()` equal to
the GUI thread's.

`detach()` unsubscribes from the service; `DownloadManagerWidget.
shutdown()` calls it, and `MainWindow.closeEvent` calls `widget.
shutdown()` before `manager.stop()` (§74/§75) — no callback can reach a
widget that is being torn down.

## GUI thread safety

No `Qt widget is ever mutated from `_on_backend_event`` (the bridge's own
callback) — it only re-emits a signal. All actual widget mutation
(`_refresh_now`/`_render_snapshot`) runs exclusively inside slots
connected to `manager_event`, which only ever fire on the GUI thread.

## Snapshot refresh model / coalescing

`_on_manager_event` sets a `_refresh_pending` flag and schedules exactly
one `QTimer.singleShot(100ms, self._refresh_now)` — a burst of events
(e.g. several progress ticks) collapses into a single refresh. Command
handlers (`_on_download_clicked`, `_on_hold_clicked`, ...) additionally
call `_refresh_now()` synchronously right after their command returns, so
the row reflects the command's outcome without waiting for the 100ms
debounce or a round-trip through the event pump.

## Stable row identity

Every row's column-0 `QTableWidgetItem` carries `queue_entry_id` (and
`task_id`) via `Qt.ItemDataRole.UserRole`/`UserRole+1` — never row index,
filename, or URL. `_render_snapshot` remembers the currently-selected
`queue_entry_id` before rebuilding the table and re-selects the matching
row afterward regardless of index changes (priority change, manual
reorder). If the previously-selected occurrence is no longer present
(e.g. it completed and was removed per A7's existing snapshot policy),
selection is simply cleared and every command button is disabled.

## Status derivation

`rychlik/gui/formatters.py::derive_status_text(task_state, queue_state)`
maps the backend's two independent state enums to one UI-only label
(`Waiting`, `On hold`, `Downloading`, `Paused`, `Waiting to retry`,
`Verifying`, `Finishing`, `Resolving`, `Completed`, `Failed`,
`Cancelled`). This label is never written back to the backend and never
re-parsed for a decision — every button-enablement check and every
command reads `task_state`/`queue_state` directly from the snapshot item.

## Progress / speed / ETA / downloaded-total formatting

All four are pure functions in `formatters.py`, independently unit-tested:

```text
format_progress(fraction)          -- "53 %" or "—" if fraction is None
format_speed(speed_bps)            -- "4.2 MB/s" or "—" if None
format_eta(eta_seconds)            -- "8 s" / "1m 24s" / "2h 05m" / "—"
format_downloaded_total(b, total)  -- "52.0 MB / 100.0 MB", "... / —" if total unknown
```

Units are decimal (1000-based: B/KB/MB/GB/TB), a documented UI choice —
A7's raw byte/bytes-per-second values are never altered, only displayed.
An unknown value is always rendered as an em dash, never a fabricated
`0 %`/`0 B/s`.

## Priority display / control

A `QComboBox` with exactly `High`/`Normal`/`Low` (`format_priority`,
mapped 1:1 to `QueuePriority`). Changing it calls
`manager.set_priority(entry_id, priority)` only; the table is never
reordered optimistically — the next `snapshot()` (triggered by the
command handler's own `_refresh_now()` call) is what actually moves the
row, per A1's canonical ordering.

## Manual reorder (Up / Down)

`Up`/`Down` compute the selected item's immediate same-priority-band
neighbor from the current `snapshot()` and call `move_before()`/
`move_after()` with that neighbor's `queue_entry_id` — never a raw index
swap on the table, and never an attempt to move across a priority
band (A1 already rejects that; at a band edge, Up/Down is a documented
no-op rather than an invalid call).

## Commands and button mapping

| Button / control | A10 call | Notes |
|---|---|---|
| Download | `add_download(request)` | bounded, returns before the network transfer starts |
| Hold | `hold(entry_id)` | queue-level only, never touches an active transfer |
| Release | `release_hold(entry_id)` | |
| Pause | `pause_transfer(entry_id)` | async (ACCEPTED before physical PAUSED) |
| Resume | `resume_transfer(entry_id)` | async; still subject to A1/A3/A5 capacity/priority/queue-hold |
| Retry now | `retry_now(entry_id)` | rejected outside RETRY_WAIT; cannot exceed the attempt budget (A6's own guarantee) |
| Cancel | `cancel(entry_id)` | synchronous for a waiting task, async (ACCEPTED) for an active transfer |
| Priority combo | `set_priority(entry_id, priority)` | |
| Up / Down | `move_before`/`move_after(entry_id, neighbor_id)` | same-band only |
| Share... | *(disabled)* | see below |

Every handler reads the currently selected `queue_entry_id`, calls
exactly one A10 method, and never mutates a row's displayed state
directly for an asynchronous outcome — the row only ever reflects the
next real `snapshot()`.

## Button enablement

Computed purely from the selected item's `DownloadViewSnapshot` (never a
hard-coded/stale flag):

```text
Hold      -- queue_state == QUEUED
Release   -- queue_state == PAUSED
Pause     -- task_state == TRANSFERRING
Resume    -- task_state == PAUSED
Retry now -- task_state == RETRY_WAIT
Cancel    -- task_state not in (COMPLETED, FAILED, CANCELLED)
Up/Down   -- always enabled when something is selected (a no-op at a band edge)
Share     -- never enabled in A11 (see below)
```

If the service is not `RUNNING` (`STOPPING`/`STOPPED`/`FAULTED`), every
command button and the Download button are disabled outright.

## Share integration status

The `Share...` button is present (legacy `ShareDialog`/`Artifact` wiring
is untouched) but **never enabled** in A11. `DownloadManagerService`'s
`DownloadViewSnapshot` deliberately does not expose a completed file's
local path (A7's own security rule — no path/credential leakage through
the normal snapshot), so there is currently no legitimate A10 accessor
for "the Artifact behind this completed occurrence." Per the prompt's own
guidance (§61/§116), this phase does not bypass the A10 boundary to
manufacture one ad hoc (e.g. by having the GUI inspect worker-completion
internals or reopen files by guessed name). A future phase can add a
narrow, explicit `DownloadManagerService` accessor for this
(`completed_artifact(queue_entry_id)` or similar) once the actual GUI
need is proven — see Known limitations.

## Startup / recovery UX

`start()`'s `RecoveryReport` is read once at widget construction
(`manager.last_recovery_report`); if it has any recovery actions, a
one-line status message ("Recovered N interrupted download(s) from a
previous session.") is shown — no dialog, no dump of raw recovery-action
internals or SQLite rows.

## Shutdown flow

`MainWindow.closeEvent`: `widget.shutdown()` (detach the Qt bridge) →
`manager.stop()` (A10's own graceful A5 shutdown + clean-shutdown marking
+ state-store close) → accept the close event. The GUI never marks clean
shutdown itself and never manipulates A8 metadata. A5's `stop()` is
graceful and may visibly wait for an active download to finish (A9's
existing semantics, unchanged) — this phase does not introduce a
force-kill path; a slow deliberate download can make window close take
visibly longer, which is documented current behavior, not a bug.

## Error / fault UX

Every command handler wraps its `DownloadManagerService` call: a
`REJECTED` `ManagerCommandResult` sets a bounded status-label message
("Could not hold: occurrence is REMOVED"); a raised exception (typically
`PersistenceCommandError`) shows a bounded `QMessageBox.warning` with the
exception's own domain-safe message — never a raw traceback, SQLite
error, or `requests` internal. A `ManagerFaultedError` additionally
disables every mutating control and shows a persistent "restart the
application" status message; the service is never silently reset.

## Known limitations

```text
Share is fully disabled in A11 -- no legitimate A10 completed-artifact
  accessor exists yet; adding one is deferred to a future phase, not
  bypassed here

no TRANSFER_STARTED-driven visual highlight -- a READY->TRANSFERRING
  transition is only visible via the next coalesced refresh

no destination-directory picker -- a per-process temporary directory is
  used by default (matching the prior DownloadWidget skeleton's own
  behavior); a minimal chooser could be added later without touching the
  A10 boundary

no batch-add UI, no drag-and-drop reorder, no download-history view, no
  bandwidth controls, no yt-dlp UI, no browser-extension integration, no
  FriendSend/Android integration -- all explicitly out of scope

no final visual design -- standard PySide6 widgets, default styling, no
  sidebar/navigation, no icons beyond what already existed

closing the window during a slow active download waits for A5's existing
  graceful shutdown (documented, not a defect)

the legacy single-download DownloadWidget/_DownloadWorker remain in the
  codebase, untouched and still individually tested, but are no longer
  reachable from main.py

A9's combined SIGKILL -> Range-resume real subprocess E2E remains OPEN
  (docs/OPEN_VALIDATION_DEBT.md, A9-CRASH-RANGE-E2E) -- untouched by this
  phase
```
