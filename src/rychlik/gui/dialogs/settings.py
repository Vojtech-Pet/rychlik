"""Settings: only what exists. Presentation theme is the one setting this application has."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from rychlik.gui.theme.manager import PREFERENCE_DARK, PREFERENCE_LIGHT, PREFERENCE_SYSTEM, ThemeManager


class SettingsDialog(QDialog):
    def __init__(self, theme_manager: ThemeManager | None, parent=None) -> None:
        super().__init__(parent)
        self._themes = theme_manager
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
        layout.addSpacing(14)
        close = QHBoxLayout()
        close.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.setProperty("variant", "primary")
        self.close_button.clicked.connect(self.accept)
        close.addWidget(self.close_button)
        layout.addLayout(close)
        self._sync()

    def _choose(self, preference: str) -> None:
        if self._themes is not None:
            self._themes.set_preference(preference)
        self._sync()

    def _sync(self) -> None:
        current = self._themes.preference if self._themes is not None else PREFERENCE_DARK
        for key, button in self.buttons.items():
            button.setChecked(key == current)
            button.setEnabled(self._themes is not None)
