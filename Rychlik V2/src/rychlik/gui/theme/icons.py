"""Central icon registry: every icon is a named vector from the approved icon set.

Widgets ask for `icon("pause", 16, color)`; nothing else in the GUI knows how or
where icon geometry is stored (no filesystem paths in widgets). Icons are
rendered from SVG at the requested logical size and the screen's device pixel
ratio, so they are never upscaled rasters.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from rychlik.gui.theme.icon_paths import ICON_PATHS

_STROKE_WIDTH = 1.75


def icon_names() -> tuple[str, ...]:
    return tuple(ICON_PATHS)


def svg_markup(name: str, color: str, stroke: float = _STROKE_WIDTH) -> str:
    if name not in ICON_PATHS:
        raise KeyError(f"unknown icon {name!r}")
    body = ICON_PATHS[name].replace("currentColor", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


_pixmap_cache: dict[tuple[str, int, str, float, float], QPixmap] = {}


def pixmap(name: str, size: int, color: str, *, stroke: float = _STROKE_WIDTH, dpr: float | None = None) -> QPixmap:
    if dpr is None:
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
    key = (name, size, color, stroke, dpr)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached
    px = max(1, round(size * dpr))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg_markup(name, color, stroke).encode("utf-8")))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    _pixmap_cache[key] = result
    return result


def icon(name: str, size: int, color: str, *, disabled_color: str | None = None, stroke: float = _STROKE_WIDTH) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, size, color, stroke=stroke), QIcon.Mode.Normal)
    if disabled_color is not None:
        result.addPixmap(pixmap(name, size, disabled_color, stroke=stroke), QIcon.Mode.Disabled)
    return result


def clear_cache() -> None:
    _pixmap_cache.clear()
