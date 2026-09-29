"""Approved Details dialog (Information only).

Shows only what the service really exposes: the flat DownloadViewSnapshot plus, for a completed
download, the privileged completed_file() destination. Effective URL, resume support, backend,
resolver, connections and log are not exposed by the service and therefore are not shown.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from rychlik.core.download_manager_service import CompletedFileStatus, DownloadManagerService, ManagerState
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui import presentation as P
from rychlik.gui.formatters import format_bytes, format_eta, format_priority, format_speed

_REFRESH_MS = 500


def build_rows(manager: DownloadManagerService, item: DownloadViewSnapshot) -> list[tuple[str, str]]:
    """Pure-ish row builder (reads the service, no widgets) so it can be tested directly."""
    info = P.status_info(item)
    status = info.label + (" · Held" if info.held else "")
    rows: list[tuple[str, str]] = [
        ("Name", item.display_name or "(unknown)"),
        ("Status", status),
    ]
    if item.source_host:
        rows.append(("Source", item.source_host))
    rows.append(("Size", format_bytes(item.total_bytes)))
    rows.append(("Downloaded", format_bytes(item.bytes_downloaded if item.bytes_downloaded or item.total_bytes else None)))
    if item.task_state == DownloadTaskState.TRANSFERRING:
        rows.append(("Speed", format_speed(item.speed_bps)))
        rows.append(("Remaining", format_eta(item.eta_seconds)))
    if P.is_live(item):
        rows.append(("Priority", format_priority(item.priority)))
        try:
            positions = P.band_positions(manager.snapshot().items)
        except Exception:  # noqa: BLE001 - manager stopped while the dialog is open
            positions = {}
        if item.queue_entry_id in positions:
            index, size = positions[item.queue_entry_id]
            rows.append(("Queue position", f"{index} of {size} in {format_priority(item.priority)}"))
        rows.append(("Held", "Yes" if info.held else "No"))
    rows.append(("Attempts", str(item.attempt_count)))
    if item.task_state == DownloadTaskState.FAILED and item.last_failure_code:
        rows.append(("Failure", item.last_failure_code))
    if item.added_at is not None:
        rows.append(("Added", P.format_added(item.added_at)))
    if item.task_state == DownloadTaskState.COMPLETED:
        completed = manager.completed_file(item.queue_entry_id)
        if completed.status == CompletedFileStatus.AVAILABLE and completed.info is not None:
            rows.append(("Destination", str(completed.info.local_path)))
        else:
            rows.append(("Destination", f"Unavailable ({completed.reason or completed.status.name})"))
    return rows


class DetailsDialog(QDialog):
    def __init__(self, manager: DownloadManagerService, item: DownloadViewSnapshot, parent=None, *, theme: str = "dark") -> None:
        super().__init__(parent)
        self._manager = manager
        self._entry_id = item.queue_entry_id
        self.setWindowTitle(item.display_name or "Download details")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        self.title_label = QLabel(item.display_name or "(unknown)")
        self.title_label.setProperty("role", "dialogTitle")
        layout.addWidget(self.title_label)
        self.grid = QGridLayout()
        self.grid.setColumnMinimumWidth(0, 130)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(7)
        self.grid.setColumnStretch(1, 1)
        layout.addSpacing(8)
        layout.addLayout(self.grid)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setVisible(False)
        self.close_button = QPushButton("Close")
        self.close_button.setProperty("variant", "primary")
        buttons.addWidget(self.open_folder_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addSpacing(10)
        layout.addLayout(buttons)
        self.close_button.clicked.connect(self.accept)
        self.open_folder_button.clicked.connect(self._open_folder)
        self._parent_widget = parent
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh(item)
        self._timer.start()

    def refresh(self, item: DownloadViewSnapshot | None = None) -> None:
        if item is None:
            if self._manager.state != ManagerState.RUNNING:
                self._timer.stop()
                return
            item = self._manager.item_snapshot(self._entry_id)
            if item is None:
                return
        rows = build_rows(self._manager, item)
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for row, (key, value) in enumerate(rows):
            k = QLabel(key)
            k.setProperty("role", "muted")
            v = QLabel(value)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setWordWrap(True)
            self.grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(v, row, 1)
        self.open_folder_button.setVisible(item.task_state == DownloadTaskState.COMPLETED)

    def _open_folder(self) -> None:
        opener = getattr(self._parent_widget, "open_folder", None)
        if opener is not None:
            opener(self._entry_id)

    def done(self, result: int) -> None:  # noqa: D401
        self._timer.stop()
        super().done(result)
