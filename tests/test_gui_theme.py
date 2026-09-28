"""Final GUI/UX implementation, stage I1: the desktop design system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton

from rychlik.gui.theme import icons
from rychlik.gui.theme.fonts import choose_family
from rychlik.gui.theme.icon_paths import ICON_PATHS
from rychlik.gui.theme.manager import PREFERENCE_LIGHT, PREFERENCE_SYSTEM, ThemeManager
from rychlik.gui.theme.stylesheet import build_stylesheet
from rychlik.gui.theme.tokens import THEME_DARK, THEME_LIGHT, THEMES, load_raw_tokens, metrics, palette

_DESIGN_TOKENS = Path(__file__).resolve().parents[1] / "design" / "final_design_tokens.json"
_PACKAGED_TOKENS = Path(__file__).resolve().parents[1] / "src" / "rychlik" / "gui" / "theme" / "final_design_tokens.json"


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_packaged_tokens_match_the_approved_design_source():
    if not _DESIGN_TOKENS.exists():
        pytest.skip("design/ folder not present")
    assert json.loads(_PACKAGED_TOKENS.read_text()) == json.loads(_DESIGN_TOKENS.read_text())


def test_approved_desktop_dimensions_are_loaded_from_tokens():
    m = metrics()
    assert (m.sidebar_width, m.task_row_height, m.toolbar_height, m.menu_height, m.statusbar_height) == (190, 42, 54, 28, 28)
    assert (m.icon_sidebar, m.icon_toolbar, m.icon_row) == (16, 18, 16)
    assert 5 <= m.progress_height <= 8
    assert (m.min_window_width, m.min_window_height) == (1100, 640)


@pytest.mark.parametrize("theme", THEMES)
def test_text_pairs_meet_wcag_aa_in_both_themes(theme):
    p = palette(theme)
    for fg, bg in [
        (p.text, p.background), (p.text, p.surface), (p.text, p.elevated), (p.text2, p.surface), (p.text2, p.elevated),
        (p.on_accent, p.accent), (p.on_accent, p.accent_hover), (p.on_accent, p.accent_pressed), ("#FFFFFF", p.error_solid),
        (p.info_text, p.surface), (p.success_text, p.surface), (p.warning_text, p.surface), (p.error_text, p.surface),
    ]:
        assert _ratio(fg, bg) >= 4.5, (theme, fg, bg)


def test_dark_and_light_are_distinct_palettes():
    assert palette(THEME_DARK).background != palette(THEME_LIGHT).background
    assert palette(THEME_DARK).accent != palette(THEME_LIGHT).accent


def _qss_problems(qss: str) -> list[str]:
    """Structural QSS check (Qt does not reliably report parse errors): balanced blocks and
    well-formed 'property: value' declarations only."""
    import re

    problems: list[str] = []
    body = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)
    consumed = 0
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        consumed += len(match.group(0))
        for declaration in filter(None, (d.strip() for d in match.group(2).split(";"))):
            if not re.fullmatch(r"[a-z][a-z-]*\s*:\s*[^:;]+(:[^;]*)?", declaration, flags=re.I):
                problems.append(f"bad declaration {declaration!r} in {match.group(1).strip()!r}")
    if len(re.sub(r"\s", "", body)) != len(re.sub(r"\s", "", "".join(m.group(0) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body)))):
        problems.append("unbalanced or stray braces")
    return problems


def test_qss_checker_detects_broken_stylesheets():
    assert _qss_problems("QPushButton { color: red; bogus @@@ }")
    assert _qss_problems("QPushButton { color: red; ")
    assert not _qss_problems("QPushButton { color: red; border: 1px solid #fff; }")


@pytest.mark.parametrize("theme", THEMES)
def test_stylesheet_is_structurally_valid_and_applies(qapp, theme):
    qss = build_stylesheet(theme)
    assert not _qss_problems(qss)
    probe = QPushButton("x")
    probe.setStyleSheet(qss)
    probe.ensurePolished()  # must not raise


def test_stylesheet_uses_tokens_not_literals(qapp):
    qss = build_stylesheet(THEME_DARK)
    assert palette(THEME_DARK).accent in qss and palette(THEME_DARK).surface in qss
    assert "{" in qss and "None" not in qss


def test_every_icon_renders_at_approved_sizes_in_both_themes(qapp):
    assert len(ICON_PATHS) >= 50 and set(icons.icon_names()) == set(ICON_PATHS)
    for theme in THEMES:
        p = palette(theme)
        for name in icons.icon_names():
            for size in (14, 16, 18):
                px = icons.pixmap(name, size, p.icon, dpr=1.0)
                assert not px.isNull() and px.width() == size, (name, size)
                image = px.toImage()
                assert any(image.pixelColor(x, y).alpha() > 0 for x in range(size) for y in range(size)), f"{name} drew nothing"


def test_icon_registry_rejects_unknown_names_and_has_no_path_references(qapp):
    with pytest.raises(KeyError):
        icons.svg_markup("does-not-exist", "#fff")
    assert all("/" not in name and "." not in name for name in ICON_PATHS)


def test_font_policy_prefers_inter_then_noto_sans_then_system():
    assert choose_family({"Inter", "Noto Sans"}) == "Inter"
    assert choose_family({"Noto Sans", "DejaVu Sans"}) == "Noto Sans"
    assert choose_family({"DejaVu Sans"}) == "DejaVu Sans"
    stack = load_raw_tokens()["typography"]["family"]["desktop"]
    assert stack.startswith("Inter")


def test_theme_manager_applies_persists_and_notifies(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "r.ini"), QSettings.Format.IniFormat)
    events: list[str] = []
    manager = ThemeManager(QApplication.instance(), settings)
    manager.theme_changed.connect(events.append)
    assert manager.theme == THEME_DARK and manager.font_family
    manager.set_preference(PREFERENCE_LIGHT)
    assert manager.theme == THEME_LIGHT and events == [THEME_LIGHT]
    assert QApplication.instance().styleSheet().count(palette(THEME_LIGHT).accent) > 0
    assert ThemeManager(QApplication.instance(), settings).preference == PREFERENCE_LIGHT  # persisted
    manager.set_preference(PREFERENCE_SYSTEM)
    assert manager.theme in THEMES
    with pytest.raises(ValueError):
        manager.set_preference("neon")
    manager.set_preference("dark")
