# Functional GUI Hardening / User Workflow Polish (Prompt A12)

This is workflow hardening, not final GUI design. `design.txt` was not
implemented — no final colors/icons/typography/animations/sidebar. The
download-manager GUI still talks only to `DownloadManagerService`. Share
remains a separate application/domain layer. The default, safe
`DownloadManagerSnapshot`/`DownloadViewSnapshot` still does not expose
local file paths — A12 adds only durable completed-file *identity*
(§29-45), reached exclusively through a new privileged accessor, never a
general download-history subsystem.

## Scope

```text
destination selection + session memory
input workflow (Enter key, normalization, double-submit guard)
empty state / status summary
command feedback (routine vs. serious) + stale-selection hardening
shutdown confirmation UX
completed-file durability + privileged accessor + Share/Open-Folder bridge
desktop launcher CWD-independence + static .desktop validation
```

## A real bug found and fixed before any GUI work

Before touching the GUI, `rychlik.core.restart_recovery.RestartRecovery.
recover()` was audited (§30) as part of investigating where completion
information currently lives and gets lost. That audit surfaced a
pre-existing, previously-untested bug unrelated to completed files:
**`recover()`'s output `PersistentDownloadState` never carried
`partial_transfers` forward** — it relied on the dataclass's empty-dict
default. Since `DownloadManagerService.start()` calls `state_store.
replace_all(recovery.state)`, this silently deleted **every** durable
partial-transfer row on **every** restart, directly contradicting A8's
"a valid PartialTransferState may survive recovery" rule and A9's "PAUSED
survives restart only with a locally-validated partial" rule. Reproduced
directly against a real SQLite file, fixed with one line
(`partial_transfers=dict(persisted.partial_transfers)`), and covered by a
new regression test
(`test_recovered_state_carries_partial_transfers_forward`). The same
carry-forward is applied to the new `completed_files` field for the same
reason.

## Destination workflow

`DownloadManagerWidget` gained a "Save to:" row (a read-only `QLineEdit`
display + "Browse…" button). The default comes from `QStandardPaths.
writableLocation(DownloadLocation)`, falling back to a per-process temp
directory if that location is unavailable or not an existing directory —
never a hard-coded real user path. The chooser itself is injected via a
constructor parameter (`destination_chooser`, defaulting to `QFileDialog.
getExistingDirectory`), so tests provide a temp directory without ever
opening a real interactive dialog (§73). Cancelling the picker (empty
string) leaves the existing selection untouched. An invalid/non-existent
chosen path is rejected with a bounded warning and never applied. The
selected destination is remembered for the current session (in-memory
only — no persistent preference file was added, per the "do not introduce
a full Settings subsystem" guidance) and used for every subsequent
`add_download()` call independently — changing it mid-session affects
only downloads added afterward.

## Input workflow

`QLineEdit.returnPressed` is connected to the same handler as the
"Download" button click, so pasting a URL and pressing Enter is
equivalent. The URL is only `.strip()`ped (§12) — no aggressive rewriting,
no auto-prefixing of a scheme. A bounded `_add_in_progress` guard (set at
the start of the handler, cleared in a `finally`) prevents a re-entrant
call to the SAME handler from issuing a second `add_download()` (§14) —
this is a small per-widget guard, not a global lock, and does not affect
any other command or backend event.

## Empty state / status summary

A `QStackedWidget` switches between the empty-state `QLabel` and the real
`QTableWidget` based purely on whether the current snapshot has any items
— never a synthetic table row standing in for "empty" (§16). A separate
summary label renders `"<n> download(s) · <k> active · <speed>"` from the
snapshot's own `active_transfer_count`/`aggregate_speed_bps` — no
aggregate ETA is invented (A7 defines none, §22), and the label is empty
when there is nothing to show.

## Command feedback / stale-selection hardening

Routine outcomes (`REJECTED`, `ACCEPTED`) now surface as a transient
status-bar message (auto-cleared after 5 seconds if unchanged) rather than
a modal — a serious backend exception (typically `PersistenceCommandError`
or `ManagerFaultedError`) still uses a bounded `QMessageBox.warning`/a
persistent fault indication, never a raw traceback (§61/§62).

Stale-selection safety was already structurally guaranteed by A11's
identity-by-`queue_entry_id` design (every command handler re-reads
`_selected_queue_entry_id()` fresh at click time, and every render
re-selects by id, never by index) — A12 adds explicit regression tests
proving it (`test_selection_cleared_when_item_disappears`, `test_command_
rejection_does_not_crash_or_corrupt_selection`) rather than changing the
underlying mechanism, since it was already correct.

## Shutdown UX

`DownloadManagerWidget.confirm_close()`: returns `True` immediately if the
service is not `RUNNING` or the current snapshot's `active_transfer_count`
is zero (no confirmation needed, §125); otherwise shows a `QMessageBox.
question` with wording matching A10's actual graceful-stop semantics
("...will wait for active transfers to finish", never "will be paused",
§67) and returns `True` only if the user chose "Close". `MainWindow.
closeEvent` calls this first; on `False` it calls `event.ignore()` and the
window/service are left completely untouched (§68). On `True`, `widget.
prepare_shutdown()` disables every mutating control and shows "Shutting
down…" (§69) before `widget.shutdown()` (detach the Qt bridge) and
`manager.stop()` run — `manager.stop()` still runs synchronously on the
GUI thread; a background-thread wrapper was considered and explicitly
**not** built, per the prompt's own "keep implementation minimal, do not
redesign A10 semantics" guidance (§71/§72) — a slow deliberate download
can therefore make the window visibly (but explicitly, via the "Shutting
down…" label) wait during close, which is documented current behavior,
not a defect.

## Completed-file durability

### Audit findings (§30)

- `CompletedDownload` (the acquisition-layer result) already carries the
  exact real `final_path`/`display_name`/`size` — but it only ever lived
  transiently inside `DispatchExecutionResult.completed_download`, itself
  only reachable via `WorkerCompletion.result` from `ConcurrentDownloadRuntime.
  drain_completions()`. A10's own event pump already drains completions to
  translate them into `ManagerEvent`s and **discarded** `completed_download`
  in the process — the exact real data existed for one call and was then
  gone forever.
- A8 (schema v2) and A9 (schema v2, unchanged) have no completion-specific
  table at all — `download_tasks.state = 'COMPLETED'` is the only durable
  trace, with no path.
- `Artifact`/`ArtifactRepository` already provide everything needed to
  turn a real file into a shareable object (hash, MIME, size) — reused
  unchanged, never duplicated.

### New durable model (schema v3)

```text
completed_files(
    queue_entry_id PRIMARY KEY,   -- identity, never task_id alone
    task_id,
    local_path, display_name, size_bytes,
    completed_at_utc
)
```

Added via the exact same real-transaction migration pattern A9 used for
`partial_transfers` (`CREATE TABLE IF NOT EXISTS` inside the same
transaction that checks/bumps `schema_version`) — verified against a
hand-built authentic v2 database. For historical (pre-v3) `COMPLETED`
tasks there is deliberately **no** synthesized row — guessing
`destination_dir + display_name` could be wrong (Content-Disposition or a
filename collision may have changed the real name), so those occurrences
simply have no completed-file record and `completed_file()` reports
`NO_RECORD` truthfully rather than fabricating a path (§41).

### Capture point (a documented, honest crash window)

`ConcurrentDownloadRuntime._run_worker()` captures the real
`completed_download` the instant `dispatch()` returns `COMPLETED` and
writes a `CompletedFileRecord` in its own, separate `checkpoint_task_state()`
transaction — deliberately **not** the same transaction as the `Task
COMPLETED`/`QueueEntry REMOVED` checkpoint dispatch() already performed
moments earlier inside the coordinator. This means a process could crash
between those two commits, leaving a durably-`COMPLETED` task with no
`completed_files` row — `completed_file()` then honestly reports
`NO_RECORD` rather than claiming availability (§44/§113). This is
documented, not hidden: SQLite and the filesystem were never one atomic
transaction in this project, and this is one more instance of that same
already-accepted limitation. The real, successfully-downloaded file is
**never** deleted just because this secondary checkpoint fails (§114) —
verified directly (`test_completion_checkpoint_failure_does_not_claim_
availability_or_delete_file`).

### Privileged accessor

```text
manager.completed_file(queue_entry_id) -> CompletedFileResult(
    status: CompletedFileStatus,
    info: CompletedFileInfo | None,
    reason: str | None,
)
```

`CompletedFileStatus`: `AVAILABLE`, `UNKNOWN_OCCURRENCE`, `NOT_COMPLETED`,
`NO_RECORD`, `FILE_MISSING`, `INVALID_RECORD`. Validation chain, all
inside the accessor, never in the GUI:

```text
occurrence must exist (via queue history, which A1 already retains)
-> task must be exactly COMPLETED
-> a durable completed_files row must exist for this EXACT queue_entry_id
-> its task_id must match (never confuses an old occurrence with a
   newer one of the same task, §32/§110)
-> its request must still be registered, and the recorded path must
   resolve to a plain, non-symlink file INSIDE that request's
   destination_dir (the same ownership philosophy as A9's partial-
   transfer validation, §37) -- a tampered/corrupted row pointing
   anywhere else is rejected as INVALID_RECORD, and the accessor never
   modifies the file it finds there or anywhere else (read-only, proven
   by a dedicated tamper test)
-> the file must still actually exist on disk -- otherwise FILE_MISSING,
   and the task is NOT reconciled back to any other state (§45)
```

`CompletedFileInfo` (only returned on `AVAILABLE`) is a small immutable
dataclass — never a persistence/domain object. The ordinary
`DownloadViewSnapshot` was re-verified (regression test) to still carry no
`local_path` field and no path/secret in its `repr()` after this phase's
additions (§33/§108).

## Share bridge / Open Folder

```text
DownloadManagerService.completed_file(queue_entry_id)
        |
        v
rychlik.gui.completed_artifact_bridge.build_artifact_for_completed()
        |  (Artifact.from_completed_download(), reused unchanged)
        v
existing ShareDialog
```

`completed_artifact_bridge.py` is the only new file bridging the two
domains — it never touches `ShareLinkService`/`DeviceShareService`
directly, and never digs into `DownloadRequest`, searches the destination
folder, or reaches into any A4/A5 worker/result cache (§50). The GUI's
Share/Open-Folder button eligibility is COMPLETED-only (cheap, no
filesystem `stat()` per table refresh, §59/§60); actual file availability
is validated lazily, on click, via the privileged accessor — an
unavailable file produces a bounded status message, never a crash or a
false success. Open Folder calls an injectable `folder_opener` (default
`QDesktopServices.openUrl(QUrl.fromLocalFile(...))`) with only the
validated file's **parent directory** — never arbitrary text from a table
cell, and never a shell command assembled from a path (§56/§57).

## Desktop launcher

`launch.sh` was re-verified to be CWD-independent (invoked with `cwd` set
to an unrelated temporary directory; the process starts cleanly with no
import/path error). `rychlik.desktop`'s `Exec=` line was verified to still
point at the real, executable `launch.sh` — no trust-flag automation was
added or attempted (desktop-environment trust remains user-managed,
unchanged from prior phases).

## Known limitations

```text
no final visual redesign -- standard PySide6 widgets, no sidebar, no
  custom icons/animations
no full Settings page -- destination is remembered only for the current
  session, not persisted across launches
no History tab, no delete-file workflow, no bandwidth controls, no
  yt-dlp/provider backend, no browser extension, no FriendSend/Android
  integration, no Device Mode completion
manager.stop() still runs synchronously on the GUI thread during
  shutdown -- a slow active download visibly (but explicitly, via
  "Shutting down...") delays window close; a background-thread wrapper
  was explicitly not built (minimal-implementation guidance)
the completed-file checkpoint is a separate transaction from the task-
  completed checkpoint -- a real (narrow, documented) crash window exists
  between them where a COMPLETED task has no completed-file record yet
pre-v3 (pre-A12) completions have no completed-file record and will
  honestly report NO_RECORD for Share/Open Folder -- never a fabricated
  path
the A9 combined SIGKILL -> real Range-resume subprocess E2E remains OPEN
  (docs/OPEN_VALIDATION_DEBT.md, A9-CRASH-RANGE-E2E) -- not attempted in
  this phase either, per the prompt's own "do not expand A12 merely to
  force it" guidance
```
