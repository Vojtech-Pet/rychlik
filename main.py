"""Entry point: DownloadManagerService (A10) behind the approved Rýchlik Desktop UI
(Final GUI/UX implementation). Composition root: it builds the service, the theme and
the window, and owns their lifetimes; widgets contain no backend construction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from rychlik.core.download_manager_service import DownloadManagerService, ManagerFaultedError
from rychlik.gui.device_mode import DeviceModeController
from rychlik.gui.dialogs.device_dialogs import DevicesPage, PairDeviceDialog, ShareSelectorDialog
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.gui.main_window import MainWindow
from rychlik.share.share_link_service import ShareLinkService
from rychlik.gui.theme.manager import ThemeManager


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Rýchlik")
    app.setOrganizationName("Rychlik")
    app.setDesktopFileName("rychlik-app")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "src" / "rychlik" / "resources" / "rychlik.svg")))
    themes = ThemeManager(app, QSettings("Rychlik", "Rychlik"))

    manager = DownloadManagerService()
    try:
        manager.start()
    except (ManagerFaultedError, Exception) as exc:  # noqa: BLE001
        QMessageBox.critical(None, "Rýchlik", f"Failed to start the download manager:\n{exc}")
        return 1

    link_service = ShareLinkService()
    devices = DeviceModeController()
    devices.start()  # discovery problems are reported by the controller, never fatal
    widget = DownloadManagerWidget(
        manager, theme=themes.theme, share_launcher=lambda artifact, parent: ShareSelectorDialog(artifact, devices, parent, link_service=link_service).exec()
    )
    devices_page = DevicesPage(devices)
    window = MainWindow(
        manager, widget, theme_manager=themes, devices_page=devices_page,
        device_actions={"pair": lambda: PairDeviceDialog(devices, window).exec()},
    )
    app.aboutToQuit.connect(devices.stop)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
