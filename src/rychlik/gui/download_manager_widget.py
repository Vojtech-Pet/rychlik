"""Functional download-manager GUI (Prompt A11).

Backed exclusively by DownloadManagerService (A10) -- this module never
imports DownloadQueue, DownloadTask, SchedulerPolicy, DispatchCoordinator,
ConcurrentDownloadRuntime, RetryPolicy, ProgressRegistry,
SqliteDownloadStateStore, RestartRecovery, or TransferControl, and never
touches sqlite3 directly. Every command/button handler calls exactly one
DownloadManagerService method and nothing else; every render reads
exactly manager.snapshot()/item_snapshot() and nothing else.

Not the final visual design (see docs/FUNCTIONAL_GUI_INTEGRATION.md) --
standard PySide6 widgets, a clean functional layout, no final colors/
icons/typography/animations.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
from rychlik.core.download_queue import QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui.formatters import (
    derive_status_text,
    format_downloaded_total,
    format_eta,
    format_priority,
    format_progress,
    format_speed,
)
from rychlik.gui.manager_qt_bridge import ManagerQtBridge

_QUEUE_ENTRY_ID_ROLE = Qt.ItemDataRole.UserRole
_TASK_ID_ROLE = Qt.ItemDataRole.UserRole + 1

_COLUMNS = ("Name", "Status", "Progress", "Downloaded / Total", "Speed", "ETA", "Priority", "Attempt")
_PRIORITY_ORDER = (QueuePriority.HIGH, QueuePriority.NORMAL, QueuePriority.LOW)
_PRIORITY_COMBO_LABELS = [format_priority(p) for p in _PRIORITY_ORDER]

_REFRESH_COALESCE_MS = 100


class DownloadManagerWidget(QWidget):
    def __init__(self, manager: DownloadManagerService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._items_by_id: dict[str, DownloadViewSnapshot] = {}
        self._refresh_pending = False
        self._updating_priority_combo = False

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

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/file.mp4")
        self.download_button = QPushButton("Download")
        url_row.addWidget(self.url_input)
        url_row.addWidget(self.download_button)
        layout.addLayout(url_row)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

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
        self.share_button = QPushButton("Share...")
        for widget in (
            self.hold_button, self.release_button, self.pause_button, self.resume_button,
            self.retry_button, self.cancel_button, self.up_button, self.down_button,
        ):
            controls.addWidget(widget)
        controls.addWidget(self.priority_combo)
        controls.addWidget(self.share_button)
        layout.addLayout(controls)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._set_command_buttons_enabled(False)

    def _connect_signals(self) -> None:
        self.download_button.clicked.connect(self._on_download_clicked)
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
        self.share_button.clicked.connect(self._on_share_clicked)

    def _show_recovery_notice(self) -> None:
        report = self._manager.last_recovery_report
        if report is not None and report.actions:
            self.status_label.setText(f"Recovered {len(report.actions)} interrupted download(s) from a previous session.")

    # --- shutdown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Call before/around manager.stop() (§74): detaches the Qt bridge
        so no further backend event can reach a widget that may be in the
        process of being destroyed."""
        self._bridge.detach()

    # --- backend event -> coalesced refresh (§18/§19) -------------------------

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

        self.download_button.setEnabled(True)
        if selected_id is not None and selected_id in self._items_by_id:
            self._select_row_for_id(selected_id)
        else:
            self._update_button_enablement(None)

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

    # --- stable identity / selection (§23-§25) ---------------------------------

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

    # --- button enablement (§53-§58) -------------------------------------------

    def _set_command_buttons_enabled(self, enabled: bool) -> None:
        for widget in (
            self.hold_button, self.release_button, self.pause_button, self.resume_button,
            self.retry_button, self.cancel_button, self.up_button, self.down_button,
            self.priority_combo, self.share_button,
        ):
            widget.setEnabled(enabled)

    def _update_button_enablement(self, item: DownloadViewSnapshot | None) -> None:
        if item is None or self._manager.state != ManagerState.RUNNING:
            self._set_command_buttons_enabled(False)
            return

        from rychlik.core.download_queue import QueueEntryState

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
        # Share is deliberately never enabled in A11 -- see
        # docs/FUNCTIONAL_GUI_INTEGRATION.md "Share integration status":
        # A10 does not yet expose a completed-artifact accessor, and this
        # phase must not bypass the facade boundary to build one ad hoc.
        self.share_button.setEnabled(False)

    # --- add download (§38-§41) -------------------------------------------------

    def _on_download_clicked(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Rýchlik", "Enter a URL first.")
            return
        try:
            request = DownloadRequest(url=url, destination_dir=self._destination_dir())
        except ValueError as exc:
            QMessageBox.warning(self, "Rýchlik", f"Invalid download request:\n{exc}")
            return

        try:
            self._manager.add_download(request)
        except Exception as exc:
            self._show_command_error("add the download", exc)
            return
        self.url_input.clear()
        self._refresh_now()

    def _destination_dir(self):
        import tempfile
        from pathlib import Path

        if not hasattr(self, "_default_destination_dir"):
            self._default_destination_dir = Path(tempfile.mkdtemp(prefix="rychlik-"))
        return self._default_destination_dir

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
        entry_id = self._selected_queue_entry_id()
        if entry_id is None:
            return
        try:
            result: ManagerCommandResult = method(entry_id)
        except Exception as exc:
            self._show_command_error(verb, exc)
            return
        if result.status == CommandStatus.REJECTED:
            self.status_label.setText(f"Could not {verb}: {result.reason or 'rejected'}")
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

    def _on_share_clicked(self) -> None:
        # Deliberately a no-op in A11 -- the button is never enabled (see
        # _update_button_enablement); kept only so a future phase can wire
        # a legitimate A10 completed-artifact accessor without restructuring.
        pass

    # --- error handling (§65) ---------------------------------------------------

    def _show_command_error(self, verb: str, exc: Exception) -> None:
        if isinstance(exc, ManagerFaultedError):
            self.status_label.setText("Download manager is unavailable (faulted). Restart the application.")
            self._apply_faulted_or_stopped_ui()
            return
        # Never expose raw tracebacks/SQLite/requests internals to the user;
        # the exception's own message is already a bounded domain string
        # for PersistenceCommandError/ManagerNotRunningError.
        QMessageBox.warning(self, "Rýchlik", f"Could not {verb} this download.\n{exc}")
