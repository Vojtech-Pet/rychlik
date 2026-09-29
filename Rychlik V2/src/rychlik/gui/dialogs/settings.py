"""Settings: only what exists. Presentation theme is the one setting this application has."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout

from rychlik.gui.theme.manager import PREFERENCE_DARK, PREFERENCE_LIGHT, PREFERENCE_SYSTEM, ThemeManager


class SettingsDialog(QDialog):
    def __init__(self, theme_manager: ThemeManager | None, parent=None, *, module_registry=None, browser_bridge=None, bridge_tokens=None) -> None:
        super().__init__(parent)
        self._themes = theme_manager
        self._module_registry = module_registry
        self._bridge = browser_bridge
        self._bridge_tokens = bridge_tokens
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Appearance")
        title.setProperty("role", "dialogTitle")
        layout.addWidget(title)
        row = QHBoxLayout()
        label = QLabel("Theme")
        row.addWidget(label)
        row.addStretch(1)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in ((PREFERENCE_LIGHT, "Light"), (PREFERENCE_DARK, "Dark"), (PREFERENCE_SYSTEM, "System")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAccessibleName(f"{text} theme")
            self.buttons[key] = button
            row.addWidget(button)
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
        layout.addSpacing(10)
        layout.addLayout(row)
        note = QLabel("Interface size follows the system display scaling.")
        note.setProperty("role", "caption")
        layout.addSpacing(6)
        layout.addWidget(note)
        self.modules_button = None
        if module_registry is not None:
            layout.addSpacing(14)
            modules_title = QLabel("Modules")
            modules_title.setProperty("role", "dialogTitle")
            layout.addWidget(modules_title)
            modules_row = QHBoxLayout()
            modules_row.addWidget(QLabel("Add or remove website modules"))
            modules_row.addStretch(1)
            self.modules_button = QPushButton("Manage modules…")
            self.modules_button.clicked.connect(self.open_modules)
            modules_row.addWidget(self.modules_button)
            layout.addLayout(modules_row)
        self.token_box = None
        if bridge_tokens is not None:
            layout.addSpacing(14)
            ext_title = QLabel("Browser extension")
            ext_title.setProperty("role", "dialogTitle")
            layout.addWidget(ext_title)
            listening = browser_bridge is not None and browser_bridge.running
            self.bridge_status = QLabel(
                f"Listening on 127.0.0.1:{browser_bridge.port}" if listening else "Not available: the port is in use by another program."
            )
            self.bridge_status.setProperty("role", "caption")
            layout.addWidget(self.bridge_status)
            note = QLabel("Paste this token into the Rýchlik browser extension once. Only the extension can send links to the app.")
            note.setWordWrap(True)
            note.setProperty("role", "caption")
            layout.addWidget(note)
            token_row = QHBoxLayout()
            self.token_box = QLineEdit(bridge_tokens.get())
            self.token_box.setReadOnly(True)
            self.token_box.setEchoMode(QLineEdit.EchoMode.Password)
            self.token_box.setAccessibleName("Browser extension token")
            self.copy_token_button = QPushButton("Copy token")
            self.new_token_button = QPushButton("New token…")
            token_row.addWidget(self.token_box, 1)
            token_row.addWidget(self.copy_token_button)
            token_row.addWidget(self.new_token_button)
            layout.addLayout(token_row)
            self.copy_token_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.token_box.text()))
            self.new_token_button.clicked.connect(self.new_token)
        layout.addSpacing(14)
        close = QHBoxLayout()
        close.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.setProperty("variant", "primary")
        self.close_button.clicked.connect(self.accept)
        close.addWidget(self.close_button)
        layout.addLayout(close)
        self._sync()

    def new_token(self) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, "New token?", "The extension stops working until you paste the new token into it.", QMessageBox.StandardButton.NoButton, self)
        go = box.addButton("Create new token", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is go and self._bridge_tokens is not None and self.token_box is not None:
            self.token_box.setText(self._bridge_tokens.regenerate())

    def open_modules(self) -> None:
        from rychlik.gui.dialogs.modules import ModulesDialog

        ModulesDialog(self._module_registry, self).exec()

    def _choose(self, preference: str) -> None:
        if self._themes is not None:
            self._themes.set_preference(preference)
        self._sync()

    def _sync(self) -> None:
        current = self._themes.preference if self._themes is not None else PREFERENCE_DARK
        for key, button in self.buttons.items():
            button.setChecked(key == current)
            button.setEnabled(self._themes is not None)
