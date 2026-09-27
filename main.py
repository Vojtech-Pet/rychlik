"""Entry point: DownloadManagerService (A10) backing a functional
DownloadManagerWidget (A11, hardened in A12). Functional GUI integration
only -- no final visual design (see docs/FUNCTIONAL_GUI_INTEGRATION.md,
docs/FUNCTIONAL_GUI_HARDENING.md)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from rychlik.core.download_manager_service import DownloadManagerService, ManagerFaultedError
from rychlik.gui.download_manager_widget import DownloadManagerWidget


class MainWindow(QMainWindow):
    def __init__(self, manager: DownloadManagerService, widget: DownloadManagerWidget) -> None:
        super().__init__()
        self._manager = manager
        self._widget = widget
        self.setWindowTitle("Rýchlik")
        self.setCentralWidget(widget)
        self.resize(900, 480)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # §66-69: a truthful confirmation only when transfers are actually
        # active; cancelling leaves the service running untouched (§68).
        if not self._widget.confirm_close():
            event.ignore()
            return
        self._widget.prepare_shutdown()
        # §12/§13: GUI never manipulates clean-shutdown metadata itself --
        # manager.stop() alone owns the correct A8/A9 shutdown ordering.
        self._widget.shutdown()
        self._manager.stop()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Rýchlik")

    manager = DownloadManagerService()
    try:
        manager.start()
    except (ManagerFaultedError, Exception) as exc:  # noqa: BLE001
        QMessageBox.critical(None, "Rýchlik", f"Failed to start the download manager:\n{exc}")
        return 1

    widget = DownloadManagerWidget(manager)
    window = MainWindow(manager, widget)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
