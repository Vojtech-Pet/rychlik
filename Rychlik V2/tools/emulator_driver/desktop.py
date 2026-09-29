import os, sys, time, hashlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
from lib import *
from pathlib import Path
from PySide6.QtWidgets import QApplication
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.device.contracts import FriendSendEndpoint
from rychlik.device.device_handoff_service import DeviceHandoffService
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.secure_transport import SecureFriendSendTransport
from rychlik.device.security.trust_store import FriendSendTrustStore
from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed
from rychlik.gui.device_mode import DeviceModeController
from rychlik.gui.dialogs import device_dialogs as DD
from http_fixture_server import HttpFixtureServer
import media_fixtures as MF

STATE = Path("/tmp/rt/emu_desktop")
app = QApplication.instance() or QApplication([])


class Discovery:
    def __init__(self): self.on_found = self.on_removed = None
    def subscribe(self, *, on_found=None, on_removed=None): self.on_found, self.on_removed = on_found, on_removed
    def start(self): pass
    def stop(self): pass


def pump(pred, timeout=60, dialog=None, seen=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if dialog is not None and seen is not None:
            h = dialog.headline.text()
            if h and (not seen or seen[-1] != h): seen.append(h)
        if pred(): return True
        time.sleep(0.02)
    return pred()


class Rig:
    def __init__(self, work: Path):
        self.work = work; work.mkdir(parents=True, exist_ok=True)
        self.http = HttpFixtureServer().start()
        self.manager = DownloadManagerService(config=DownloadManagerConfig(database_path=work / "state.db", max_active_transfers=1))
        self.manager.start()
        self.identity = DesktopIdentityStore(STATE / "id").load_or_create()
        self.trust = FriendSendTrustStore(STATE / "trust.json")
        self.device = self.trust.all_devices()[0]
        self.stale_port = self.device.endpoint_port
        self.port = self.discover_port()  # real zeroconf discovery of the emulator's NsdManager advertisement
        adb("forward", f"tcp:{self.port}", f"tcp:{self.port}")
        self.service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=self.identity, trust_store=self.trust))
        self.discovery = Discovery()
        self.controller = DeviceModeController(trust_store=self.trust, identity=self.identity, handoff_service=self.service, discovery=self.discovery)
        self.controller.start()
        # test setup only: the emulator's NSD address is not dialable from the host, so announce the forwarded endpoint
        self.announce(self.port)

    def discover_port(self):
        from rychlik.device.security.discovery import FriendSendDiscoveryService
        found = []
        svc = FriendSendDiscoveryService()
        svc.subscribe(on_found=lambda d: found.append(d))
        svc.start()
        end = time.monotonic() + 8
        while time.monotonic() < end and not any(d.device_id == self.device.device_id for d in found):
            time.sleep(0.3)
        svc.stop()
        match = [d for d in found if d.device_id == self.device.device_id]
        if not match:
            self.nsd_endpoint = None
            print("NSD: no announcement seen (emulator multicast is unreliable); using the endpoint recorded at pairing")
            return self.device.endpoint_port
        self.nsd_endpoint = match[0].endpoint
        return match[0].endpoint.port

    def announce(self, port):
        self.discovery.on_found(DiscoveredFriendSendDevice(self.device.device_id, FriendSendEndpoint("127.0.0.1", port), 1, "pinned-tls-signature-v1"))

    def download(self, source: Path, key: str):
        body = source.read_bytes()
        self.http.configure_resumable(key, body=body)
        added = self.manager.add_download(DownloadRequest(url=f"{self.http.base_url}/resumable/{key}", destination_dir=self.work / "dl", filename_hint=source.name))
        assert pump(lambda: not any(i.queue_entry_id == added.queue_entry_id for i in self.manager.snapshot().items), 60)
        artifact, _ = build_artifact_for_completed(self.manager, added.queue_entry_id)
        return artifact

    def send(self, artifact, timeout=120):
        dialog = DD.SendToDeviceDialog(artifact, self.controller)
        seen = []
        dialog.send_button.click()
        ok = pump(lambda: dialog.cancel_button.text() == "Close", timeout, dialog, seen)
        return dialog, seen, ok

    def close(self):
        self.controller.stop(); self.manager.stop(); self.http.stop()
