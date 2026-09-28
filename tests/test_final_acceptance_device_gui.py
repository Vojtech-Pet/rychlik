"""Final acceptance: real completed download -> Share -> Send to device dialog -> real A16 preparation ->
real A15 secure transport -> real Dart receiver process -> RECEIVED, observed through the visible dialog.

Real: DownloadManagerService, HTTP fixture server, FFmpeg, TLS-pinned Ed25519 transport, Dart receiver, trust store,
production DeviceModeController/DeviceHandoffService/dialogs. Stubbed: LAN discovery only (the mDNS path is an
emulator/physical concern) -- the device is announced to the controller exactly as discovery would."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.device.contracts import FriendSendEndpoint
from rychlik.device.device_handoff_service import DeviceHandoffService
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.pairing_bootstrap import SecurePairingManager
from rychlik.device.security.secure_transport import SecureFriendSendTransport
from rychlik.device.security.trust_store import FriendSendTrustStore
from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed
from rychlik.gui.device_mode import DeviceModeController
from rychlik.gui.dialogs import device_dialogs as DD

from friendsend_secure_dart_harness import FriendSendSecureDartHarness, run_dart_pairing_client
from media_fixtures import make_compatible_mp4, make_full_transcode_source, make_remux_mkv

pytestmark = pytest.mark.skipif(
    shutil.which("dart") is None
    and not Path("/mnt/Basic_data_partition1/vojtech/flutter/bin/cache/dart-sdk/bin/dart").exists(),
    reason="no Dart SDK available for the real receiver process",
)

PROFILE = "pinned-tls-signature-v1"


class _Discovery:
    def __init__(self):
        self.on_found = self.on_removed = None

    def subscribe(self, *, on_found=None, on_removed=None):
        self.on_found, self.on_removed = on_found, on_removed

    def start(self):
        pass

    def stop(self):
        pass


def _pump(qapp, predicate, timeout=30.0, seen=None, dialog=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if dialog is not None and seen is not None:
            headline = dialog.headline.text()
            if headline and (not seen or seen[-1] != headline):
                seen.append(headline)
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


def _download(manager, http_fixture_server, source: Path, key: str, dest: Path, qapp):
    body = source.read_bytes()
    http_fixture_server.configure_resumable(key, body=body)
    added = manager.add_download(DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/{key}", destination_dir=dest, filename_hint=source.name))
    assert _pump(qapp, lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items), timeout=20)
    artifact, _ = build_artifact_for_completed(manager, added.queue_entry_id)
    assert artifact is not None
    return artifact, body


def _paired(tmp_path, receiver):
    import json

    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")
    pairing = SecurePairingManager(identity=identity, trust_store=trust_store)
    payload = pairing.create_session()
    try:
        result = run_dart_pairing_client(identity_dir=receiver.identity_dir, device_id=receiver.device_id, display_name="Test Phone",
                                         friendsend_host=receiver.host, friendsend_port=receiver.port, payload_json_line=json.dumps(payload.to_wire_dict()))
        assert result["pairing_result"] == "ok", result
    finally:
        pairing.stop()
    return identity, trust_store


def _stages(seen):
    """The distinct headlines in order, ignoring the brief, optional 'Connecting…' step."""
    return [h for h in seen if not h.startswith("Connecting")]


def _run(qapp, http_fixture_server, tmp_path, maker, name, expect_kind):
    source = maker(tmp_path / name)
    body_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", max_active_transfers=1))
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    controller = None
    try:
        artifact, _ = _download(manager, http_fixture_server, source, "acc-" + name, tmp_path / "dl", qapp)
        identity, trust_store = _paired(tmp_path, receiver)
        service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        discovery = _Discovery()
        controller = DeviceModeController(trust_store=trust_store, identity=identity, handoff_service=service, discovery=discovery)
        controller.start()
        discovery.on_found(DiscoveredFriendSendDevice(receiver.device_id, FriendSendEndpoint(receiver.host, receiver.port), 1, PROFILE))

        selector = DD.ShareSelectorDialog(artifact, controller)
        assert selector.device_button.isEnabled()
        dialog = DD.SendToDeviceDialog(artifact, controller)
        assert dialog.selected_device_id() == receiver.device_id
        seen: list[str] = []
        import rychlik.device.media.preparation_service as prep_module

        real_run = prep_module.run_preparation

        def held_run(*args, **kwargs):  # keep the real FFmpeg run open ~0.6 s so its stage is observable
            time.sleep(0.6)
            return real_run(*args, **kwargs)

        prep_module.run_preparation = held_run
        dialog.send_button.click()
        assert _pump(qapp, lambda: dialog.headline.text().startswith("Received by"), timeout=40, seen=seen, dialog=dialog), seen
        snap = controller.snapshot(dialog._handoff_id)
        assert snap.preparation_kind == expect_kind
        first_sending = next(i for i, h in enumerate(seen) if h.startswith("Sending"))
        assert not any(h.startswith("Sending") for h in seen[:first_sending]) and first_sending >= 1
        # a preparation stage is never labelled as sending, and no fabricated processed-time appears without a known duration
        assert artifact.duration is None and not dialog.detail.text().startswith("Converted")
        event = receiver.wait_for_event("received", handoff_id=dialog._handoff_id)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == body_hash  # the original is untouched
        return seen, event, artifact
    finally:
        import rychlik.device.media.preparation_service as prep_module

        prep_module.run_preparation = real_run
        if controller is not None:
            controller.stop()
        receiver.stop()
        manager.stop()


def test_gui_send_passthrough(qapp, http_fixture_server, tmp_path):
    seen, event, artifact = _run(qapp, http_fixture_server, tmp_path, make_compatible_mp4, "pass.mp4", "PASSTHROUGH")
    assert event["sha256"] == artifact.sha256
    assert _stages(seen)[:2] == ["Checking the file…", "Sending to Test Phone…"], seen
    assert not any("compatible copy" in h or "Converting" in h for h in seen)
    assert seen[-1].startswith("Received by")


def test_gui_send_remux_shows_preparing_then_sending(qapp, http_fixture_server, tmp_path):
    seen, event, artifact = _run(qapp, http_fixture_server, tmp_path, make_remux_mkv, "remux.mkv", "REMUX")
    assert event["sha256"] != artifact.sha256  # derived MP4
    assert _stages(seen)[:3] == ["Checking the file…", "Preparing compatible copy…", "Sending to Test Phone…"], seen
    assert seen[-1].startswith("Received by")


def test_gui_send_transcode_shows_converting_then_sending(qapp, http_fixture_server, tmp_path):
    seen, event, artifact = _run(qapp, http_fixture_server, tmp_path, make_full_transcode_source, "transcode.webm", "TRANSCODE_AUDIO_VIDEO")
    assert event["sha256"] != artifact.sha256
    assert _stages(seen)[:3] == ["Checking the file…", "Converting video for Test Phone…", "Sending to Test Phone…"], seen
    assert seen[-1].startswith("Received by")


def _rig(qapp, http_fixture_server, tmp_path, name="churn.mp4"):
    source = make_compatible_mp4(tmp_path / name)
    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", max_active_transfers=1))
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    artifact, _ = _download(manager, http_fixture_server, source, "acc-" + name, tmp_path / "dl", qapp)
    identity, trust_store = _paired(tmp_path, receiver)
    service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
    discovery = _Discovery()
    controller = DeviceModeController(trust_store=trust_store, identity=identity, handoff_service=service, discovery=discovery)
    controller.start()
    return manager, receiver, artifact, trust_store, discovery, controller


def test_gui_endpoint_churn_refreshes_endpoint_only_and_keeps_the_pin(qapp, http_fixture_server, tmp_path):
    manager, receiver, artifact, trust_store, discovery, controller = _rig(qapp, http_fixture_server, tmp_path)
    try:
        before = trust_store.get(receiver.device_id)
        trust_store.upsert(before.with_endpoint(FriendSendEndpoint(receiver.host, 1)))  # stale: the phone changed its port
        discovery.on_found(DiscoveredFriendSendDevice(receiver.device_id, FriendSendEndpoint(receiver.host, receiver.port), 1, PROFILE))
        dialog = DD.SendToDeviceDialog(artifact, controller)
        dialog.send_button.click()
        assert _pump(qapp, lambda: dialog.headline.text().startswith("Received by"), timeout=40)
        after = trust_store.get(receiver.device_id)
        assert (after.endpoint_host, after.endpoint_port) == (receiver.host, receiver.port)
        assert after.tls_spki_sha256 == before.tls_spki_sha256 and after.device_id == before.device_id  # identity/pin untouched
    finally:
        controller.stop()
        receiver.stop()
        manager.stop()


def test_gui_identity_change_offers_only_forget_and_forgetting_removes_trust(qapp, http_fixture_server, tmp_path):
    manager, receiver, artifact, trust_store, discovery, controller = _rig(qapp, http_fixture_server, tmp_path)
    impostor = FriendSendSecureDartHarness(tmp_path / "impostor")  # a different TLS identity answering at the discovered endpoint
    try:
        discovery.on_found(DiscoveredFriendSendDevice(receiver.device_id, FriendSendEndpoint(impostor.host, impostor.port), 1, PROFILE))
        dialog = DD.SendToDeviceDialog(artifact, controller)
        dialog.send_button.click()
        assert _pump(qapp, lambda: dialog.headline.text() == "Device identity changed", timeout=30), dialog.headline.text()
        assert dialog.forget_button.isVisibleTo(dialog)
        labels = " ".join(b.text().lower() for b in dialog.findChildren(DD.QPushButton))
        assert "trust" not in labels and "accept" not in labels and "certificate" not in labels
        assert controller.row_for(receiver.device_id).state.name == "IDENTITY_CHANGED"
        assert trust_store.get(receiver.device_id).tls_spki_sha256 == receiver.tls_spki_sha256  # never silently re-pinned
        assert not impostor_received(impostor)  # no media byte reached the impostor
        controller.forget_device(receiver.device_id)
        assert trust_store.get(receiver.device_id) is None
    finally:
        controller.stop()
        impostor.stop()
        receiver.stop()
        manager.stop()


def impostor_received(harness) -> bool:
    try:
        harness.wait_for_event("received", timeout=0.3)
        return True
    except Exception:  # noqa: BLE001 - no event within the window
        return False
