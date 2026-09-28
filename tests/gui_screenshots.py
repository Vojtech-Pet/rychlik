"""Desktop screenshot harness for the visual acceptance review (offscreen Qt, real widgets, fixture data).

    QT_QPA_PLATFORM=offscreen python tests/gui_screenshots.py artifacts/gui_implementation_review/desktop

The rows come from a fake DownloadManagerService (fixture data), everything else is the real UI code.
Also imported by tests/test_gui_screenshots.py, which renders into a temp dir and checks that no image is blank.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from rychlik.core.artifact import Artifact
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.device.contracts import DeviceHandoffSnapshot, FriendSendEndpoint, HandoffErrorCode, HandoffState
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.trust_store import FriendSendTrustStore, TrustedFriendSendDevice
from rychlik.gui.device_mode import DeviceModeController
from rychlik.gui.dialogs import AddDownloadDialog, DetailsDialog, SettingsDialog
from rychlik.gui.dialogs.device_dialogs import DevicesPage, PairDeviceDialog, SendToDeviceDialog, ShareByLinkDialog, ShareSelectorDialog
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.gui.main_window import MainWindow
from rychlik.gui.theme.manager import ThemeManager

T, Q, PR = DownloadTaskState, QueueEntryState, QueuePriority
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _it(eid, name, task, queue=Q.QUEUED, prio=PR.NORMAL, host="cdn.example.com", **kw):
    base = dict(task_id="t" + eid, queue_entry_id=eid, display_name=name, task_state=task, queue_state=queue, priority=prio, position=0,
                attempt_count=1, bytes_downloaded=0, total_bytes=None, progress_fraction=None, speed_bps=None, eta_seconds=None,
                source_host=host, added_at=NOW - timedelta(minutes=int(eid[1:]) * 7))
    base.update(kw)
    return DownloadViewSnapshot(**base)


def fixture_items():
    mb = 1024 * 1024
    return [
        _it("q1", "holiday_2026_full_length.mp4", T.TRANSFERRING, bytes_downloaded=146 * mb, total_bytes=198 * mb, progress_fraction=0.74, speed_bps=12.8 * mb, eta_seconds=4, prio=PR.HIGH),
        _it("q2", "conference_keynote_4k.mkv", T.TRANSFERRING, bytes_downloaded=612 * mb, total_bytes=2100 * mb, progress_fraction=0.29, speed_bps=8.1 * mb, eta_seconds=183),
        _it("q3", "podcast_episode_112.mp3", T.PAUSED, bytes_downloaded=21 * mb, total_bytes=64 * mb, progress_fraction=0.33),
        _it("q4", "archive_backup_2025.zip", T.READY, queue=Q.PAUSED, total_bytes=940 * mb),
        _it("q5", "linux_installer.iso", T.READY, total_bytes=3400 * mb),
        _it("q6", "slides_final.pdf", T.RETRY_WAIT, attempt_count=2, retry_in_seconds=12, host="files.example.org"),
        _it("q7", "wedding_clip.mp4", T.COMPLETED, queue=Q.REMOVED, bytes_downloaded=198 * mb, total_bytes=198 * mb, progress_fraction=1.0),
        _it("q8", "lecture_03.webm", T.COMPLETED, queue=Q.REMOVED, bytes_downloaded=412 * mb, total_bytes=412 * mb, progress_fraction=1.0),
        _it("q9", "missing_file.bin", T.FAILED, queue=Q.REMOVED, last_failure_code="HTTP_404"),
        _it("q10", "very_long_name_" + "segment_" * 8 + "final_render_v12.mp4", T.READY, prio=PR.LOW, total_bytes=88 * mb, host="a-very-long-hostname.subdomain.example.co.uk"),
        _it("q11", "cancelled_download.zip", T.CANCELLED, queue=Q.REMOVED),
        _it("q12", "photos_export.tar", T.READY, total_bytes=1200 * mb),
    ]


def _select(widget, *ids):
    sel = widget.table.selectionModel()
    sel.clearSelection()
    for row in range(widget.proxy.rowCount()):
        if widget.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) in ids:
            sel.select(widget.proxy.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


class _Handoff:
    def __init__(self):
        self.subs, self.snaps, self.calls = {}, {}, []

    def start(self): pass
    def stop(self, **_): pass
    def subscribe(self, cb): self.subs[len(self.subs) + 1] = cb; return len(self.subs)
    def unsubscribe(self, token): self.subs.pop(token, None)
    def send(self, device_id, artifact): self.calls.append(device_id); return "h1"
    def snapshot(self, handoff_id): return self.snaps.get(handoff_id)
    def cancel(self, handoff_id): return True


class _Discovery:
    def __init__(self): self.on_found = self.on_removed = None
    def subscribe(self, *, on_found=None, on_removed=None): self.on_found, self.on_removed = on_found, on_removed
    def start(self): pass
    def stop(self): pass


PROFILE = "pinned-tls-signature-v1"


def _trusted(device_id, name, host):
    return TrustedFriendSendDevice(device_id=device_id, display_name=name, tls_spki_sha256="ab" * 32, protocol_version=1, security_profile=PROFILE,
                                   endpoint_host=host, endpoint_port=41000, paired_at_utc=NOW.isoformat())


def render_all(out: Path, tmp: Path, *, quick: bool = False) -> list[Path]:
    from fixtures_fake_manager import FakeManager  # noqa: PLC0415 - test helper alias, see bottom of file

    app = QApplication.instance() or QApplication([])
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    themes = ThemeManager(app, QSettings(str(tmp / "settings.ini"), QSettings.Format.IniFormat))

    identity = DesktopIdentityStore(tmp / "id").load_or_create()
    store = FriendSendTrustStore(tmp / "trust.json")
    store.upsert(_trusted("pixel", "Pixel 8", "192.168.1.20"))
    store.upsert(_trusted("moto", "Moto G84", "192.168.1.31"))
    store.upsert(_trusted("tab", "Galaxy Tab", "192.168.1.44"))
    handoff, discovery = _Handoff(), _Discovery()
    controller = DeviceModeController(trust_store=store, identity=identity, handoff_service=handoff, discovery=discovery)
    controller.start()
    discovery.on_found(DiscoveredFriendSendDevice("pixel", FriendSendEndpoint("192.168.1.20", 41000), 1, PROFILE))
    discovery.on_found(DiscoveredFriendSendDevice("tab", FriendSendEndpoint("192.168.1.44", 41000), 1, PROFILE))

    media = tmp / "holiday.mp4"
    media.write_bytes(b"x" * 2048)
    artifact = Artifact.from_completed_download(media, duration=168.0)

    def save(widget, name, theme):
        path = out / theme / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        app.processEvents()
        widget.grab().save(str(path))
        written.append(path)

    def make_window(width, height):
        manager = FakeManager(items=fixture_items())
        manager.stop = lambda: None
        widget = DownloadManagerWidget(manager, theme=themes.theme)
        window = MainWindow(manager, widget, theme_manager=themes, devices_page=DevicesPage(controller), device_actions={"pair": lambda: None})
        window.resize(width, height)
        window.show()
        widget.publish_state()
        app.processEvents()
        return manager, widget, window

    for theme in ("dark", "light"):
        themes.set_theme(theme)
        sizes = [(1366, 768)] if quick else [(1366, 768), (1920, 1080), (2560, 1440), (1100, 640)]
        for width, height in sizes:
            manager, widget, window = make_window(width, height)
            save(window, f"main_{width}x{height}", theme)
            if (width, height) == (1366, 768):
                _select(widget, "q1", "q2")
                save(window, "main_multiselect", theme)
                _select(widget, "q1")
                menu = widget.build_context_menu()
                if menu is not None:
                    menu.adjustSize()
                    save(menu, "context_menu_transferring", theme)
                _select(widget, "q7")
                menu = widget.build_context_menu()
                if menu is not None:
                    menu.adjustSize()
                    save(menu, "context_menu_completed", theme)
                window._select_sidebar("status:Completed")
                save(window, "main_filter_completed", theme)
                window._select_sidebar("tool:queues")
                save(window, "queues_view", theme)
                window._select_sidebar("tool:devices")
                save(window, "devices_page", theme)
                window._select_sidebar("status:All")
                details = DetailsDialog(manager, manager.item_snapshot("q1"), window, theme=theme)
                save(details, "details_active", theme)
                details.close()
                details = DetailsDialog(manager, manager.item_snapshot("q9"), window, theme=theme)
                save(details, "details_failed", theme)
                details.close()
                add = AddDownloadDialog(widget, theme=theme)
                save(add, "add_download", theme)
                add.close()
                share = ShareSelectorDialog(artifact, controller, window)
                save(share, "share_selector", theme)
                share.close()
                link = ShareByLinkDialog(artifact, window)
                save(link, "share_by_link", theme)
                link.close()
                send = SendToDeviceDialog(artifact, controller, window)
                save(send, "send_select_device", theme)
                stages = {
                    "send_preparing": snap(HandoffState.PREPARING),
                    "send_remux": snap(HandoffState.PREPARING, preparation_kind="REMUX", preparation_progress=0.43),
                    "send_transcoding": snap(HandoffState.PREPARING, preparation_kind="TRANSCODE_VIDEO", preparation_progress=0.43),
                    "send_transferring": snap(HandoffState.TRANSFERRING, progress_fraction=0.62),
                    "send_received": snap(HandoffState.RECEIVED),
                    "send_identity_changed": snap(HandoffState.FAILED, failure_code=HandoffErrorCode.TLS_PIN_MISMATCH),
                    "send_error": snap(HandoffState.FAILED, failure_code=HandoffErrorCode.CONNECTION_FAILED),
                }
                send.select_page.hide()
                send._device = controller.row_for("pixel")
                send._handoff_id = "h1"
                for name, s in stages.items():
                    handoff.snaps["h1"] = s
                    dlg = SendToDeviceDialog(artifact, controller, window)
                    dlg._device = controller.row_for("pixel")
                    dlg._handoff_id = "h1"
                    dlg.select_page.hide()
                    dlg.progress_page.show()
                    dlg.send_button.hide()
                    dlg._on_updated("h1")
                    save(dlg, name, theme)
                    dlg.close()
                send.close()
                pair = PairDeviceDialog(controller, window)
                save(pair, "pair_device", theme)
                pair.reject()
                box = QMessageBox(QMessageBox.Icon.Warning, "Forget Pixel 8?", "Rýchlik will no longer send files to this device. You can pair it again at any time.", QMessageBox.StandardButton.NoButton, window)
                box.addButton("Forget device", QMessageBox.ButtonRole.DestructiveRole)
                box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
                save(box, "forget_confirmation", theme)
                box.close()
                settings = SettingsDialog(themes, window)
                save(settings, "settings", theme)
                settings.close()
            window.close()
    controller.stop()
    return written


def snap(state, **kw):
    base = dict(handoff_id="h1", device_id="pixel", artifact_id="a1", state=state, bytes_sent=0, total_bytes=100, progress_fraction=None)
    base.update(kw)
    return DeviceHandoffSnapshot(**base)


if __name__ == "__main__":
    import tempfile

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/gui_implementation_review/desktop")
    with tempfile.TemporaryDirectory() as td:
        files = render_all(target, Path(td))
    print(f"wrote {len(files)} screenshots to {target}")
