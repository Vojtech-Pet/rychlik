"""The approved dense download table: painted delegate + view (QTableView, no per-row widgets).

Rows are painted, never instantiated as widgets, so 1,000+ rows stay cheap. The delegate reports
clicks on its painted controls as signals carrying the row's `queue_entry_id`; it never calls the
backend itself.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from rychlik.core.download_queue import QueuePriority
from rychlik.gui import presentation as P
from rychlik.gui.download_model import ID_ROLE, ITEM_ROLE
from rychlik.gui.theme import icons
from rychlik.gui.theme.tokens import Palette, metrics, palette

_ACTION_GLYPH = {
    P.RowAction.PAUSE: "pause", P.RowAction.RESUME: "play", P.RowAction.HOLD: "lock", P.RowAction.RELEASE: "unlock",
    P.RowAction.RETRY_NOW: "refresh", P.RowAction.CANCEL: "x-circle", P.RowAction.OPEN_FOLDER: "folder", P.RowAction.SHARE: "send",
}
ACTION_TOOLTIP = {
    P.RowAction.PAUSE: "Pause the running transfer", P.RowAction.RESUME: "Resume", P.RowAction.RETRY_NOW: "Retry now",
    P.RowAction.HOLD: "Hold: do not start this download until it is released. A running transfer is not stopped.",
    P.RowAction.RELEASE: "Release the hold", P.RowAction.CANCEL: "Cancel this download",
    P.RowAction.OPEN_FOLDER: "Open folder", P.RowAction.SHARE: "Share…",
}
_BUTTON = 24  # hover action button size


def _alpha(hex_color: str, alpha: float) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def _qcolor(css: str) -> QColor:
    """Accepts '#RRGGBB' or 'rgba(r,g,b,a)' (as produced by tokens.accent_tint)."""
    if css.startswith("rgba"):
        r, g, b, a = (float(x) for x in css[css.index("(") + 1 : css.index(")")].split(","))
        return QColor(int(r), int(g), int(b), round(a * 255))
    return QColor(css)


class DownloadItemDelegate(QStyledItemDelegate):
    action_requested = Signal(str, object)  # queue_entry_id, RowAction
    more_requested = Signal(str, QPoint)  # queue_entry_id, global position
    toggle_requested = Signal(QModelIndex)  # checkbox column

    def __init__(self, theme: str = "dark", parent=None) -> None:
        super().__init__(parent)
        self._p: Palette = palette(theme)
        self._hover_row = -1
        self._single_selected_row = -1

    def set_theme(self, theme: str) -> None:
        self._p = palette(theme)

    def set_hover_row(self, row: int) -> None:
        self._hover_row = row

    def set_single_selected_row(self, row: int) -> None:
        self._single_selected_row = row

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), metrics().task_row_height)

    # --- geometry helpers (shared by paint and hit testing) ------------------------------

    @staticmethod
    def action_buttons(cell: QRect, count: int) -> list[QRect]:
        """Right-aligned buttons: [primary actions...][more], laid out from the right edge."""
        rects = []
        x = cell.right() - 4 - _BUTTON
        for _ in range(count + 1):
            rects.append(QRect(x, cell.center().y() - _BUTTON // 2, _BUTTON, _BUTTON))
            x -= _BUTTON + 2
        rects.reverse()
        return rects  # last one is "more"

    @staticmethod
    def retry_button_rect(cell: QRect, width: int = 84) -> QRect:
        return QRect(cell.left() + 4, cell.center().y() - 10, width, 20)

    @staticmethod
    def checkbox_rect(cell: QRect) -> QRect:
        return QRect(cell.left() + 10, cell.center().y() - 8, 16, 16)

    def _wants_actions(self, row: int) -> bool:
        return row == self._hover_row or row == self._single_selected_row

    # --- painting -----------------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item = index.data(ITEM_ROLE)
        if item is None:
            return
        p = self._p
        col = index.column()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = index.row() == self._hover_row
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(p.surface))
        if selected:
            painter.fillRect(rect, _qcolor(p.accent_tint))
        elif hover:
            painter.fillRect(rect, QColor(p.surface2))
        painter.setPen(_alpha(p.border, 0.55))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if selected and col == P.COL_SELECT:
            painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()), QColor(p.accent))

        info = P.status_info(item)
        if col == P.COL_SELECT:
            self._paint_checkbox(painter, rect, selected)
        elif col == P.COL_NAME:
            self._paint_name(painter, rect, item)
        elif col == P.COL_PROGRESS:
            self._paint_progress(painter, rect, item, info)
        elif col == P.COL_STATUS:
            self._paint_status(painter, rect, info)
        elif col == P.COL_SPEED and info.key == "retrying":
            self._paint_retry_button(painter, rect)
        elif col == P.COL_ACTIONS:
            self._paint_actions(painter, rect, item, index.row(), selected or hover)
        else:
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            secondary = col in (P.COL_CATEGORY, P.COL_TYPE, P.COL_ETA, P.COL_ADDED)
            painter.setPen(QColor(p.text2 if secondary else p.text))
            fm = painter.fontMetrics()
            painter.drawText(rect.adjusted(4, 0, -4, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             fm.elidedText(text, Qt.TextElideMode.ElideRight, rect.width() - 8))
        painter.restore()

    def _paint_checkbox(self, painter: QPainter, cell: QRect, selected: bool) -> None:
        p = self._p
        box = self.checkbox_rect(cell)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.accent))
            painter.drawRoundedRect(box, 4, 4)
            painter.drawPixmap(box.left() + 2, box.top() + 2, icons.pixmap("check", 12, p.on_accent, stroke=3.0))
        else:
            painter.setPen(QPen(QColor(p.border_strong), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(box).adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)

    def _paint_name(self, painter: QPainter, cell: QRect, item) -> None:
        p, m = self._p, metrics()
        category = P.category_for(item.display_name)
        pm = icons.pixmap(P.category_icon(category), m.icon_row, p.icon)
        painter.drawPixmap(cell.left() + 4, cell.center().y() - m.icon_row // 2, pm)
        text_left = cell.left() + 4 + m.icon_row + 10
        text_width = cell.right() - text_left - 6
        name_font = QFont(painter.font())
        name_font.setBold(True)
        name_font.setPixelSize(m.font_table)
        sub_font = QFont(painter.font())
        sub_font.setBold(False)
        sub_font.setPixelSize(m.font_caption)
        nfm, sfm = QFontMetrics(name_font), QFontMetrics(sub_font)
        block = nfm.height() + sfm.height()
        top = cell.top() + (cell.height() - block) // 2
        painter.setFont(name_font)
        painter.setPen(QColor(p.text))
        painter.drawText(QRect(text_left, top, text_width, nfm.height()), int(Qt.AlignmentFlag.AlignVCenter),
                         nfm.elidedText(item.display_name or "(unknown)", Qt.TextElideMode.ElideRight, text_width))
        painter.setFont(sub_font)
        x = text_left
        if item.priority in (QueuePriority.HIGH, QueuePriority.LOW) and P.is_live(item):
            glyph = "arrow-up" if item.priority == QueuePriority.HIGH else "arrow-down"
            color = p.accent_text if item.priority == QueuePriority.HIGH else p.text2
            painter.drawPixmap(x, top + nfm.height() + (sfm.height() - 11) // 2, icons.pixmap(glyph, 11, color, stroke=2.4))
            x += 15
        painter.setPen(QColor(p.text2))
        painter.drawText(QRect(x, top + nfm.height(), max(0, cell.right() - x - 6), sfm.height()), int(Qt.AlignmentFlag.AlignVCenter),
                         sfm.elidedText(item.source_host or "", Qt.TextElideMode.ElideRight, max(0, cell.right() - x - 6)))

    def _paint_progress(self, painter: QPainter, cell: QRect, item, info) -> None:
        p, m = self._p, metrics()
        pct_w = 36
        bar = QRect(cell.left() + 4, cell.center().y() - m.progress_height // 2, max(20, cell.width() - pct_w - 16), m.progress_height)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QColor(p.text)
        track.setAlphaF(0.11)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, m.progress_height / 2, m.progress_height / 2)
        fraction = item.progress_fraction
        show = fraction is not None and info.key != "waiting"
        if show:
            fill_w = max(m.progress_height, round(bar.width() * max(0.0, min(1.0, fraction))))
            color = {"info": p.info, "success": p.success, "warning": p.warning, "error": p.error, "neutral": p.neutral}[info.tone]
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRect(bar.left(), bar.top(), fill_w, bar.height()), m.progress_height / 2, m.progress_height / 2)
        painter.setPen(QColor(p.text2))
        painter.drawText(QRect(bar.right() + 6, cell.top(), pct_w, cell.height()),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                         f"{round(fraction * 100)}%" if show else "—")

    def _paint_status(self, painter: QPainter, cell: QRect, info) -> None:
        p, m = self._p, metrics()
        color = p.status_text_color(info.tone)
        x = cell.left() + 4
        painter.drawPixmap(x, cell.center().y() - 7, icons.pixmap(info.glyph, 14, color))
        x += 20
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(m.font_table)
        painter.setFont(font)
        painter.setPen(QColor(color))
        fm = QFontMetrics(font)
        available = max(0, cell.right() - x - 6)
        advance = fm.horizontalAdvance(info.label)
        label = info.label if advance <= available else fm.elidedText(info.label, Qt.TextElideMode.ElideRight, available)
        label_w = min(advance, available)
        painter.drawText(QRect(x, cell.top(), available, cell.height()), int(Qt.AlignmentFlag.AlignVCenter), label)
        if info.held:
            chip_font = QFont(font)
            chip_font.setPixelSize(m.font_caption - 0.5)
            painter.setFont(chip_font)
            cfm = QFontMetrics(chip_font)
            chip_w = 11 + 4 + cfm.horizontalAdvance("Held") + 12
            chip_x = x + label_w + 8
            if chip_x + chip_w <= cell.right():
                chip = QRect(chip_x, cell.center().y() - 9, chip_w, 18)
                painter.setPen(QPen(QColor(p.border_strong), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(chip).adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
                painter.drawPixmap(chip.left() + 6, chip.center().y() - 5, icons.pixmap("lock", 11, p.text2))
                painter.setPen(QColor(p.text2))
                painter.drawText(QRect(chip.left() + 21, chip.top(), chip_w - 21, chip.height()), int(Qt.AlignmentFlag.AlignVCenter), "Held")

    def _paint_retry_button(self, painter: QPainter, cell: QRect) -> None:
        p, m = self._p, metrics()
        rect = self.retry_button_rect(cell)
        painter.drawPixmap(rect.left() + 4, rect.center().y() - 6, icons.pixmap("refresh", 12, p.accent_text))
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(m.font_caption)
        painter.setFont(font)
        painter.setPen(QColor(p.accent_text))
        painter.drawText(QRect(rect.left() + 20, rect.top(), rect.width() - 20, rect.height()), int(Qt.AlignmentFlag.AlignVCenter), "Retry now")

    def _paint_actions(self, painter: QPainter, cell: QRect, item, row: int, active: bool) -> None:
        p = self._p
        actions = P.hover_actions(item) if active and self._wants_actions(row) else ()
        rects = self.action_buttons(cell, len(actions))
        for action, rect in zip(actions, rects):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(p.text, 0.09))
            painter.drawRoundedRect(rect, 5, 5)
            painter.drawPixmap(rect.left() + 4, rect.top() + 4, icons.pixmap(_ACTION_GLYPH[action], 16, p.icon))
        more = rects[-1]
        if active and self._wants_actions(row):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(p.text, 0.09))
            painter.drawRoundedRect(more, 5, 5)
        painter.drawPixmap(more.left() + 4, more.top() + 4, icons.pixmap("more", 16, p.icon if active else p.text2))

    # --- interaction ---------------------------------------------------------------------------

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.MouseButtonRelease or event.button() != Qt.MouseButton.LeftButton:
            return False
        item = index.data(ITEM_ROLE)
        if item is None:
            return False
        pos = event.position().toPoint() if isinstance(event, QMouseEvent) else event.pos()
        col = index.column()
        if col == P.COL_SELECT:
            return True  # the view toggles the row on mouse press (see DownloadTableView.mousePressEvent)
        if col == P.COL_SPEED and P.status_info(item).key == "retrying" and self.retry_button_rect(option.rect).contains(pos):
            self.action_requested.emit(item.queue_entry_id, P.RowAction.RETRY_NOW)
            return True
        if col == P.COL_ACTIONS:
            actions = P.hover_actions(item) if self._wants_actions(index.row()) else ()
            rects = self.action_buttons(option.rect, len(actions))
            for action, rect in zip(actions, rects):
                if rect.contains(pos):
                    self.action_requested.emit(item.queue_entry_id, action)
                    return True
            if rects[-1].contains(pos):
                self.more_requested.emit(item.queue_entry_id, event.globalPosition().toPoint())
                return True
        return False


class DownloadTableView(QTableView):
    details_requested = Signal(str)
    context_requested = Signal(QPoint)
    space_requested = Signal()

    def __init__(self, theme: str = "dark", parent=None) -> None:
        super().__init__(parent)
        self.delegate = DownloadItemDelegate(theme, self)
        self.setItemDelegate(self.delegate)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(metrics().task_row_height)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(24)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setFixedHeight(metrics().table_header_height)
        self.doubleClicked.connect(self._on_double_clicked)

    # column geometry: approved widths; Name absorbs the rest
    _WIDTHS = {P.COL_SELECT: 36, P.COL_CATEGORY: 90, P.COL_TYPE: 74, P.COL_SIZE: 80, P.COL_PROGRESS: 190, P.COL_STATUS: 168,
               P.COL_SPEED: 96, P.COL_ETA: 76, P.COL_ADDED: 104, P.COL_ACTIONS: 88}

    def apply_column_layout(self, layout: P.ColumnLayout) -> None:
        model = self.model()
        if model is None:
            return
        header = self.horizontalHeader()
        for column in range(model.columnCount()):
            self.setColumnHidden(column, column not in layout.visible)
            if column == P.COL_NAME:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                width = self._WIDTHS.get(column, 80)
                if column == P.COL_SPEED and layout.merge_speed_eta:
                    width = 132
                if column == P.COL_PROGRESS and layout.tier in ("compact", "rail"):
                    width = 150
                if column == P.COL_STATUS and layout.tier in ("compact", "rail"):
                    width = 150
                self.setColumnWidth(column, width)
        header.setMinimumSectionSize(24)

    def selected_ids(self) -> list[str]:
        sel = self.selectionModel()
        if sel is None:
            return []
        rows = sorted(sel.selectedRows(), key=lambda i: i.row())
        return [i.data(ID_ROLE) for i in rows if i.data(ID_ROLE) is not None]

    def refresh_hover_state(self) -> None:
        ids = self.selected_ids()
        self.delegate.set_single_selected_row(self.selectionModel().selectedRows()[0].row() if len(ids) == 1 else -1)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.column() == P.COL_SELECT:
                self.toggle_row_selection(index)  # the checkbox toggles this row without clearing the others
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else -1
        if row != self.delegate._hover_row:  # noqa: SLF001
            self.delegate.set_hover_row(row)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.delegate.set_hover_row(-1)
        self.viewport().update()
        super().leaveEvent(event)

    def viewportEvent(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid() and index.column() == P.COL_ACTIONS:
                item = index.data(ITEM_ROLE)
                actions = P.hover_actions(item) if item is not None and self.delegate._wants_actions(index.row()) else ()  # noqa: SLF001
                rect = self.visualRect(index)
                rects = self.delegate.action_buttons(rect, len(actions))
                from PySide6.QtWidgets import QToolTip

                for action, r in zip(actions, rects):
                    if r.contains(event.pos()):
                        QToolTip.showText(event.globalPos(), ACTION_TOOLTIP[action], self)
                        return True
                if rects[-1].contains(event.pos()):
                    QToolTip.showText(event.globalPos(), "More actions", self)
                    return True
        return super().viewportEvent(event)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if index.column() in (P.COL_SELECT, P.COL_ACTIONS):
            return
        entry_id = index.data(ID_ROLE)
        if entry_id:
            self.details_requested.emit(entry_id)

    def toggle_row_selection(self, index: QModelIndex) -> None:
        sel = self.selectionModel()
        if sel is None or not index.isValid():
            return
        from PySide6.QtCore import QItemSelectionModel

        sel.select(index, QItemSelectionModel.SelectionFlag.Toggle | QItemSelectionModel.SelectionFlag.Rows)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.pos())
        if index.isValid():
            sel = self.selectionModel()
            if not sel.isRowSelected(index.row(), QModelIndex()):
                from PySide6.QtCore import QItemSelectionModel

                sel.select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
            self.context_requested.emit(event.globalPos())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ids = self.selected_ids()
            if len(ids) == 1:
                self.details_requested.emit(ids[0])
                return
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_requested.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.clearSelection()
            return
        super().keyPressEvent(event)
