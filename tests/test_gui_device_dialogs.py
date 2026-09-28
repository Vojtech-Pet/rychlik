"""Device Mode dialogs driven through the real controller with a fake handoff transport."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from rychlik.core.artifact import Artifact
from rychlik.device.contracts import FriendSendEndpoint, HandoffErrorCode, HandoffState
from rychlik.device.device_handoff_service import DeviceHandoffEvent, DeviceHandoffEventKind
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.trust_store import FriendSendTrustStore
from rychlik.gui.device_mode import DeviceModeController
from rychlik.gui.dialogs import device_dialogs as DD
from tests.test_gui_device_mode import PROFILE, _FakeDiscovery, _FakeHandoff, _trusted, snap


@pytest.fixture
def env(qapp, tmp_path):
    identity = DesktopIdentityStore(tmp_path / "id").load_or_create()
    store = FriendSendTrustStore(tmp_path / "trust.json")
    handoff, discovery = _FakeHandoff(), _FakeDiscovery()
    c = DeviceModeController(trust_store=store, identity=identity, handoff_service=handoff, discovery=discovery)
    c.start()
    media = tmp_path / "holiday.mp4"
    media.write_bytes(b"x" * 64)
    artifact = Artifact.from_completed_download(media, duration=168.0)
    yield c, handoff, discovery, store, artifact
    c.stop()


def online(discovery, port=41000):
    discovery.on_found(DiscoveredFriendSendDevice("d1", FriendSendEndpoint("192.168.1.20", port), 1, PROFILE))


def test_selector_disables_device_send_without_trusted_devices_and_says_why(env):
    c, _, _, store, artifact = env
    dlg = DD.ShareSelectorDialog(artifact, c)
    assert not dlg.device_button.isEnabled() and "No paired devices" in dlg.hint.text()
    store.upsert(_trusted())
    assert DD.ShareSelectorDialog(artifact, c).device_button.isEnabled()
    assert not DD.ShareSelectorDialog(artifact, None).device_button.isEnabled()


def test_send_dialog_offline_banner_and_disabled_send(env):
    c, _, _, store, artifact = env
    store.upsert(_trusted())
    dlg = DD.SendToDeviceDialog(artifact, c)
    assert "No device is online" in dlg.banner.text() and not dlg.send_button.isEnabled()
    online(env[2])
    assert dlg.banner.text() == "" and dlg.send_button.isEnabled() and dlg.selected_device_id() == "d1"


def test_send_flow_shows_truthful_stages_and_result(env):
    c, handoff, discovery, store, artifact = env
    store.upsert(_trusted())
    online(discovery)
    dlg = DD.SendToDeviceDialog(artifact, c)
    dlg.send_button.click()
    assert handoff.calls[0][:2] == ("send", "d1")
    handoff.snapshots["h1"] = snap(HandoffState.PREPARING, preparation_kind="TRANSCODE_VIDEO", preparation_progress=0.43)
    dlg._on_updated("h1")
    assert dlg.headline.text() == "Converting video for Pixel 8…" and dlg.detail.text() == "Converted 1:12 of 2:48"
    assert "original file will not be changed" in dlg.reassurance.text()
    handoff.snapshots["h1"] = snap(HandoffState.TRANSFERRING, progress_fraction=0.5)
    dlg._on_updated("h1")
    assert dlg.headline.text() == "Sending to Pixel 8…" and dlg.bar.value() == 500
    handoff.snapshots["h1"] = snap(HandoffState.RECEIVED)
    dlg._on_updated("h1")
    assert dlg.headline.text() == "Received by Pixel 8" and dlg.cancel_button.text() == "Close"


def test_cancel_button_cancels_a_running_handoff_before_closing(env):
    c, handoff, discovery, store, artifact = env
    store.upsert(_trusted())
    online(discovery)
    dlg = DD.SendToDeviceDialog(artifact, c)
    dlg.send_button.click()
    handoff.snapshots["h1"] = snap(HandoffState.TRANSFERRING, progress_fraction=0.1)
    dlg.cancel_button.click()
    assert ("cancel", "h1") in handoff.calls


def test_identity_failure_offers_forget_only_never_trust_new_identity(env):
    c, handoff, discovery, store, artifact = env
    store.upsert(_trusted())
    online(discovery)
    dlg = DD.SendToDeviceDialog(artifact, c)
    dlg.send_button.click()
    handoff.snapshots["h1"] = snap(HandoffState.FAILED, failure_code=HandoffErrorCode.TLS_PIN_MISMATCH)
    handoff.emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_FAILED, "h1", "d1"))
    assert dlg.headline.text() == "Device identity changed"
    assert dlg.forget_button.isVisibleTo(dlg)
    texts = " ".join(b.text().lower() for b in dlg.findChildren(DD.QPushButton))
    assert "trust" not in texts and "accept" not in texts


def test_port_churn_between_open_and_send_uses_the_new_endpoint_without_repinning(env):
    c, handoff, discovery, store, artifact = env
    store.upsert(_trusted(port=41000))
    online(discovery, 41000)
    dlg = DD.SendToDeviceDialog(artifact, c)
    online(discovery, 52000)  # the phone restarted and re-registered on another port
    dlg.send_button.click()
    trusted = store.get("d1")
    assert trusted.endpoint_port == 52000 and trusted.tls_spki_sha256 == "ab" * 32
    assert handoff.calls[0][1] == "d1"


def test_pair_dialog_shows_real_code_and_detects_completion(env, monkeypatch):
    from rychlik.device.security.pairing_bootstrap import SecurePairingManager

    c, _, _, store, _ = env
    c._pairing_factory = lambda ident, ts: SecurePairingManager(identity=ident, trust_store=ts, bind_host="127.0.0.1")
    dlg = DD.PairDeviceDialog(c)
    assert dlg.code_box.toPlainText().startswith("{") and dlg.status_label.text() == "Waiting for the phone…"
    assert dlg.expiry_label.text().startswith("Expires in")
    store.upsert(_trusted("phone-2"))
    dlg.tick()
    assert dlg.paired_row is not None and "Paired with Pixel 8" in dlg.status_label.text() and dlg.code_box.toPlainText() == ""
    dlg.reject()


def test_pair_dialog_expiry_disables_copy(env):
    from rychlik.device.security.pairing_bootstrap import SecurePairingManager

    c, *_ = env
    c._pairing_factory = lambda ident, ts: SecurePairingManager(identity=ident, trust_store=ts, bind_host="127.0.0.1")
    dlg = DD.PairDeviceDialog(c)
    dlg._session = type(dlg._session)(dlg._session.code_text, datetime(2000, 1, 1, tzinfo=timezone.utc), dlg._session.baseline_device_ids)
    dlg.tick()
    assert dlg.status_label.text() == "This code expired." and not dlg.copy_button.isEnabled()
    dlg.reject()


def test_devices_page_lists_states_and_forget_requires_confirmation(env, monkeypatch):
    c, _, discovery, store, _ = env
    store.upsert(_trusted())
    page = DD.DevicesPage(c)
    assert "Trusted · Offline" in page.list.item(0).text()
    online(discovery)
    assert "Trusted · Online" in page.list.item(0).text()
    page.list.setCurrentRow(0)
    monkeypatch.setattr(DD, "confirm_forget", lambda row, parent=None: False)
    page.forget_selected()
    assert store.get("d1") is not None
    monkeypatch.setattr(DD, "confirm_forget", lambda row, parent=None: True)
    page.forget_selected()
    assert store.get("d1") is None
    assert page.list.count() == 1 and "Not paired" in page.list.item(0).text()  # still visible on the LAN, no longer trusted
    discovery.on_removed("d1")
    assert page.list.count() == 0 and page.empty.isVisibleTo(page)


def test_share_by_link_never_claims_a_public_link(env):
    _, _, _, _, artifact = env
    dlg = DD.ShareByLinkDialog(artifact)
    assert dlg.result.status in (DD.ShareStatus.ACTIVE, DD.ShareStatus.CREATING, DD.ShareStatus.OFFLINE, DD.ShareStatus.FAILED)
    assert "http" not in dlg.status_label.text().lower()
