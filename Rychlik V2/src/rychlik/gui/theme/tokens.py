"""Approved final design tokens for Rýchlik Desktop (Prompt: Final GUI/UX Implementation).

The single source is design/final_design_tokens.json; a byte-identical copy is
packaged next to this module (tests/test_gui_theme.py fails if they drift), so
the running application never depends on the design/ folder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_TOKENS_PATH = Path(__file__).with_name("final_design_tokens.json")

THEME_DARK = "dark"
THEME_LIGHT = "light"
THEMES = (THEME_DARK, THEME_LIGHT)


@dataclass(frozen=True)
class Palette:
    """Resolved colors for one theme. Values are '#RRGGBB' or an rgba()/CSS color string."""

    name: str
    background: str
    surface: str
    surface2: str
    elevated: str
    text: str
    text2: str
    text_disabled: str
    on_accent: str
    border: str
    border_strong: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_tint: str  # rgba() with the token's tint opacity
    info: str
    info_text: str
    success: str
    success_text: str
    warning: str
    warning_text: str
    error: str
    error_text: str
    error_solid: str
    neutral: str
    icon: str
    focus: str
    scrim: str

    def status_color(self, kind: str) -> str:
        """Fill color (bars/glyphs) for a semantic status color name."""
        return {
            "info": self.info, "success": self.success, "warning": self.warning,
            "error": self.error, "neutral": self.neutral,
        }[kind]

    def status_text_color(self, kind: str) -> str:
        """Text-safe (AA) color for a semantic status color name."""
        return {
            "info": self.info_text, "success": self.success_text, "warning": self.warning_text,
            "error": self.error_text, "neutral": self.text2,
        }[kind]


@dataclass(frozen=True)
class DesktopMetrics:
    sidebar_width: int
    menu_height: int
    toolbar_height: int
    statusbar_height: int
    task_row_height: int
    table_header_height: int
    control_height: int
    progress_height: int
    icon_sidebar: int
    icon_toolbar: int
    icon_row: int
    icon_menu: int
    icon_dialog: int
    min_window_width: int
    min_window_height: int
    radius_small: int
    radius_medium: int
    radius_large: int
    font_caption: int
    font_body: int
    font_table: int
    font_heading: int
    font_dialog_title: int


@lru_cache(maxsize=1)
def load_raw_tokens() -> dict:
    return json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))


def _rgba(hex_color: str, opacity: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{opacity:.2f})"


@lru_cache(maxsize=None)
def palette(theme: str) -> Palette:
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}")
    c = load_raw_tokens()["color"][theme]
    s, t, st, ac, bd = c["surface"], c["text"], c["status"], c["accent"], c["border"]
    return Palette(
        name=theme,
        background=s["background"], surface=s["primary"], surface2=s["secondary"], elevated=s["elevated"],
        text=t["primary"], text2=t["secondary"], text_disabled=t["disabled"], on_accent=t["onAccent"],
        border=bd["default"], border_strong=bd["strong"],
        accent=ac["primary"], accent_hover=ac["hover"], accent_pressed=ac["pressed"], accent_text=ac["text"],
        accent_tint=_rgba(ac["primary"], ac["tintOpacity"]),
        info=st["info"], info_text=st["infoText"], success=st["success"], success_text=st["successText"],
        warning=st["warning"], warning_text=st["warningText"], error=st["error"], error_text=st["errorText"],
        error_solid=st["errorSolid"], neutral=st["neutral"],
        icon=c["icon"]["neutral"], focus=c["focus"], scrim=c["scrim"],
    )


@lru_cache(maxsize=1)
def metrics() -> DesktopMetrics:
    raw = load_raw_tokens()
    d = raw["size"]["desktop"]
    ty = raw["typography"]["desktop"]
    r = raw["radius"]["desktop"]
    return DesktopMetrics(
        sidebar_width=d["sidebarWidth"], menu_height=d["menuHeight"], toolbar_height=d["toolbarHeight"],
        statusbar_height=d["statusbarHeight"], task_row_height=d["taskRowHeight"],
        table_header_height=d["tableHeaderHeight"], control_height=d["controlHeight"],
        progress_height=d["progressHeight"],
        icon_sidebar=d["icon"]["sidebar"], icon_toolbar=d["icon"]["toolbar"], icon_row=d["icon"]["row"],
        icon_menu=d["icon"]["menu"], icon_dialog=d["icon"]["dialog"],
        min_window_width=d["minWindow"]["width"], min_window_height=d["minWindow"]["height"],
        radius_small=r["small"], radius_medium=r["medium"], radius_large=r["large"],
        font_caption=ty["caption"]["size"], font_body=ty["body"]["size"], font_table=ty["table"]["size"],
        font_heading=ty["heading"]["size"], font_dialog_title=ty["dialogTitle"]["size"],
    )


def font_family_stack() -> list[str]:
    """Approved desktop family order: Inter first, then the approved fallbacks."""
    stack = load_raw_tokens()["typography"]["family"]["desktop"]
    return [part.strip().strip("'\"") for part in stack.split(",") if part.strip() not in ("system-ui", "sans-serif")]
