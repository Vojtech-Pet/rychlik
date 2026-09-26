"""Minimal entry point: opens the existing DownloadWidget skeleton in a
plain QMainWindow. Functional skeleton only -- no final visual design,
no menu/toolbar/sidebar (that starts only after Prompt 32's design gate)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import QApplication, QMainWindow

from rychlik.gui.download_widget import DownloadWidget


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Rýchlik")

    window = QMainWindow()
    window.setWindowTitle("Rýchlik")
    window.setCentralWidget(DownloadWidget())
    window.resize(480, 200)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
