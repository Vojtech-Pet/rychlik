"""Approved Queues page: one download queue shown as High / Normal / Low priority bands.

Reads DownloadManagerService.snapshot() (live occurrences only, in the scheduler's canonical order)
and sends reorder / priority / hold commands back through the owning DownloadManagerWidget, so every
command path (identity checks, one service call per occurrence, error handling) is the same as on
the main page. Reordering only ever happens inside a band (the backend refuses cross-band moves).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QTableView, QVBoxLayout, QWidget

from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui import presentation as P
from rychlik.gui.formatters import format_bytes, format_priority
from rychlik.gui.theme import icons
from rychlik.gui.theme.tokens import palette

ID_ROLE = Qt.ItemDataRole.UserRole
_HEADERS = ("#", "Name", "Size", "Status", "Held")


class QueueBandModel(QAbstractTableModel):
    """Rows are either a band header ("band") or a live download ("item")."""

    def __init__(self, theme: str = "dark", parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple] = []
        self._p = palette(theme)

    def set_theme(self, theme: str) -> None:
        self._p = palette(theme)
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(_HEADERS) - 1))

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        if self._rows[index.row()][0] == "band":
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_items(self, items) -> None:
        live = [i for i in items if P.is_live(i)]
        rows: list[tuple] = []
        for priority in P.PRIORITY_ORDER:
            band = [i for i in live if i.priority == priority]
            rows.append(("band", priority, len(band)))
            for position, item in enumerate(band, 1):
                rows.append(("item", item, position))
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def is_band_row(self, row: int) -> bool:
        return self._rows[row][0] == "band"

    def band_rows(self) -> list[int]:
        return [r for r, row in enumerate(self._rows) if row[0] == "band"]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        p = self._p
        if row[0] == "band":
            _, priority, count = row
            if role == Qt.ItemDataRole.DisplayRole and col == 0:
                return f"{format_priority(priority).upper()} PRIORITY  ·  {count} download{'s' if count != 1 else ''}"
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor(p.surface2)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(p.text2)
            if role == Qt.ItemDataRole.FontRole:
                f = QFont()
                f.setBold(True)
                f.setPixelSize(11)
                return f
            return None
        _, item, position = row
        info = P.status_info(item)
        if role == ID_ROLE:
            return item.queue_entry_id
        if role == Qt.ItemDataRole.UserRole + 1:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return {0: str(position), 1: item.display_name or "(unknown)", 2: format_bytes(item.total_bytes), 3: info.label,
                    4: "Held" if info.held else "—"}[col]
        if role == Qt.ItemDataRole.DecorationRole:
            if col == 1:
                return icons.icon(P.category_icon(P.category_for(item.display_name)), 16, p.icon)
            if col == 3:
                return icons.icon(info.glyph, 14, p.status_text_color(info.tone))
            if col == 4 and info.held:
                return icons.icon("lock", 12, p.text2)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 3:
                return QColor(p.status_text_color(info.tone))
            if col in (0, 4):
                return QColor(p.text2)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{position}. {item.display_name or 'Unknown file'}, {info.label}{', held' if info.held else ''}"
        return None


class QueueView(QWidget):
    def __init__(self, manager, page_widget, *, theme: str = "dark", parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._page = page_widget  # the DownloadManagerWidget: single owner of command handling
        self._theme = theme
        self.setObjectName("Content")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 8)
        title = QLabel("Queues")
        title.setProperty("role", "heading")
        root.addWidget(title)
        intro = QLabel("One download queue with three priority bands. Downloads start from the top of High, then Normal, then Low. "
                       "Reordering works within a band.")
        intro.setProperty("role", "muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("BulkBar")
        bar = QHBoxLayout(self.action_bar)
        bar.setContentsMargins(12, 0, 8, 0)
        self.selected_label = QLabel("Select a download")
        self.selected_label.setProperty("role", "dialogTitle")
        bar.addWidget(self.selected_label)
        self.up_button = self._button("Move up", "arrow-up")
        self.down_button = self._button("Move down", "arrow-down")
        self.priority_button = self._button("Priority", "arrow-up")
        self.hold_button = self._button("Hold", "lock")
        self.release_button = self._button("Release", "unlock")
        for b in (self.up_button, self.down_button, self.priority_button, self.hold_button, self.release_button):
            bar.addWidget(b)
        bar.addStretch(1)
        self.action_bar.setFixedHeight(36)
        root.addSpacing(8)
        root.addWidget(self.action_bar)
        root.addSpacing(8)

        self.model = QueueBandModel(theme, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(16, 16))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 48), (2, 90), (3, 170), (4, 90)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)
        note = QLabel("Held keeps a download from starting and does not stop one that is running. Pause stops a running transfer.")
        note.setProperty("role", "caption")
        note.setWordWrap(True)
        root.addSpacing(8)
        root.addWidget(note)

        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.up_button.clicked.connect(lambda: self._reorder(P.RowAction.MOVE_UP))
        self.down_button.clicked.connect(lambda: self._reorder(P.RowAction.MOVE_DOWN))
        self.hold_button.clicked.connect(lambda: self._act(P.RowAction.HOLD))
        self.release_button.clicked.connect(lambda: self._act(P.RowAction.RELEASE))
        self.priority_button.clicked.connect(self._priority_menu)
        page_widget.model.modelReset.connect(self.refresh)
        page_widget.model.rowsInserted.connect(lambda *_: self.refresh())
        page_widget.model.rowsRemoved.connect(lambda *_: self.refresh())
        page_widget.model.layoutChanged.connect(self.refresh)
        page_widget.model.dataChanged.connect(lambda *_: self.refresh())
        self.refresh()

    def _button(self, label: str, glyph: str) -> QPushButton:
        button = QPushButton(label)
        button.setProperty("variant", "tertiary")
        button.setProperty("glyph", glyph)
        button.setIcon(icons.icon(glyph, 14, palette(self._theme).accent_text, disabled_color=palette(self._theme).text_disabled))
        button.setIconSize(QSize(14, 14))
        button.setFixedHeight(26)
        return button

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.model.set_theme(theme)
        p = palette(theme)
        for b in (self.up_button, self.down_button, self.priority_button, self.hold_button, self.release_button):
            b.setIcon(icons.icon(b.property("glyph"), 14, p.accent_text, disabled_color=p.text_disabled))

    def selected_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].data(ID_ROLE) if rows else None

    def refresh(self) -> None:
        selected = self.selected_id()
        self.model.set_items(self._page.model.items())
        self.table.clearSpans()
        for row in self.model.band_rows():
            self.table.setSpan(row, 0, 1, 5)
            self.table.setRowHeight(row, 28)
        if selected is not None:
            for row in range(self.model.rowCount()):
                if self.model.index(row, 0).data(ID_ROLE) == selected:
                    self.table.selectRow(row)
                    break
        self._update_buttons()

    def _item(self) -> DownloadViewSnapshot | None:
        entry_id = self.selected_id()
        return self._page.model.item_for_id(entry_id) if entry_id else None

    def _update_buttons(self) -> None:
        item = self._item()
        if item is None:
            self.selected_label.setText("Select a download")
            for b in (self.up_button, self.down_button, self.priority_button, self.hold_button, self.release_button):
                b.setEnabled(False)
            return
        positions = P.band_positions(self._page.model.items())
        acts = P.available_actions(item, band_position=positions.get(item.queue_entry_id))
        self.selected_label.setText("1 selected")
        self.up_button.setEnabled(acts[P.RowAction.MOVE_UP].enabled)
        self.down_button.setEnabled(acts[P.RowAction.MOVE_DOWN].enabled)
        self.up_button.setToolTip(acts[P.RowAction.MOVE_UP].reason or "")
        self.down_button.setToolTip(acts[P.RowAction.MOVE_DOWN].reason or "")
        self.priority_button.setEnabled(P.can_change_priority(item))
        self.hold_button.setEnabled(acts[P.RowAction.HOLD].visible and acts[P.RowAction.HOLD].enabled)
        self.release_button.setEnabled(acts[P.RowAction.RELEASE].visible and acts[P.RowAction.RELEASE].enabled)

    def _reorder(self, action) -> None:
        entry_id = self.selected_id()
        if entry_id:
            self._page.run_action(action, [entry_id])

    def _act(self, action) -> None:
        entry_id = self.selected_id()
        if entry_id:
            self._page.run_action(action, [entry_id])

    def _priority_menu(self) -> None:
        entry_id = self.selected_id()
        if not entry_id:
            return
        menu = QMenu(self)
        item = self._item()
        for prio in P.PRIORITY_ORDER:
            label = ("✓ " if item is not None and item.priority == prio else "    ") + format_priority(prio)
            menu.addAction(label).triggered.connect(lambda _=False, pr=prio: self._page.set_priority(pr, [entry_id]))
        menu.exec(self.priority_button.mapToGlobal(self.priority_button.rect().bottomLeft()))
