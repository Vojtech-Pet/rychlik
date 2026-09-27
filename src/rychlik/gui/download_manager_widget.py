"""Functional download-manager GUI (Prompt A11, hardened in Prompt A12).

Backed exclusively by DownloadManagerService (A10) -- this module never
imports DownloadQueue, DownloadTask, SchedulerPolicy, DispatchCoordinator,
ConcurrentDownloadRuntime, RetryPolicy, ProgressRegistry,
SqliteDownloadStateStore, RestartRecovery, or TransferControl, and never
touches sqlite3 directly. Every command/button handler calls exactly one
DownloadManagerService method and nothing else; every render reads
exactly manager.snapshot()/item_snapshot()/completed_file() and nothing
else. Share itself remains a separate domain: this module only ever
bridges through rychlik.gui.completed_artifact_bridge and the existing
ShareDialog, never touching ShareLinkService/DeviceShareService directly.

Not the final visual design (see docs/FUNCTIONAL_GUI_INTEGRATION.md,
docs/FUNCTIONAL_GUI_HARDENING.md) -- standard PySide6 widgets, a clean
functional layout, no final colors/icons/typography/animations.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import (
    CommandStatus,
    DownloadManagerService,
    ManagerCommandResult,
    ManagerFaultedError,
    ManagerState,
)
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed
from rychlik.gui.formatters import (
    derive_status_text,
    format_downloaded_total,
    format_eta,
    format_priority,
    format_progress,
    format_speed,
)
from rychlik.gui.manager_qt_bridge import ManagerQtBridge
from rychlik.gui.share_dialog import ShareDialog

_QUEUE_ENTRY_ID_ROLE = Qt.ItemDataRole.UserRole
_TASK_ID_ROLE = Qt.ItemDataRole.UserRole + 1

_COLUMNS = ("Name", "Status", "Progress", "Downloaded / Total", "Speed", "ETA", "Priority", "Attempt")
_PRIORITY_ORDER = (QueuePriority.HIGH, QueuePriority.NORMAL, QueuePriority.LOW)
_PRIORITY_COMBO_LABELS = [format_priority(p) for p in _PRIORITY_ORDER]

_REFRESH_COALESCE_MS = 100
_STATUS_MESSAGE_LIFETIME_MS = 5000


def default_destination_dir() -> Path:
    """§6: prefer the platform download location; fall back to a per-
    process temp dir rather than hard-coding a real user path."""
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if location:
        path = Path(location)
        if path.is_dir():
            return path
    import tempfile

    return Path(tempfile.mkdtemp(prefix="rychlik-"))


class DownloadManagerWidget(QWidget):
    def __init__(
        self,
        manager: DownloadManagerService,
        parent: QWidget | None = None,
        *,
        destination_chooser=None,
        folder_opener=None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._items_by_id: dict[str, DownloadViewSnapshot] = {}
        self._refresh_pending = False
        self._updating_priority_combo = False
        self._add_in_progress = False
        self._destination_dir = default_destination_dir()
        # §73: injectable so tests never open a real interactive dialog /
        # never invoke the real platform file manager in headless CI (§92).
        self._destination_chooser = destination_chooser or (
            lambda parent, start_dir: QFileDialog.getExistingDirectory(parent, "Choose destination", start_dir)
        )
        self._folder_opener = folder_opener or (
            lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        )

        self._bridge = ManagerQtBridge(manager, parent=self)
        self._bridge.manager_event.connect(self._on_manager_event)
        self._bridge.attach()

        self._build_ui()
        self._connect_signals()
        self._show_recovery_notice()
        self._refresh_now()

    # --- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Save to:"))
        self.destination_display = QLineEdit(str(self._destination_dir))
        self.destination_display.setReadOnly(True)
        self.browse_button = QPushButton("Browse…")
        dest_row.addWidget(self.destination_display)
        dest_row.addWidget(self.browse_button)
        layout.addLayout(dest_row)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/file.mp4")
        self.download_button = QPushButton("Download")
        url_row.addWidget(self.url_input)
        url_row.addWidget(self.download_button)
        layout.addLayout(url_row)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

        self.empty_state_label = QLabel("No downloads yet.\nPaste a URL above to start.")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self.empty_state_label)  # index 0
        self._table_stack.addWidget(self.table)  # index 1
        layout.addWidget(self._table_stack)

        controls = QHBoxLayout()
        self.hold_button = QPushButton("Hold")
        self.release_button = QPushButton("Release")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.retry_button = QPushButton("Retry now")
        self.cancel_button = QPushButton("Cancel")
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(_PRIORITY_COMBO_LABELS)
        self.open_folder_button = QPushButton("Open Folder")
        self.share_button = QPushButton("Share...")
        for widget in (
            self.hold_button, self.release_button, self.pause_button, self.resume_button,
            self.retry_button, self.cancel_button, self.up_button, self.down_button,
        ):
            controls.addWidget(widget)
        controls.addWidget(self.priority_combo)
        controls.addWidget(self.open_folder_button)
        controls.addWidget(self.share_button)
        layout.addLayout(controls)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._set_command_buttons_enabled(False)

    def _connect_signals(self) -> None:
        self.browse_button.clicked.connect(self._on_browse_clicked)
        self.download_button.clicked.connect(self._on_download_clicked)
        self.url_input.returnPressed.connect(self._on_download_clicked)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.hold_button.clicked.connect(self._on_hold_clicked)
        self.release_button.clicked.connect(self._on_release_clicked)
        self.pause_button.clicked.connect(self._on_pause_clicked)
        self.resume_button.clicked.connect(self._on_resume_clicked)
        self.retry_button.clicked.connect(self._on_retry_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.up_button.clicked.connect(self._on_up_clicked)
        self.down_button.clicked.connect(self._on_down_clicked)
        self.priority_combo.currentIndexChanged.connect(self._on_priority_combo_changed)
        self.open_folder_button.clicked.connect(self._on_open_folder_clicked)
        self.share_button.clicked.connect(self._on_share_clicked)

    def _show_recovery_notice(self) -> None:
        # §64: a clean launch with nothing to report must stay silent.
        report = self._manager.last_recovery_report
        if report is not None and report.actions:
            self.status_label.setText(f"Recovered {len(report.actions)} interrupted download(s) from a previous session.")

    # --- destination workflow (§5-9, §73, §120-121) ---------------------------

    def _on_browse_clicked(self) -> None:
        chosen = self._destination_chooser(self, str(self._destination_dir))
        if not chosen:
            return  # cancelled -- existing selection unchanged (§121)
        path = Path(chosen)
        if not path.is_dir():
            QMessageBox.warning(self, "Rýchlik", f"Not a valid directory:\n{path}")
            return
        self._destination_dir = path
        self.destination_display.setText(str(path))

    # --- shutdown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Call before/around manager.stop() (§74): detaches the Qt bridge
        so no further backend event can reach a widget that may be in the
        process of being destroyed."""
        self._bridge.detach()

    def confirm_close(self) -> bool:
        """§66-68: returns True if closing should proceed. A truthful
        confirmation is only shown when the CURRENT snapshot reports an
        active transfer -- wording matches actual A10 stop() semantics
        (graceful wait, never an automatic pause)."""
        if self._manager.state != ManagerState.RUNNING:
            return True
        snapshot = self._manager.snapshot()
        if snapshot.active_transfer_count == 0:
            return True
        reply = QMessageBox.question(
            self,
            "Rýchlik",
            "Downloads are still active.\nClosing Rýchlik will wait for active transfers to finish.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Close,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Close

    def prepare_shutdown(self) -> None:
        """§69: called only after confirm_close() returns True -- disables
        every mutating control and shows a bounded status before the
        (possibly slow, graceful) manager.stop() call is made."""
        self.download_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self._set_command_buttons_enabled(False)
        self.status_label.setText("Shutting down…")

    # --- backend event -> coalesced refresh (§18/§19, §126-128) ---------------

    def _on_manager_event(self, event) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(_REFRESH_COALESCE_MS, self._refresh_now)

    def _refresh_now(self) -> None:
        self._refresh_pending = False
        if self._manager.state != ManagerState.RUNNING:
            self._apply_faulted_or_stopped_ui()
            return
        snapshot = self._manager.snapshot()
        self._render_snapshot(snapshot)

    def _apply_faulted_or_stopped_ui(self) -> None:
        self._set_command_buttons_enabled(False)
        self.download_button.setEnabled(False)
        if self._manager.state == ManagerState.FAULTED:
            self.status_label.setText("Download manager is unavailable (faulted). Restart the application.")

    # --- rendering (§76: reads only, no backend commands) ---------------------

    def _render_snapshot(self, snapshot) -> None:
        selected_id = self._selected_queue_entry_id()

        self._items_by_id = {item.queue_entry_id: item for item in snapshot.items}
        self.table.setRowCount(len(snapshot.items))
        for row, item in enumerate(snapshot.items):
            self._render_row(row, item)

        self._table_stack.setCurrentIndex(1 if snapshot.items else 0)  # §16-17
        self._render_summary(snapshot)

        self.download_button.setEnabled(True)
        if selected_id is not None and selected_id in self._items_by_id:
            self._select_row_for_id(selected_id)
        else:
            # §28/§96: the previously-selected occurrence is no longer
            # displayed (completed/removed/terminal) -- clear selection
            # rather than letting some other row silently inherit control.
            self.table.clearSelection()
            self._update_button_enablement(None)

    def _render_summary(self, snapshot) -> None:
        if not snapshot.items:
            self.summary_label.setText("")
            return
        active = snapshot.active_transfer_count
        parts = [f"{len(snapshot.items)} download(s)"]
        if active:
            parts.append(f"{active} active")
        if snapshot.aggregate_speed_bps:
            parts.append(format_speed(snapshot.aggregate_speed_bps))
        self.summary_label.setText(" · ".join(parts))

    def _render_row(self, row: int, item: DownloadViewSnapshot) -> None:
        name_item = QTableWidgetItem(item.display_name or "(unknown)")
        name_item.setData(_QUEUE_ENTRY_ID_ROLE, item.queue_entry_id)
        name_item.setData(_TASK_ID_ROLE, item.task_id)
        cells = [
            name_item,
            QTableWidgetItem(derive_status_text(item.task_state, item.queue_state)),
            QTableWidgetItem(format_progress(item.progress_fraction)),
            QTableWidgetItem(format_downloaded_total(item.bytes_downloaded, item.total_bytes)),
            QTableWidgetItem(format_speed(item.speed_bps)),
            QTableWidgetItem(format_eta(item.eta_seconds)),
            QTableWidgetItem(format_priority(item.priority)),
            QTableWidgetItem(str(item.attempt_count)),
        ]
        for col, cell in enumerate(cells):
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, cell)

    # --- stable identity / selection (§23-§25, §95-96) --------------------------

    def _selected_queue_entry_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(_QUEUE_ENTRY_ID_ROLE) if item is not None else None

    def _select_row_for_id(self, queue_entry_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(_QUEUE_ENTRY_ID_ROLE) == queue_entry_id:
                self.table.selectRow(row)
                self._update_button_enablement(self._items_by_id.get(queue_entry_id))
                return
        self._update_button_enablement(None)

    def _on_selection_changed(self) -> None:
        selected_id = self._selected_queue_entry_id()
        self._update_button_enablement(self._items_by_id.get(selected_id) if selected_id else None)

    # --- button enablement (§53-§59) -------------------------------------------

    def _set_command_buttons_enabled(self, enabled: bool) -> None:
        for widget in (
            self.hold_button, self.release_button, self.pause_button, self.resume_button,
            self.retry_button, self.cancel_button, self.up_button, self.down_button,
            self.priority_combo, self.open_folder_button, self.share_button,
        ):
            widget.setEnabled(enabled)

    def _update_button_enablement(self, item: DownloadViewSnapshot | None) -> None:
        if item is None or self._manager.state != ManagerState.RUNNING:
            self._set_command_buttons_enabled(False)
            return

        self.hold_button.setEnabled(item.queue_state == QueueEntryState.QUEUED)
        self.release_button.setEnabled(item.queue_state == QueueEntryState.PAUSED)
        self.pause_button.setEnabled(item.task_state == DownloadTaskState.TRANSFERRING)
        self.resume_button.setEnabled(item.task_state == DownloadTaskState.PAUSED)
        self.retry_button.setEnabled(item.task_state == DownloadTaskState.RETRY_WAIT)
        self.cancel_button.setEnabled(
            item.task_state not in (DownloadTaskState.COMPLETED, DownloadTaskState.FAILED, DownloadTaskState.CANCELLED)
        )
        self.up_button.setEnabled(True)
        self.down_button.setEnabled(True)
        self.priority_combo.setEnabled(True)
        self._updating_priority_combo = True
        try:
            self.priority_combo.setCurrentIndex(_PRIORITY_ORDER.index(item.priority))
        finally:
            self._updating_priority_combo = False
        # §59: eligibility is COMPLETED-only here (cheap, no filesystem
        # access per refresh, §60) -- actual file availability is validated
        # lazily, on click, via the privileged completed_file() accessor.
        completed = item.task_state == DownloadTaskState.COMPLETED
        self.open_folder_button.setEnabled(completed)
        self.share_button.setEnabled(completed)

    # --- add download (§10-§15, §38-§41) -----------------------------------------

    def _on_download_clicked(self) -> None:
        if self._add_in_progress:
            return  # §14: bounded double-submit guard, not a global lock
        self._add_in_progress = True
        try:
            self._add_download_impl()
        finally:
            self._add_in_progress = False

    def _add_download_impl(self) -> None:
        url = self.url_input.text().strip()  # §12: trim only, no rewriting
        if not url:
            QMessageBox.warning(self, "Rýchlik", "Enter a URL first.")
            return
        if not self._destination_dir.is_dir():
            QMessageBox.warning(self, "Rýchlik", f"Destination is not a valid directory:\n{self._destination_dir}")
            return
        try:
            request = DownloadRequest(url=url, destination_dir=self._destination_dir)
        except ValueError as exc:
            QMessageBox.warning(self, "Rýchlik", f"Invalid download request:\n{exc}")
            return

        try:
            self._manager.add_download(request)
        except Exception as exc:
            self._show_command_error("add the download", exc)
            return
        self.url_input.clear()  # §13: only after a successful add
        self.url_input.setFocus()
        self._refresh_now()

    # --- commands (§42-§52) ------------------------------------------------------

    def _on_hold_clicked(self) -> None:
        self._run_command("hold", self._manager.hold)

    def _on_release_clicked(self) -> None:
        self._run_command("release the hold on", self._manager.release_hold)

    def _on_pause_clicked(self) -> None:
        self._run_command("pause", self._manager.pause_transfer)

    def _on_resume_clicked(self) -> None:
        self._run_command("resume", self._manager.resume_transfer)

    def _on_retry_clicked(self) -> None:
        self._run_command("retry", self._manager.retry_now)

    def _on_cancel_clicked(self) -> None:
        self._run_command("cancel", self._manager.cancel)

    def _run_command(self, verb: str, method) -> None:
        # §25/§93: always re-read the CURRENTLY selected id at click time --
        # never a cached row index -- so a stale/removed occurrence cannot
        # accidentally command whatever now occupies its old row.
        entry_id = self._selected_queue_entry_id()
        if entry_id is None:
            return
        try:
            result: ManagerCommandResult = method(entry_id)
        except Exception as exc:
            self._show_command_error(verb, exc)
            return
        if result.status == CommandStatus.REJECTED:
            self._show_status_message(f"Could not {verb}: {result.reason or 'rejected'}")
        elif result.status == CommandStatus.ACCEPTED:
            self._show_status_message(f"{verb.capitalize()} requested…")
        self._refresh_now()

    def _on_priority_combo_changed(self, index: int) -> None:
        if self._updating_priority_combo:
            return
        entry_id = self._selected_queue_entry_id()
        if entry_id is None or index < 0:
            return
        try:
            self._manager.set_priority(entry_id, _PRIORITY_ORDER[index])
        except Exception as exc:
            self._show_command_error("change priority", exc)
        self._refresh_now()

    def _on_up_clicked(self) -> None:
        self._move(direction=-1)

    def _on_down_clicked(self) -> None:
        self._move(direction=1)

    def _move(self, *, direction: int) -> None:
        entry_id = self._selected_queue_entry_id()
        if entry_id is None:
            return
        item = self._items_by_id.get(entry_id)
        if item is None:
            return
        snapshot = self._manager.snapshot()
        same_band = [i for i in snapshot.items if i.priority == item.priority]
        try:
            index = next(i for i, x in enumerate(same_band) if x.queue_entry_id == entry_id)
        except StopIteration:
            return
        neighbor_index = index + direction
        if not (0 <= neighbor_index < len(same_band)):
            return  # already at the edge of its band -- no-op, not an error
        target_id = same_band[neighbor_index].queue_entry_id
        try:
            if direction < 0:
                self._manager.move_before(entry_id, target_id)
            else:
                self._manager.move_after(entry_id, target_id)
        except Exception as exc:
            self._show_command_error("reorder", exc)
        self._refresh_now()

    # --- completed-file bridge: Open Folder / Share (§46-§60) --------------------

    def _on_open_folder_clicked(self) -> None:
        entry_id = self._selected_queue_entry_id()
        if entry_id is None:
            return
        result = self._manager.completed_file(entry_id)
        if result.info is None:
            self._show_status_message(f"Cannot open folder: {result.reason or result.status.name}")
            return
        self._folder_opener(result.info.local_path.parent)

    def _on_share_clicked(self) -> None:
        entry_id = self._selected_queue_entry_id()
        if entry_id is None:
            return
        artifact, result = build_artifact_for_completed(self._manager, entry_id)
        if artifact is None:
            self._show_status_message(f"Cannot share: {result.reason or result.status.name}")
            return
        dialog = ShareDialog(artifact, parent=self)
        dialog.exec()

    # --- error handling (§61-§62, §65) -------------------------------------------

    def _show_status_message(self, text: str) -> None:
        """Routine command feedback (§62): shown briefly, then cleared --
        never used for a persistent fault indication (that stays until the
        service state itself changes, via _apply_faulted_or_stopped_ui)."""
        self.status_label.setText(text)
        QTimer.singleShot(_STATUS_MESSAGE_LIFETIME_MS, self._clear_status_message_if_unchanged(text))

    def _clear_status_message_if_unchanged(self, text: str):
        def _clear() -> None:
            if self.status_label.text() == text:
                self.status_label.setText("")

        return _clear

    def _show_command_error(self, verb: str, exc: Exception) -> None:
        if isinstance(exc, ManagerFaultedError):
            self.status_label.setText("Download manager is unavailable (faulted). Restart the application.")
            self._apply_faulted_or_stopped_ui()
            return
        # Never expose raw tracebacks/SQLite/requests internals to the user;
        # the exception's own message is already a bounded domain string
        # for PersistenceCommandError/ManagerNotRunningError.
        QMessageBox.warning(self, "Rýchlik", f"Could not {verb} this download.\n{exc}")
