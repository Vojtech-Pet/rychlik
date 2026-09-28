"""Render text as a QR code image (pure segno matrix -> QImage; no Pillow)."""

from __future__ import annotations

import segno
from PySide6.QtGui import QColor, QImage, QPainter


def qr_image(text: str, *, scale: int = 6, border: int = 4) -> QImage:
    matrix = segno.make(text, error="m", micro=False).matrix
    size = len(matrix) + 2 * border
    image = QImage(size * scale, size * scale, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    black = QColor("black")
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                painter.fillRect((x + border) * scale, (y + border) * scale, scale, scale, black)
    painter.end()
    return image
