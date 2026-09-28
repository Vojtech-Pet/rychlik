"""Production font selection for Rýchlik Desktop.

Policy (Final GUI/UX Implementation §15): Inter when it is legitimately
available on the machine; otherwise the approved fallbacks (Noto Sans, then a
system sans). No font files are downloaded or bundled, and nothing is fetched
at runtime. The chosen family is recorded so it can be reported and re-checked.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

from rychlik.gui.theme.tokens import font_family_stack, metrics

_SYSTEM_FALLBACKS = ("Segoe UI", "Cantarell", "DejaVu Sans")


def choose_family(available: set[str] | None = None) -> str:
    families = available if available is not None else set(QFontDatabase.families())
    for candidate in [*font_family_stack(), *_SYSTEM_FALLBACKS]:
        if candidate in families:
            return candidate
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def base_font(family: str | None = None) -> QFont:
    font = QFont(family or choose_family())
    font.setPixelSize(metrics().font_body)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font
