"""Test-only adapter: lets the A11/A12 behavior tests (written against the functional skeleton's
buttons and QTableWidget) exercise the approved UI through its REAL paths.

    old "button"       -> the matching QAction in widget.build_context_menu() (real menu logic:
                          visible only when the backend contract allows it, disabled at band edges)
    old url_input +
    download_button    -> the real AddDownloadDialog
    old QTableWidget   -> the real proxy/model (rows are in the same displayed order)

Production code has no knowledge of this module.
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QMenu

from rychlik.gui import presentation as P
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.gui.formatters import derive_status_text, format_downloaded_total, format_priority
from rychlik.gui.download_model import ID_ROLE, ITEM_ROLE


def _find_action(menu: QMenu | None, text: str):
    if menu is None:
        return None
    for action in menu.actions():
        label = action.text().strip().lstrip("✓").strip()
        if label == text:
            return action
        if action.menu() is not None:
            found = _find_action(action.menu(), text)
            if found is not None:
                return found
    return None


class _Cell:
    def __init__(self, text: str, entry_id: str) -> None:
        self._text, self._id = text, entry_id

    def text(self) -> str:
        return self._text

    def data(self, role: int):
        return self._id if role in (256, int(Qt.ItemDataRole.UserRole)) else None


class _Table:
    """Old QTableWidget surface, backed by the real view/model."""

    _MAP = {0: "name", 1: "status", 2: "progress", 3: "downloaded", 4: "speed", 5: "eta", 6: "priority", 7: "attempt"}

    def __init__(self, widget: DownloadManagerWidget) -> None:
        self._w = widget
        self.view = widget.table

    def rowCount(self) -> int:  # noqa: N802
        return self._w.proxy.rowCount()

    def _item(self, row: int):
        return self._w.proxy.index(row, 0).data(ITEM_ROLE)

    def live_count(self) -> int:
        """Rows for downloads that are still in the queue. The approved table also keeps finished
        downloads as history rows, so 'finished' is no longer 'row gone' (an intended change)."""
        return sum(1 for r in range(self.rowCount()) if P.is_live(self._item(r)))

    def item(self, row: int, col: int):
        item = self._item(row)
        if item is None:
            return None
        key = self._MAP[col]
        if key == "name":
            text = item.display_name or "(unknown)"
        elif key == "status":
            # legacy wording; the approved labels (and the Held chip) are covered by test_gui_presentation
            text = derive_status_text(item.task_state, item.queue_state)
        elif key == "progress":
            text = self._w.proxy.index(row, P.COL_PROGRESS).data()
        elif key == "downloaded":
            text = format_downloaded_total(item.bytes_downloaded, item.total_bytes)
        elif key == "speed":
            text = self._w.proxy.index(row, P.COL_SPEED).data()
        elif key == "eta":
            text = self._w.proxy.index(row, P.COL_ETA).data()
        elif key == "priority":
            text = format_priority(item.priority)
        else:
            text = str(item.attempt_count)
        return _Cell(text, item.queue_entry_id)

    def selectRow(self, row: int) -> None:  # noqa: N802
        self.view.selectionModel().select(
            self._w.proxy.index(row, 0), QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        )

    def selectionModel(self):  # noqa: N802
        return self.view.selectionModel()

    def clearSelection(self) -> None:  # noqa: N802
        self.view.clearSelection()


class _Action:
    """A former push-button: enabled == the real QAction exists and is enabled; click() triggers it."""

    def __init__(self, widget, text: str, *, gate_on_widget: bool = False) -> None:
        self._w, self._text = widget, text

    def _action(self):
        return _find_action(self._w.build_context_menu(), self._text)

    def isEnabled(self) -> bool:  # noqa: N802
        act = self._action()
        return act is not None and act.isEnabled()

    def click(self) -> None:
        act = self._action()
        if act is not None and act.isEnabled():
            act.trigger()


class _StandaloneButton:
    def __init__(self, get_enabled, on_click) -> None:
        self._get, self._click = get_enabled, on_click

    def isEnabled(self) -> bool:  # noqa: N802
        return self._get()

    def click(self) -> None:
        self._click()


class _PriorityCombo:
    _LABELS = ("High", "Normal", "Low")

    def __init__(self, widget) -> None:
        self._w = widget

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        act = _find_action(self._w.build_context_menu(), self._LABELS[index])
        if act is not None:
            act.trigger()

    def isEnabled(self) -> bool:  # noqa: N802
        return _find_action(self._w.build_context_menu(), "Normal") is not None


class _ReturnPressed:
    def __init__(self, on_emit) -> None:
        self._on_emit = on_emit

    def emit(self) -> None:
        self._on_emit()


class _Url:
    def __init__(self) -> None:
        self.text_value = ""
        self.returnPressed = _ReturnPressed(lambda: None)

    def setText(self, text: str) -> None:  # noqa: N802
        self.text_value = text

    def text(self) -> str:
        return self.text_value


def make_widget(manager, **kwargs) -> DownloadManagerWidget:
    widget = DownloadManagerWidget(manager, **kwargs)
    return adapt(widget)


def adapt(widget: DownloadManagerWidget) -> DownloadManagerWidget:
    from rychlik.gui.dialogs import AddDownloadDialog

    widget._table_stack = widget._stack
    widget.empty_state_label = widget.empty_title
    widget.table_legacy = _Table(widget)
    widget.url_input = _Url()
    dialog_holder: dict = {}

    def _submit() -> None:
        dialog = AddDownloadDialog(widget)
        dialog.url_input.setText(widget.url_input.text_value)
        dialog.download_button.click()
        dialog_holder["last"] = dialog
        if widget.url_input.text_value.strip() and not dialog.result():
            pass
        else:
            widget.url_input.text_value = ""

    widget.url_input.returnPressed = _ReturnPressed(_submit)
    widget.download_button = _StandaloneButton(lambda: widget.empty_add_button.isEnabled(), _submit)
    widget.browse_button = _StandaloneButton(lambda: True, lambda: widget.choose_destination())
    widget.destination_display = type("D", (), {"text": lambda self: str(widget.destination_dir)})()
    widget.hold_button = _Action(widget, "Hold")
    widget.release_button = _Action(widget, "Release")
    widget.pause_button = _Action(widget, "Pause")
    widget.resume_button = _Action(widget, "Resume")
    widget.retry_button = _Action(widget, "Retry now")
    widget.cancel_button = _Action(widget, "Cancel")
    widget.up_button = _Action(widget, "Move up")
    widget.down_button = _Action(widget, "Move down")
    widget.priority_combo = _PriorityCombo(widget)
    widget.open_folder_button = _Action(widget, "Open folder")
    widget.share_button = _Action(widget, "Share…")
    return widget
