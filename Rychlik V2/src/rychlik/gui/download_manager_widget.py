"""Approved download-manager page (Final GUI/UX implementation).

Backed exclusively by DownloadManagerService -- this module never imports
DownloadQueue/DownloadTask/Scheduler/Runtime/SQLite. Every command handler
calls exactly one DownloadManagerService method per affected occurrence, and
every render reads only manager.snapshot(include_history=True) / completed_file().
No task state is ever fabricated locally: after a command the widget re-reads
the service snapshot.

Rows are identified by `queue_entry_id` only (never a row number), so sorting,
filtering and live reordering cannot redirect an action to another download.

Share itself remains a separate domain: this page only bridges through
rychlik.gui.completed_artifact_bridge into an injected share launcher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
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
from rychlik.gui import presentation as P
from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed
from rychlik.gui.download_model import ITEM_ROLE, DownloadFilterProxy, DownloadTableModel
from rychlik.gui.download_table import DownloadTableView
from rychlik.gui.formatters import format_priority, format_speed
from rychlik.gui.manager_qt_bridge import ManagerQtBridge
from rychlik.gui.share_dialog import ShareDialog
from rychlik.gui.theme import icons
from rychlik.gui.theme.tokens import metrics, palette

_REFRESH_COALESCE_MS = 100
_RETRY_TICK_MS = 1000
_STATUS_MESSAGE_LIFETIME_MS = 5000
_HISTORY_LIMIT = 500

_SERVICE_CALL = {
    P.RowAction.PAUSE: ("pause", "pause_transfer"),
    P.RowAction.RESUME: ("resume", "resume_transfer"),
    P.RowAction.HOLD: ("hold", "hold"),
    P.RowAction.RELEASE: ("release the hold on", "release_hold"),
    P.RowAction.RETRY_NOW: ("retry", "retry_now"),
    P.RowAction.CANCEL: ("cancel", "cancel"),
}
_PAST = {P.RowAction.PAUSE: "Pause", P.RowAction.RESUME: "Resume", P.RowAction.HOLD: "Hold", P.RowAction.RELEASE: "Release",
         P.RowAction.RETRY_NOW: "Retry", P.RowAction.CANCEL: "Cancel"}


def default_destination_dir() -> Path:
    """Prefer the platform download location; fall back to a per-process temp dir."""
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if location:
        path = Path(location)
        if path.is_dir():
            return path
    import tempfile

    return Path(tempfile.mkdtemp(prefix="rychlik-"))


class DownloadManagerWidget(QWidget):
    summary_changed = Signal(int, int, str)  # active transfers, waiting downloads, aggregate speed text
    counts_changed = Signal(dict)  # {"status": {name: n}, "category": {name: n}}
    status_message = Signal(str)
    add_requested = Signal()
    open_queues_requested = Signal()
    _resolve_finished = Signal(object, object, object)  # job, outcome, callback (queued onto the GUI thread)

    def __init__(
        self,
        manager: DownloadManagerService,
        parent: QWidget | None = None,
        *,
        destination_chooser=None,
        folder_opener=None,
        share_launcher: Callable | None = None,
        details_launcher: Callable | None = None,
        theme: str = "dark",
        resolve_service=None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._resolve_service = resolve_service
        self._resolve_jobs: set = set()
        self._theme = theme
        self._refresh_pending = False
        self._add_in_progress = False
        self._last_speed_text = ""
        self._shutting_down = False
        self._destination_dir = default_destination_dir()
        self._share_launcher = share_launcher
        self._details_launcher = details_launcher
        self._destination_chooser = destination_chooser or (
            lambda parent, start_dir: QFileDialog.getExistingDirectory(parent, "Choose destination", start_dir)
        )
        self._folder_opener = folder_opener or (lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        self._title = "All Downloads"

        self.model = DownloadTableModel(self)
        self.proxy = DownloadFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self._resolve_finished.connect(self._on_resolve_finished)
        self._bridge = ManagerQtBridge(manager, parent=self)
        self._bridge.manager_event.connect(self._on_manager_event)
        self._bridge.attach()

        self._build_ui()
        self._wire()
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(_RETRY_TICK_MS)
        self._retry_timer.timeout.connect(self._retry_tick)
        self._show_recovery_notice()
        self._refresh_now()

    # --- construction ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setObjectName("Content")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 0)
        root.setSpacing(0)

        self.title_label = QLabel(self._title)
        self.title_label.setProperty("role", "heading")
        root.addWidget(self.title_label)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 8, 0, 10)
        filters.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search downloads…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMaximumWidth(340)
        self.search_input.setMinimumWidth(200)
        self.search_input.setAccessibleName("Search downloads")
        self.status_combo = QComboBox()
        self.status_combo.setAccessibleName("Status filter")
        for name in P.STATUS_COMBO_FILTERS:
            self.status_combo.addItem(f"Status: {name}", name)
        self.sort_combo = QComboBox()
        self.sort_combo.setAccessibleName("Sort order")
        for name in P.SORT_KEYS:
            self.sort_combo.addItem(f"Sort: {name}", name)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "muted")
        filters.addWidget(self.search_input)
        filters.addWidget(self.status_combo)
        filters.addWidget(self.sort_combo)
        filters.addStretch(1)
        filters.addWidget(self.count_label)
        self._filters_row = QWidget()
        self._filters_row.setLayout(filters)

        self.bulk_bar = QFrame()
        self.bulk_bar.setObjectName("BulkBar")
        bulk = QHBoxLayout(self.bulk_bar)
        bulk.setContentsMargins(12, 0, 8, 0)
        bulk.setSpacing(4)
        self.bulk_count_label = QLabel("")
        self.bulk_count_label.setProperty("role", "dialogTitle")
        bulk.addWidget(self.bulk_count_label)
        self.bulk_buttons: dict[P.RowAction, QPushButton] = {}
        for action, label, glyph in (
            (P.RowAction.PAUSE, "Pause", "pause"), (P.RowAction.RESUME, "Resume", "play"), (P.RowAction.HOLD, "Hold", "lock"),
            (P.RowAction.RELEASE, "Release", "unlock"),
        ):
            button = self._flat_button(label, glyph)
            self.bulk_buttons[action] = button
            bulk.addWidget(button)
        self.bulk_priority_button = self._flat_button("Priority", "arrow-up")
        bulk.addWidget(self.bulk_priority_button)
        cancel_button = self._flat_button("Cancel", "x-circle")
        self.bulk_buttons[P.RowAction.CANCEL] = cancel_button
        bulk.addWidget(cancel_button)
        bulk.addStretch(1)
        self.bulk_clear_button = self._flat_button("Clear selection", "x")
        bulk.addWidget(self.bulk_clear_button)
        self.bulk_bar.setFixedHeight(36)
        self.bulk_bar.setVisible(False)

        self.empty_state = QWidget()
        empty = QVBoxLayout(self.empty_state)
        empty.addStretch(2)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel("No downloads yet")
        self.empty_title.setProperty("role", "dialogTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text = QLabel("Press Ctrl+N to add a download.")
        self.empty_text.setProperty("role", "muted")
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_add_button = QPushButton("Add download")
        self.empty_add_button.setProperty("variant", "primary")
        for widget in (self.empty_icon, self.empty_title, self.empty_text):
            empty.addWidget(widget)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.empty_add_button)
        row.addStretch(1)
        empty.addLayout(row)
        empty.addStretch(3)

        self.table = DownloadTableView(self._theme)
        self.table.setModel(self.proxy)
        self.table.apply_column_layout(P.column_layout(1366))

        self._stack = QStackedWidget()
        self._stack.addWidget(self.empty_state)  # 0
        self._stack.addWidget(self.table)  # 1

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "caption")
        self.hint_label = QLabel("Double-click opens details · Right-click shows actions · Ctrl+N adds a download")
        self.hint_label.setProperty("role", "caption")

        root.addWidget(self._filters_row)
        root.addWidget(self.bulk_bar)
        root.addWidget(self._stack, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 6, 2, 8)
        footer.addWidget(self.hint_label)
        footer.addStretch(1)
        footer.addWidget(self.status_label)
        root.addLayout(footer)
        self._refresh_icons()

    def _flat_button(self, label: str, glyph: str) -> QPushButton:
        button = QPushButton(label)
        button.setProperty("variant", "tertiary")
        button.setProperty("glyph", glyph)
        button.setFixedHeight(26)
        return button

    def _refresh_icons(self) -> None:
        p = palette(self._theme)
        m = metrics()
        from PySide6.QtCore import QSize

        for button in [*self.bulk_buttons.values(), self.bulk_priority_button, self.bulk_clear_button]:
            button.setIcon(icons.icon(button.property("glyph"), 14, p.accent_text, disabled_color=p.text_disabled))
            button.setIconSize(QSize(14, 14))
        self.empty_icon.setPixmap(icons.pixmap("download", 26, p.accent_text))
        _ = m

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.table.delegate.set_theme(theme)
        self._refresh_icons()
        self.table.viewport().update()

    def _wire(self) -> None:
        self.search_input.textChanged.connect(self._on_search_changed)
        self.status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.empty_add_button.clicked.connect(self.add_requested)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._on_selection_changed())
        self.table.details_requested.connect(self.show_details)
        self.table.context_requested.connect(self.show_context_menu)
        self.table.space_requested.connect(self._toggle_pause_resume)
        delegate = self.table.delegate
        delegate.action_requested.connect(self._on_row_action)
        delegate.more_requested.connect(self._on_more_requested)
        header = self.table.horizontalHeader()
        header.sectionClicked.connect(self._on_header_clicked)
        for action, button in self.bulk_buttons.items():
            button.clicked.connect(lambda _=False, a=action: self.run_action(a, self.selected_queue_entry_ids()))
        self.bulk_priority_button.clicked.connect(self._show_bulk_priority_menu)
        self.bulk_clear_button.clicked.connect(self.table.clearSelection)

    # --- filtering / titles -------------------------------------------------------------------

    def set_status_filter(self, name: str, title: str | None = None) -> None:
        self.proxy.set_category_filter(None)
        self.proxy.set_status_filter(name)
        index = self.status_combo.findData(name)
        if index >= 0 and self.status_combo.currentIndex() != index:
            self.status_combo.blockSignals(True)
            self.status_combo.setCurrentIndex(index)
            self.status_combo.blockSignals(False)
        self._set_title(title or ("All Downloads" if name == "All" else name))
        self._update_counts_and_empty_state()

    def set_category_filter(self, category: str) -> None:
        self.proxy.set_status_filter("All")
        self.proxy.set_category_filter(category)
        self.status_combo.blockSignals(True)
        self.status_combo.setCurrentIndex(0)
        self.status_combo.blockSignals(False)
        self._set_title(category)
        self._update_counts_and_empty_state()

    def _set_title(self, title: str) -> None:
        self._title = title
        self.title_label.setText(title)

    def _on_search_changed(self, text: str) -> None:
        self.proxy.set_search_text(text)
        self._update_counts_and_empty_state()

    def _on_status_combo_changed(self, index: int) -> None:
        name = self.status_combo.itemData(index)
        if name:
            self.proxy.set_category_filter(None)
            self.proxy.set_status_filter(name)
            self._set_title("All Downloads" if name == "All" else name)
            self._update_counts_and_empty_state()

    def _on_sort_changed(self, index: int) -> None:
        key = self.sort_combo.itemData(index)
        if key:
            self.proxy.set_sort(key, descending=(key == "Newest"))

    def _on_header_clicked(self, column: int) -> None:
        key = self.proxy.sort_key_for_column(column)
        if key is None:
            return
        descending = self.proxy.sort_key == key and not self.proxy._descending  # noqa: SLF001
        self.proxy.set_sort(key, descending=descending)
        i = self.sort_combo.findData(key)
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentIndex(max(0, i))
        self.sort_combo.blockSignals(False)

    def apply_width(self, width: int) -> P.ColumnLayout:
        layout = P.column_layout(width)
        self.model.set_merge_speed_eta(layout.merge_speed_eta)
        self.table.apply_column_layout(layout)
        return layout

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)

    def publish_state(self) -> None:
        """Re-emit counts and the summary from the current model (for late-connecting listeners)."""
        self._update_counts_and_empty_state()
        snapshot_items = self.model.items()
        active = sum(1 for i in snapshot_items if i.task_state == DownloadTaskState.TRANSFERRING)
        waiting = sum(1 for i in snapshot_items if P.matches_status_filter(i, "Waiting"))
        self.summary_changed.emit(active, waiting, self._last_speed_text)

    # --- shutdown / close -------------------------------------------------------------------------

    def shutdown(self) -> None:
        for job in list(self._resolve_jobs):
            job.cancel()
        self._resolve_jobs.clear()
        self._retry_timer.stop()
        self._bridge.detach()

    def confirm_close(self) -> bool:
        if self._manager.state != ManagerState.RUNNING:
            return True
        if self._manager.snapshot().active_transfer_count == 0:
            return True
        reply = QMessageBox.question(
            self, "Rýchlik", "Downloads are still active.\nClosing Rýchlik will wait for active transfers to finish.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Close, QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Close

    def prepare_shutdown(self) -> None:
        self._shutting_down = True
        self.empty_add_button.setEnabled(False)
        self._set_commands_enabled(False)
        self._show_status_text("Shutting down…", persistent=True)

    def _show_recovery_notice(self) -> None:
        report = self._manager.last_recovery_report
        if report is not None and report.actions:
            self._show_status_text(f"Recovered {len(report.actions)} interrupted download(s) from a previous session.", persistent=True)

    # --- refresh (event driven, coalesced) -------------------------------------------------------------

    def _on_manager_event(self, event) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(_REFRESH_COALESCE_MS, self._refresh_now)

    def _retry_tick(self) -> None:
        self._refresh_now()

    def _refresh_now(self) -> None:
        self._refresh_pending = False
        if self._manager.state != ManagerState.RUNNING:
            self._apply_faulted_or_stopped_ui()
            return
        snapshot = self._manager.snapshot(include_history=True, history_limit=_HISTORY_LIMIT)
        self.model.set_items(snapshot.items)
        self._update_counts_and_empty_state()
        waiting = sum(1 for i in snapshot.items if P.matches_status_filter(i, "Waiting"))
        speed = format_speed(snapshot.aggregate_speed_bps) if snapshot.aggregate_speed_bps else ""
        self._last_speed_text = speed
        self.summary_changed.emit(snapshot.active_transfer_count, waiting, speed)
        needs_tick = any(i.task_state == DownloadTaskState.RETRY_WAIT for i in snapshot.items)
        if needs_tick and not self._retry_timer.isActive():
            self._retry_timer.start()
        elif not needs_tick and self._retry_timer.isActive():
            self._retry_timer.stop()
        self.empty_add_button.setEnabled(True)
        self._on_selection_changed()

    def _apply_faulted_or_stopped_ui(self) -> None:
        self._set_commands_enabled(False)
        self.empty_add_button.setEnabled(False)
        if self._manager.state == ManagerState.FAULTED:
            self._show_status_text("Download manager is unavailable (faulted). Restart the application.", persistent=True)

    def _update_counts_and_empty_state(self) -> None:
        total = self.model.rowCount()
        shown = self.proxy.rowCount()
        self.count_label.setText(f"{shown} task{'s' if shown != 1 else ''}")
        if total == 0:
            self.empty_title.setText("No downloads yet")
            self.empty_text.setText("Press Ctrl+N to add a download.")
            self.empty_add_button.setVisible(True)
            self._stack.setCurrentIndex(0)
        elif shown == 0:
            self.empty_title.setText("No downloads match")
            self.empty_text.setText("Change the search or filter to see more.")
            self.empty_add_button.setVisible(False)
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        items = self.model.items()
        status_counts = {name: sum(1 for i in items if P.matches_status_filter(i, name)) for name in P.SIDEBAR_FILTERS}
        category_counts = {c: sum(1 for i in items if P.category_for(i.display_name) == c) for c in P.CATEGORIES}
        self.counts_changed.emit({"status": status_counts, "category": category_counts})

    # --- selection / identity -------------------------------------------------------------------------

    def selected_queue_entry_ids(self) -> list[str]:
        return self.table.selected_ids()

    def _selected_queue_entry_id(self) -> str | None:
        ids = self.selected_queue_entry_ids()
        return ids[0] if len(ids) == 1 else None

    def selected_items(self) -> list[DownloadViewSnapshot]:
        out = []
        for entry_id in self.selected_queue_entry_ids():
            item = self.model.item_for_id(entry_id)
            if item is not None:
                out.append(item)
        return out

    def _on_selection_changed(self) -> None:
        items = self.selected_items()
        self.table.refresh_hover_state()
        many = len(items) >= 2
        self.bulk_bar.setVisible(many)
        if many:
            self.bulk_count_label.setText(f"{len(items)} selected")
            enabled = P.bulk_actions(items)
            for action, button in self.bulk_buttons.items():
                button.setEnabled(enabled[action] and self._manager.state == ManagerState.RUNNING)
            self.bulk_priority_button.setEnabled(any(P.can_change_priority(i) for i in items))

    def _set_commands_enabled(self, enabled: bool) -> None:
        for button in [*self.bulk_buttons.values(), self.bulk_priority_button]:
            button.setEnabled(enabled)

    # --- commands: exactly one service call per affected occurrence -----------------------------------

    def run_action(self, action: P.RowAction, entry_ids: list[str]) -> None:
        if action in (P.RowAction.MOVE_UP, P.RowAction.MOVE_DOWN):
            if len(entry_ids) == 1:
                self._move(entry_ids[0], -1 if action == P.RowAction.MOVE_UP else 1)
            return
        if action == P.RowAction.OPEN_FOLDER:
            self.open_folder(entry_ids[0]) if entry_ids else None
            return
        if action == P.RowAction.SHARE:
            self.share(entry_ids[0]) if entry_ids else None
            return
        if action == P.RowAction.DETAILS:
            item = self.model.item_for_id(entry_ids[0]) if entry_ids else None
            if item is not None:
                self.show_details(item.queue_entry_id)
            return
        if action not in _SERVICE_CALL:
            return
        verb, method_name = _SERVICE_CALL[action]
        method = getattr(self._manager, method_name)
        applied = rejected = 0
        last_reason = ""
        for entry_id in entry_ids:
            item = self.model.item_for_id(entry_id)
            if item is None:
                continue  # left the snapshot since it was selected -- never redirect to another row
            state = P.available_actions(item)[action]
            if not (state.visible and state.enabled):
                continue
            try:
                result: ManagerCommandResult = method(entry_id)
            except Exception as exc:  # noqa: BLE001
                self._show_command_error(verb, exc)
                return
            if result.status == CommandStatus.REJECTED:
                rejected += 1
                last_reason = result.reason or "rejected"
            else:
                applied += 1
        if rejected and not applied:
            self._show_status_text(f"Could not {verb}: {last_reason}")
        elif applied:
            noun = "download" if applied == 1 else "downloads"
            self._show_status_text(f"{_PAST[action]} requested for {applied} {noun}.")
        self._refresh_now()

    def set_priority(self, priority: QueuePriority, entry_ids: list[str]) -> None:
        changed = 0
        for entry_id in entry_ids:
            item = self.model.item_for_id(entry_id)
            if item is None or not P.can_change_priority(item):
                continue
            try:
                result = self._manager.set_priority(entry_id, priority)
            except Exception as exc:  # noqa: BLE001
                self._show_command_error("change priority of", exc)
                return
            changed += 1 if result.status != CommandStatus.REJECTED else 0
        if changed:
            self._show_status_text(f"Priority set to {format_priority(priority)}.")
        self._refresh_now()

    def _move(self, entry_id: str, direction: int) -> None:
        target = P.band_neighbor(self.model.items(), entry_id, direction)
        if target is None:
            return  # already at the edge of its band -- a no-op, not an error
        try:
            (self._manager.move_before if direction < 0 else self._manager.move_after)(entry_id, target)
        except Exception as exc:  # noqa: BLE001
            self._show_command_error("reorder", exc)
        self._refresh_now()

    def _toggle_pause_resume(self) -> None:
        item = self.selected_items()[0] if len(self.selected_items()) == 1 else None
        if item is None:
            return
        if item.task_state == DownloadTaskState.TRANSFERRING:
            self.run_action(P.RowAction.PAUSE, [item.queue_entry_id])
        elif item.task_state == DownloadTaskState.PAUSED:
            self.run_action(P.RowAction.RESUME, [item.queue_entry_id])

    def _on_row_action(self, entry_id: str, action) -> None:
        self.run_action(action, [entry_id])

    def _on_more_requested(self, entry_id: str, global_pos: QPoint) -> None:
        proxy_row = next((r for r in range(self.proxy.rowCount()) if self.proxy.index(r, 0).data(Qt.ItemDataRole.UserRole) == entry_id), None)
        if proxy_row is not None and entry_id not in self.selected_queue_entry_ids():
            from PySide6.QtCore import QItemSelectionModel

            self.table.selectionModel().select(self.proxy.index(proxy_row, 0),
                                               QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        self.show_context_menu(global_pos)

    # --- context menu -----------------------------------------------------------------------------------

    def build_context_menu(self) -> QMenu | None:
        items = self.selected_items()
        if not items:
            return None
        p = palette(self._theme)
        menu = QMenu(self)

        running = self._manager.state == ManagerState.RUNNING and not self._shutting_down

        def add(text, glyph, action=None, slot=None, enabled=True, tip=None, danger=False) -> QAction:
            act = menu.addAction(icons.icon(glyph, 15, p.error_text if danger else p.icon, disabled_color=p.text_disabled), text)
            act.setEnabled(enabled and (running or action is None and slot is not None and text in ("Details",)))
            if tip:
                act.setToolTip(tip)
                act.setStatusTip(tip)
            if slot is not None:
                act.triggered.connect(slot)
            elif action is not None:
                ids = [i.queue_entry_id for i in items]
                act.triggered.connect(lambda _=False, a=action, ids=ids: self.run_action(a, ids))
            return act

        if len(items) == 1:
            item = items[0]
            acts = P.available_actions(item, band_position=self.model.data(self.model.index(self.model_row(item.queue_entry_id), 0), Qt.ItemDataRole.UserRole + 2))
            group_a = False
            for action, text, glyph in (
                (P.RowAction.PAUSE, "Pause", "pause"), (P.RowAction.RESUME, "Resume", "play"), (P.RowAction.RETRY_NOW, "Retry now", "refresh"),
                (P.RowAction.HOLD, "Hold", "lock"), (P.RowAction.RELEASE, "Release", "unlock"),
            ):
                if acts[action].visible:
                    tip = "Do not start this download until it is released. A running transfer is not stopped; use Pause for that." if action == P.RowAction.HOLD else None
                    add(text, glyph, action, tip=tip)
                    group_a = True
            if acts[P.RowAction.MOVE_UP].visible:
                if group_a:
                    menu.addSeparator()
                sub = menu.addMenu(icons.icon("arrow-up", 15, p.icon), "Priority")
                for prio in P.PRIORITY_ORDER:
                    a = sub.addAction(("✓ " if prio == item.priority else "    ") + format_priority(prio))
                    a.triggered.connect(lambda _=False, pr=prio, i=item.queue_entry_id: self.set_priority(pr, [i]))
                sub.addSeparator()
                note = sub.addAction("Order changes apply within the same priority.")
                note.setEnabled(False)
                for action, text, glyph in ((P.RowAction.MOVE_UP, "Move up", "arrow-up"), (P.RowAction.MOVE_DOWN, "Move down", "arrow-down")):
                    st = acts[action]
                    add(text, glyph, action, enabled=st.enabled, tip=st.reason)
            if acts[P.RowAction.CANCEL].visible:
                menu.addSeparator()
                add("Cancel", "x-circle", P.RowAction.CANCEL, danger=True)
            def separator_if_needed() -> None:
                last = menu.actions()[-1] if menu.actions() else None
                if last is not None and not last.isSeparator():
                    menu.addSeparator()

            secondary = [(P.RowAction.OPEN, "Open", "external"), (P.RowAction.OPEN_FOLDER, "Open folder", "folder"), (P.RowAction.DETAILS, "Details", "info"),
                         (P.RowAction.SHARE, "Share…", "send")]
            for action, text, glyph in secondary:
                if not acts[action].visible:
                    continue
                if action == P.RowAction.OPEN or (action == P.RowAction.DETAILS and not any(acts[a].visible for a in (P.RowAction.OPEN, P.RowAction.OPEN_FOLDER))):
                    separator_if_needed()
                if action == P.RowAction.SHARE:
                    separator_if_needed()
                if action == P.RowAction.OPEN:
                    add(text, glyph, slot=lambda _=False, i=item.queue_entry_id: self.open_file(i))
                elif action == P.RowAction.OPEN_FOLDER:
                    add(text, glyph, slot=lambda _=False, i=item.queue_entry_id: self.open_folder(i))
                elif action == P.RowAction.DETAILS:
                    add(text, glyph, slot=lambda _=False, i=item.queue_entry_id: self.show_details(i))
                else:
                    add(text, glyph, slot=lambda _=False, i=item.queue_entry_id: self.share(i))
        else:
            enabled = P.bulk_actions(items)
            for action, text, glyph in ((P.RowAction.PAUSE, "Pause", "pause"), (P.RowAction.RESUME, "Resume", "play"),
                                        (P.RowAction.HOLD, "Hold", "lock"), (P.RowAction.RELEASE, "Release", "unlock")):
                add(text, glyph, action, enabled=enabled[action])
            sub = menu.addMenu(icons.icon("arrow-up", 15, p.icon), "Priority")
            ids = [i.queue_entry_id for i in items]
            for prio in P.PRIORITY_ORDER:
                sub.addAction(format_priority(prio)).triggered.connect(lambda _=False, pr=prio, ids=ids: self.set_priority(pr, ids))
            menu.addSeparator()
            add("Cancel", "x-circle", P.RowAction.CANCEL, enabled=enabled[P.RowAction.CANCEL], danger=True)
        return menu

    def model_row(self, entry_id: str) -> int:
        for row, item in enumerate(self.model.items()):
            if item.queue_entry_id == entry_id:
                return row
        return 0

    def show_context_menu(self, global_pos: QPoint) -> None:
        menu = self.build_context_menu()
        if menu is not None:
            menu.exec(global_pos)

    def _show_bulk_priority_menu(self) -> None:
        menu = QMenu(self)
        ids = self.selected_queue_entry_ids()
        for prio in P.PRIORITY_ORDER:
            menu.addAction(format_priority(prio)).triggered.connect(lambda _=False, pr=prio, ids=ids: self.set_priority(pr, ids))
        menu.exec(self.bulk_priority_button.mapToGlobal(self.bulk_priority_button.rect().bottomLeft()))

    # --- add download -----------------------------------------------------------------------------------

    @property
    def destination_dir(self) -> Path:
        return self._destination_dir

    def choose_destination(self) -> Path | None:
        chosen = self._destination_chooser(self, str(self._destination_dir))
        if not chosen:
            return None
        path = Path(chosen)
        if not path.is_dir():
            QMessageBox.warning(self, "Rýchlik", f"Not a valid directory:\n{path}")
            return None
        self._destination_dir = path
        return path

    def submit_download(self, url: str, destination_dir: Path | None = None, media=None) -> bool:
        """Adds one download. Returns True only after the service accepted it.
        A bounded re-entrancy guard (not a global lock) keeps a double submit from adding twice."""
        if self._add_in_progress:
            return False
        self._add_in_progress = True
        try:
            return self._submit_download_impl(url, destination_dir, media)
        finally:
            self._add_in_progress = False

    def _submit_download_impl(self, url: str, destination_dir: Path | None, media=None) -> bool:
        url = url.strip()
        if not url:
            QMessageBox.warning(self, "Rýchlik", "Enter a URL first.")
            return False
        destination = Path(destination_dir) if destination_dir is not None else self._destination_dir
        if not destination.is_dir():
            QMessageBox.warning(self, "Rýchlik", f"Destination is not a valid directory:\n{destination}")
            return False
        try:
            request = DownloadRequest(url=url, destination_dir=destination, media=media)
        except ValueError as exc:
            QMessageBox.warning(self, "Rýchlik", f"Invalid download request:\n{exc}")
            return False
        try:
            self._manager.add_download(request)
        except Exception as exc:  # noqa: BLE001
            self._show_command_error("add the download", exc)
            return False
        self._destination_dir = destination
        self._refresh_now()
        return True

    # --- add download through site modules ---------------------------------------------------------------------

    def submit_download_async(self, url: str, destination_dir: Path | None, on_done, media=None):
        """Add-download entry point used by the dialog. `on_done(ok, message)` is called on the GUI thread once the download
        was added or refused. With no module service this is exactly the ordinary path, completed synchronously.
        Returns a job (with cancel()) while a module resolves the URL, else None."""
        if self._resolve_service is None:
            on_done(self.submit_download(url, destination_dir, media), "")
            return None
        url = url.strip()
        if not url:
            QMessageBox.warning(self, "Rýchlik", "Enter a URL first.")
            on_done(False, "")
            return None
        destination = Path(destination_dir) if destination_dir is not None else self._destination_dir
        if not destination.is_dir():
            QMessageBox.warning(self, "Rýchlik", f"Destination is not a valid directory:\n{destination}")
            on_done(False, "")
            return None
        job = self._resolve_service.start(
            url, destination, lambda finished_job, outcome: self._resolve_finished.emit(finished_job, outcome, (url, destination, on_done, media))
        )
        self._resolve_jobs.add(job)
        return job

    def _on_resolve_finished(self, job, outcome, context) -> None:
        self._resolve_jobs.discard(job)
        url, destination, on_done, media = context
        if job.cancelled or self._shutting_down:
            return  # the user cancelled or the window is closing: nothing is enqueued and no stale callback runs
        from rychlik.modules.resolve_service import ResolveKind

        if outcome.kind == ResolveKind.PLAIN:
            on_done(self.submit_download(url, destination, media), "")
        elif outcome.kind == ResolveKind.RESOLVED:
            try:
                self._manager.add_download(outcome.request)
            except Exception as exc:  # noqa: BLE001
                self._show_command_error("add the download", exc)
                on_done(False, "")
                return
            self._destination_dir = destination
            self._refresh_now()
            on_done(True, f"Added using {outcome.module_name}" if outcome.module_name else "")
        elif outcome.kind == ResolveKind.AMBIGUOUS:
            on_done(False, outcome.message)
        elif outcome.kind == ResolveKind.FAILED:
            who = f"{outcome.module_name}: " if outcome.module_name else ""
            on_done(False, f"Could not resolve this URL.\n{who}{outcome.message}")
        else:
            on_done(False, "")

    def open_add_dialog(self, url: str = "", media=None) -> None:
        from rychlik.gui.dialogs import AddDownloadDialog

        AddDownloadDialog(self, theme=self._theme, url=url, media=media).exec()

    # --- completed-file bridge: Open / Open folder / Share / Details --------------------------------------

    def open_folder(self, entry_id: str) -> None:
        result = self._manager.completed_file(entry_id)
        if result.info is None:
            self._show_status_text(f"Cannot open folder: {result.reason or result.status.name}")
            return
        self._folder_opener(result.info.local_path.parent)

    def open_file(self, entry_id: str) -> None:
        result = self._manager.completed_file(entry_id)
        if result.info is None:
            self._show_status_text(f"Cannot open file: {result.reason or result.status.name}")
            return
        self._folder_opener(result.info.local_path)

    def share(self, entry_id: str) -> None:
        artifact, result = build_artifact_for_completed(self._manager, entry_id)
        if artifact is None:
            self._show_status_text(f"Cannot share: {result.reason or result.status.name}")
            return
        launcher = self._share_launcher
        if launcher is None:
            ShareDialog(artifact, parent=self).exec()
        else:
            launcher(artifact, self)

    def show_details(self, entry_id: str) -> None:
        item = self.model.item_for_id(entry_id)
        if item is None:
            return
        if self._details_launcher is not None:
            self._details_launcher(item, self)
            return
        from rychlik.gui.dialogs import DetailsDialog

        DetailsDialog(self._manager, item, self, theme=self._theme).exec()

    # --- messages -------------------------------------------------------------------------------------------

    def _show_status_text(self, text: str, *, persistent: bool = False) -> None:
        self.status_label.setText(text)
        self.status_message.emit(text)
        if not persistent:
            QTimer.singleShot(_STATUS_MESSAGE_LIFETIME_MS, self._clear_if_unchanged(text))

    def _show_status_message(self, text: str) -> None:
        self._show_status_text(text)

    def _clear_if_unchanged(self, text: str):
        def _clear() -> None:
            if self.status_label.text() == text:
                self.status_label.setText("")

        return _clear

    def _show_command_error(self, verb: str, exc: Exception) -> None:
        if isinstance(exc, ManagerFaultedError):
            self._show_status_text("Download manager is unavailable (faulted). Restart the application.", persistent=True)
            self._apply_faulted_or_stopped_ui()
            return
        QMessageBox.warning(self, "Rýchlik", f"Could not {verb} this download.\n{exc}")
