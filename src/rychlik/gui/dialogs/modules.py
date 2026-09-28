"""Settings › Modules: add, enable, disable and remove site modules. A thin layer over ModuleRegistry."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from rychlik.modules.catalog import describe
from rychlik.modules.registry import (
    COMPAT_NEEDS_PROCESS_BRIDGE, LoadStatus, ModuleError, ModuleInfo, ModuleRegistry, inspect_module_source,
)

TRUST_WARNING = (
    "Local modules are Python programs and run with your user privileges.\n"
    "Install only modules you trust. Rýchlik does not sandbox them."
)


def display_status(info: ModuleInfo) -> str:
    if info.load_status == LoadStatus.CHANGED:
        return "Changed"
    if info.load_status == LoadStatus.MISSING:
        return "Missing"
    if info.load_status == LoadStatus.ERROR:
        return "Error"
    if info.compatibility == COMPAT_NEEDS_PROCESS_BRIDGE:
        return "Unsupported"
    return "Enabled" if info.enabled else "Disabled"


def status_explanation(info: ModuleInfo) -> str:
    status = display_status(info)
    if status == "Changed":
        return "The module file changed after installation.\nFor safety it will not be loaded until it is removed and added again."
    if status == "Missing":
        return "The module file is missing. Remove the module and add it again."
    if status == "Error":
        return f"The module could not run:\n{info.last_error}"
    if status == "Unsupported":
        return "This module starts its own download process, which Rýchlik cannot run safely yet (process bridge required)."
    return ""


def _default_file_chooser(parent) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, "Add module", str(Path.home()), "Python modules (*.py)")
    return path


def _default_confirm(parent, title: str, text: str, action: str) -> bool:
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.NoButton, parent)
    go = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is go


def _default_notice(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


class ModulesDialog(QDialog):
    def __init__(
        self,
        registry: ModuleRegistry,
        parent=None,
        *,
        file_chooser: Callable[[QDialog], str] | None = None,
        confirm: Callable[[QDialog, str, str, str], bool] | None = None,
        notice: Callable[[QDialog, str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._file_chooser = file_chooser or _default_file_chooser
        self._confirm = confirm or _default_confirm
        self._notice = notice or _default_notice
        self.setWindowTitle("Modules")
        self.setModal(True)
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Modules")
        title.setProperty("role", "dialogTitle")
        layout.addWidget(title)
        intro = QLabel("Modules teach Rýchlik how to download from specific websites.")
        intro.setProperty("role", "caption")
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.setAccessibleName("Installed modules")
        layout.addWidget(self.list, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setProperty("role", "caption")
        layout.addWidget(self.detail)
        self.empty = QLabel("No modules yet. Add a module file to support more websites.")
        self.empty.setProperty("role", "caption")
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add module…")
        self.add_button.setProperty("variant", "primary")
        self.enable_button = QPushButton("Enable")
        self.disable_button = QPushButton("Disable")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setProperty("variant", "dangerText")
        self.close_button = QPushButton("Close")
        for button in (self.add_button, self.enable_button, self.disable_button, self.remove_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.add_button.clicked.connect(self.add_module)
        self.enable_button.clicked.connect(lambda: self._set_enabled(True))
        self.disable_button.clicked.connect(lambda: self._set_enabled(False))
        self.remove_button.clicked.connect(self.remove_module)
        self.close_button.clicked.connect(self.accept)
        self.list.currentRowChanged.connect(lambda _r: self._sync())
        self.refresh()

    # --- view ------------------------------------------------------------------------------------------------------

    def selected(self) -> ModuleInfo | None:
        item = self.list.currentItem()
        return self._registry.get(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def refresh(self) -> None:
        keep = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        try:
            infos = self._registry.list()
        except ModuleError as exc:
            infos = []
            self.detail.setText(str(exc))
        self.list.clear()
        for info in infos:
            description = describe(info.sha256)
            name = description.name if description else info.module_id
            item = QListWidgetItem(f"{name}    ·    {display_status(info)}")
            item.setData(Qt.ItemDataRole.UserRole, info.module_id)
            self.list.addItem(item)
            if info.module_id == keep:
                self.list.setCurrentItem(item)
        self.empty.setVisible(not infos)
        self._sync()

    def _sync(self) -> None:
        info = self.selected()
        text = ""
        if info is not None:
            description = describe(info.sha256)
            if description is not None and info.load_status not in (LoadStatus.CHANGED, LoadStatus.MISSING):
                text = "Handles: " + ", ".join(description.handles)
            explanation = status_explanation(info)
            text = (text + "\n" + explanation).strip()
        if text or info is not None:
            self.detail.setText(text)
        status = display_status(info) if info is not None else ""
        self.enable_button.setEnabled(info is not None and status == "Disabled")
        self.disable_button.setEnabled(info is not None and status in ("Enabled", "Error", "Unsupported") and info.enabled)
        self.remove_button.setEnabled(info is not None)

    # --- actions ----------------------------------------------------------------------------------------------------

    def add_module(self) -> None:
        chosen = self._file_chooser(self)
        if not chosen:
            return
        path = Path(chosen)
        inspection = inspect_module_source(path)
        if not inspection.ok:
            self._notice(self, "Can’t add this module", inspection.problem)
            return
        note = ""
        if inspection.compatibility == COMPAT_NEEDS_PROCESS_BRIDGE:
            note = "\n\nThis module starts its own download process. It will be added but not used until Rýchlik supports that."
        if not self._confirm(self, f"Add “{path.name}”?", TRUST_WARNING + note, "Add module"):
            return
        try:
            self._registry.add(path, trust_confirmed=True)
        except ModuleError as exc:
            self._notice(self, "Can’t add this module", str(exc))
            return
        self.refresh()

    def _set_enabled(self, enabled: bool) -> None:
        info = self.selected()
        if info is None:
            return
        try:
            self._registry.set_enabled(info.module_id, enabled)
        except ModuleError as exc:
            self._notice(self, "Modules", str(exc))
        self.refresh()

    def remove_module(self) -> None:
        info = self.selected()
        if info is None:
            return
        description = describe(info.sha256)
        name = description.name if description else info.module_id
        if not self._confirm(self, f"Remove {name}?", "The module and its stored copy are deleted. Downloads already added keep working.", "Remove"):
            return
        try:
            self._registry.remove(info.module_id)
        except ModuleError as exc:
            self._notice(self, "Modules", str(exc))
        self.refresh()
