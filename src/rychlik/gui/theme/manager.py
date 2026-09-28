"""Applies the approved theme to a QApplication and tells widgets when it changes."""

from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from rychlik.gui.theme import icons
from rychlik.gui.theme.fonts import base_font, choose_family
from rychlik.gui.theme.stylesheet import build_stylesheet
from rychlik.gui.theme.tokens import THEME_DARK, THEME_LIGHT, THEMES, Palette, palette

PREFERENCE_DARK = "dark"
PREFERENCE_LIGHT = "light"
PREFERENCE_SYSTEM = "system"
PREFERENCES = (PREFERENCE_DARK, PREFERENCE_LIGHT, PREFERENCE_SYSTEM)

_SETTINGS_KEY = "appearance/theme"


def system_theme() -> str:
    """Dark unless the platform reports a light color scheme."""
    hints = QGuiApplication.styleHints()
    try:
        from PySide6.QtCore import Qt

        scheme = hints.colorScheme()
        return THEME_LIGHT if scheme == Qt.ColorScheme.Light else THEME_DARK
    except Exception:  # noqa: BLE001 - older Qt without colorScheme()
        return THEME_DARK


class ThemeManager(QObject):
    """One per application. Preference is stored with QSettings (the only theme
    preference store; the desktop app had none before)."""

    theme_changed = Signal(str)

    def __init__(self, app: QApplication, settings: QSettings | None = None) -> None:
        super().__init__(app)
        self._app = app
        self._settings = settings
        self._font_family = choose_family()
        self._preference = self._load_preference()
        self._theme = self._resolve(self._preference)
        self.apply()

    @property
    def font_family(self) -> str:
        return self._font_family

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def preference(self) -> str:
        return self._preference

    @property
    def palette(self) -> Palette:
        return palette(self._theme)

    def _load_preference(self) -> str:
        if self._settings is None:
            return PREFERENCE_DARK
        value = str(self._settings.value(_SETTINGS_KEY, PREFERENCE_DARK))
        return value if value in PREFERENCES else PREFERENCE_DARK

    @staticmethod
    def _resolve(preference: str) -> str:
        return system_theme() if preference == PREFERENCE_SYSTEM else preference

    def set_preference(self, preference: str) -> None:
        if preference not in PREFERENCES:
            raise ValueError(f"unknown theme preference {preference!r}")
        self._preference = preference
        if self._settings is not None:
            self._settings.setValue(_SETTINGS_KEY, preference)
        self.set_theme(self._resolve(preference))

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            raise ValueError(f"unknown theme {theme!r}")
        changed = theme != self._theme
        self._theme = theme
        self.apply()
        if changed:
            self.theme_changed.emit(theme)

    def apply(self) -> None:
        p = palette(self._theme)
        self._app.setFont(base_font(self._font_family))
        qp = QPalette()
        from PySide6.QtGui import QColor

        qp.setColor(QPalette.ColorRole.Window, QColor(p.background))
        qp.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
        qp.setColor(QPalette.ColorRole.Base, QColor(p.surface))
        qp.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface2))
        qp.setColor(QPalette.ColorRole.Text, QColor(p.text))
        qp.setColor(QPalette.ColorRole.Button, QColor(p.surface2))
        qp.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
        qp.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
        qp.setColor(QPalette.ColorRole.HighlightedText, QColor(p.on_accent))
        qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.text))
        qp.setColor(QPalette.ColorRole.ToolTipText, QColor(p.background))
        qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.text_disabled))
        self._app.setPalette(qp)
        self._app.setStyleSheet(build_stylesheet(self._theme))
        icons.clear_cache()
