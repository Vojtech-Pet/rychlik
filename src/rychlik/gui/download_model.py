"""Qt model/view data layer for the approved download table.

`DownloadTableModel` holds the latest immutable DownloadViewSnapshot list and
updates INCREMENTALLY (row removal / insertion / reorder by queue_entry_id), so
the view keeps its selection and scroll position across the ~10 refreshes per
second a busy queue produces. Logical identity is always `queue_entry_id`
(ID_ROLE), never a row number.

The model never calls the backend and never mutates a snapshot.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui import presentation as P
from rychlik.gui.formatters import format_bytes, format_eta, format_speed

ID_ROLE = Qt.ItemDataRole.UserRole
ITEM_ROLE = Qt.ItemDataRole.UserRole + 1
BAND_ROLE = Qt.ItemDataRole.UserRole + 2  # (position_in_band, band_size) or None

_HEADERS = ("", "Name", "Category", "Type", "Size", "Progress", "Status", "Speed", "Remaining", "Added", "")

_STATUS_ORDER = {"downloading": 0, "resolving": 1, "verifying": 2, "finishing": 3, "retrying": 4, "waiting": 5, "paused": 6,
                 "failed": 7, "cancelled": 8, "completed": 9}


class DownloadTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[DownloadViewSnapshot] = []
        self._bands: dict[str, tuple[int, int]] = {}
        self._merge_speed_eta = False

    # --- QAbstractTableModel -----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == P.COL_SPEED and self._merge_speed_eta:
                return "Speed"
            return _HEADERS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == ID_ROLE:
            return item.queue_entry_id
        if role == ITEM_ROLE:
            return item
        if role == BAND_ROLE:
            return self._bands.get(item.queue_entry_id)
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display_text(item, column)
        if role == Qt.ItemDataRole.ToolTipRole and column == P.COL_NAME:
            host = f"\n{item.source_host}" if item.source_host else ""
            return f"{item.display_name or '(unknown)'}{host}"
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{item.display_name or 'Unknown file'}, {P.status_info(item).label}"
        return None

    def display_text(self, item: DownloadViewSnapshot, column: int) -> str:
        if column == P.COL_NAME:
            return item.display_name or "(unknown)"
        if column == P.COL_CATEGORY:
            return P.category_for(item.display_name)
        if column == P.COL_TYPE:
            return P.type_label(item.display_name)
        if column == P.COL_SIZE:
            return format_bytes(item.total_bytes)
        if column == P.COL_PROGRESS:
            return "—" if item.progress_fraction is None else f"{round(item.progress_fraction * 100)}%"
        if column == P.COL_STATUS:
            return P.status_info(item).label
        if column == P.COL_SPEED:
            speed = format_speed(item.speed_bps) if (item.speed_bps or 0) > 0 else "—"
            if self._merge_speed_eta and speed != "—":
                return f"{speed} · {format_eta(item.eta_seconds)}"
            return speed
        if column == P.COL_ETA:
            return format_eta(item.eta_seconds) if (item.speed_bps or 0) > 0 else "—"
        if column == P.COL_ADDED:
            return P.format_added(item.added_at)
        return ""

    # --- updates -------------------------------------------------------------------

    def set_merge_speed_eta(self, merge: bool) -> None:
        if merge != self._merge_speed_eta:
            self._merge_speed_eta = merge
            if self._items:
                self.dataChanged.emit(self.index(0, P.COL_SPEED), self.index(len(self._items) - 1, P.COL_SPEED))
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, P.COL_SPEED, P.COL_SPEED)

    @property
    def merge_speed_eta(self) -> bool:
        return self._merge_speed_eta

    def items(self) -> tuple[DownloadViewSnapshot, ...]:
        return tuple(self._items)

    def item_for_id(self, queue_entry_id: str) -> DownloadViewSnapshot | None:
        for item in self._items:
            if item.queue_entry_id == queue_entry_id:
                return item
        return None

    def set_items(self, new_items) -> None:
        """Incremental update keyed by queue_entry_id: removals, insertions, then a pure reorder
        (which keeps persistent indexes, hence selection), then a data refresh."""
        new_items = list(new_items)
        new_ids = [i.queue_entry_id for i in new_items]
        new_id_set = set(new_ids)
        # 1) removals (bottom-up so earlier rows keep their numbers)
        for row in range(len(self._items) - 1, -1, -1):
            if self._items[row].queue_entry_id not in new_id_set:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()
        # 2) reorder the surviving rows to the target relative order
        survivors = [i.queue_entry_id for i in self._items]
        target = [i for i in new_ids if i in set(survivors)]
        by_id = {i.queue_entry_id: i for i in new_items}
        if survivors != target:
            self.layoutAboutToBeChanged.emit()
            old_list = self.persistentIndexList()
            old_ids = [self._items[i.row()].queue_entry_id for i in old_list]
            self._items = [by_id[i] for i in target]
            row_of = {eid: row for row, eid in enumerate(target)}
            self.changePersistentIndexList(old_list, [self.index(row_of[eid], idx.column()) for eid, idx in zip(old_ids, old_list)])
            self.layoutChanged.emit()
        # 3) insertions at their final positions
        for position, item in enumerate(new_items):
            if position >= len(self._items) or self._items[position].queue_entry_id != item.queue_entry_id:
                self.beginInsertRows(QModelIndex(), position, position)
                self._items.insert(position, item)
                self.endInsertRows()
        # 4) refresh values of every row (snapshots are immutable, so replace)
        changed = False
        for row, item in enumerate(new_items):
            if self._items[row] != item:
                self._items[row] = item
                changed = True
        self._bands = P.band_positions(self._items)
        if changed and self._items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._items) - 1, self.columnCount() - 1))


# --- filter / sort proxy ----------------------------------------------------------------------


class DownloadFilterProxy(QSortFilterProxyModel):
    """Search + status + category filtering and sorting. Sorting is by explicit key; the default
    ("Queue order") keeps the backend's canonical order untouched."""

    SORT_QUEUE = "Queue order"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._status = "All"
        self._category: str | None = None
        self._search = ""
        self._sort_key = self.SORT_QUEUE
        self._descending = False
        self.setDynamicSortFilter(True)

    # filters
    def set_status_filter(self, key: str) -> None:
        self.beginFilterChange()
        self._status = key
        self.endFilterChange()

    def set_category_filter(self, category: str | None) -> None:
        self.beginFilterChange()
        self._category = category
        self.endFilterChange()

    def set_search_text(self, text: str) -> None:
        self.beginFilterChange()
        self._search = text
        self.endFilterChange()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        item = self.sourceModel().data(self.sourceModel().index(source_row, 0), ITEM_ROLE)
        if item is None:
            return False
        if not P.matches_status_filter(item, self._status):
            return False
        if self._category is not None and P.category_for(item.display_name) != self._category:
            return False
        return P.matches_search(item, self._search)

    # sorting
    def set_sort(self, key: str, descending: bool = False) -> None:
        if key not in P.SORT_KEYS:
            raise ValueError(f"unknown sort key {key!r}")
        self._sort_key = key
        self._descending = descending
        if key == self.SORT_QUEUE:
            self.sort(-1)
        else:
            self.sort(0, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
        self.invalidate()

    @property
    def sort_key(self) -> str:
        return self._sort_key

    def sort_key_for_column(self, column: int) -> str | None:
        return {P.COL_NAME: "Name", P.COL_SIZE: "Size", P.COL_PROGRESS: "Progress", P.COL_STATUS: "Status", P.COL_ADDED: "Newest"}.get(column)

    def _key(self, item: DownloadViewSnapshot):
        if self._sort_key == "Name":
            return (item.display_name or "").casefold()
        if self._sort_key == "Size":
            return item.total_bytes if item.total_bytes is not None else -1
        if self._sort_key == "Progress":
            return item.progress_fraction if item.progress_fraction is not None else -1.0
        if self._sort_key == "Status":
            return _STATUS_ORDER.get(P.status_info(item).key, 99)
        if self._sort_key == "Newest":
            return item.added_at.timestamp() if item.added_at is not None else 0.0
        return 0

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        if self._sort_key == self.SORT_QUEUE:
            return left.row() < right.row()
        model = self.sourceModel()
        a, b = model.data(left, ITEM_ROLE), model.data(right, ITEM_ROLE)
        ka, kb = self._key(a), self._key(b)
        if ka == kb:
            return left.row() < right.row() if not self._descending else left.row() > right.row()
        return ka < kb

    # identity-safe helpers
    def id_for_index(self, index: QModelIndex) -> str | None:
        return self.data(index, ID_ROLE) if index.isValid() else None

    def item_for_index(self, index: QModelIndex) -> DownloadViewSnapshot | None:
        return self.data(index, ITEM_ROLE) if index.isValid() else None
