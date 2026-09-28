"""Entry point: DownloadManagerService (A10) behind the approved Rýchlik Desktop UI
(Final GUI/UX implementation). Composition root: it builds the service, the theme and
the window, and owns their lifetimes; widgets contain no backend construction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from rychlik.core.download_manager_service import DownloadManagerService, ManagerFaultedError
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.gui.main_window import MainWindow
from rychlik.gui.theme.manager import ThemeManager


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Rýchlik")
    app.setOrganizationName("Rychlik")
    themes = ThemeManager(app, QSettings("Rychlik", "Rychlik"))

    manager = DownloadManagerService()
    try:
        manager.start()
    except (ManagerFaultedError, Exception) as exc:  # noqa: BLE001
        QMessageBox.critical(None, "Rýchlik", f"Failed to start the download manager:\n{exc}")
        return 1

    widget = DownloadManagerWidget(manager, theme=themes.theme)
    window = MainWindow(manager, widget, theme_manager=themes)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
