"""Final GUI/UX implementation: the Device Mode presentation boundary (wording, stages, controller)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from rychlik.device.contracts import DeviceHandoffSnapshot, FriendSendEndpoint, HandoffErrorCode, HandoffState
from rychlik.device.desktop_devices import DeviceState
from rychlik.device.device_handoff_service import DeviceHandoffEvent, DeviceHandoffEventKind
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.trust_store import FriendSendTrustStore, TrustedFriendSendDevice
from rychlik.gui.device_mode import DeviceModeController, PairingSession, friendly_error, pairing_expiry_text, send_stage

PROFILE = "pinned-tls-signature-v1"
FORBIDDEN_WORDS = ("spki", "ed25519", "signature", "challenge", "hmac", "nonce", "transcript", "payload", "pin ", "tls_", "_mismatch")


def snap(state, **kw):
    base = dict(handoff_id="h1", device_id="d1", artifact_id="a1", state=state, bytes_sent=0, total_bytes=100, progress_fraction=None)
    base.update(kw)
    return DeviceHandoffSnapshot(**base)


# --- wording ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", list(HandoffErrorCode))
def test_every_error_code_has_plain_wording_without_protocol_terms(code):
    err = friendly_error(code, "Pixel 8")
    assert err.title and err.text
    lowered = f"{err.title} {err.text}".lower()
    assert not [w for w in FORBIDDEN_WORDS if w in lowered], (code, lowered)


@pytest.mark.parametrize("code", [HandoffErrorCode.TLS_PIN_MISMATCH, HandoffErrorCode.DEVICE_IDENTITY_MISMATCH])
def test_identity_problems_use_the_approved_message_and_never_offer_to_trust(code):
    err = friendly_error(code, "Moto G84")
    assert err.identity_problem and err.title == "Device identity changed"
    assert "forget the device and pair it again" in err.text
    assert not any(w in err.text.lower() for w in ("trust the new", "accept the new", "trust automatically"))


def test_unknown_or_missing_code_falls_back_to_a_bounded_message():
    assert friendly_error(None, "Pixel 8").title == "Couldn’t send the file"


def test_connection_failure_names_the_device_and_says_nothing_was_saved():
    err = friendly_error(HandoffErrorCode.CONNECTION_FAILED, "Pixel 8")
    assert "Pixel 8" in err.text and "Nothing was saved" in err.text


# --- stages: truthful, never "Sending" while preparing ---------------------------------------------------------------------


def test_stage_checking_then_passthrough_goes_straight_to_sending():
    assert send_stage(snap(HandoffState.PREPARING), "Pixel 8").headline == "Checking the file…"
    assert send_stage(snap(HandoffState.PREPARING, preparation_kind="PASSTHROUGH"), "Pixel 8").step == 0
    sending = send_stage(snap(HandoffState.TRANSFERRING, progress_fraction=0.62, preparation_kind="PASSTHROUGH"), "Pixel 8")
    assert (sending.step, sending.headline, sending.fraction) == (1, "Sending to Pixel 8…", 0.62)


def test_remux_shows_preparing_compatible_copy_with_the_reassurance():
    st = send_stage(snap(HandoffState.PREPARING, preparation_kind="REMUX", preparation_progress=0.43), "Pixel 8")
    assert (st.step, st.headline, st.detail, st.fraction) == (0, "Preparing compatible copy…", "Optimizing container…", 0.43)
    assert st.reassurance == "The original file will not be changed."


def test_transcode_shows_converted_time_only_when_duration_is_really_known():
    with_duration = send_stage(snap(HandoffState.PREPARING, preparation_kind="TRANSCODE_AUDIO_VIDEO", preparation_progress=0.43), "Pixel 8", 168.0)
    assert with_duration.headline == "Converting video for Pixel 8…" and with_duration.detail == "Converted 1:12 of 2:48"
    without = send_stage(snap(HandoffState.PREPARING, preparation_kind="TRANSCODE_VIDEO", preparation_progress=0.43), "Pixel 8", None)
    assert without.detail == "" and without.fraction == 0.43  # never a fabricated time
    unknown_fraction = send_stage(snap(HandoffState.PREPARING, preparation_kind="TRANSCODE_VIDEO", preparation_progress=None), "Pixel 8", 168.0)
    assert unknown_fraction.indeterminate and unknown_fraction.detail == ""


def test_sending_is_never_shown_while_state_is_preparing_or_connecting():
    for st in (HandoffState.CREATED, HandoffState.PREPARING):
        assert "Sending" not in send_stage(snap(st, preparation_kind="TRANSCODE_VIDEO"), "P").headline
    assert send_stage(snap(HandoffState.CONNECTING), "P").step == 1


def test_received_wording_never_claims_delivery_to_a_person():
    st = send_stage(snap(HandoffState.RECEIVED), "Pixel 8")
    assert st.step == 2 and "verified" in st.detail and "deliver" not in (st.headline + st.detail).lower()


# --- controller ------------------------------------------------------------------------------------------------------------------


class _FakeHandoff:
    def __init__(self):
        self.calls, self._subs, self.snapshots, self.started, self.stopped = [], {}, {}, False, False

    def start(self):
        self.started = True

    def stop(self, **_):
        self.stopped = True

    def subscribe(self, cb):
        self._subs[len(self._subs) + 1] = cb
        return len(self._subs)

    def unsubscribe(self, token):
        self._subs.pop(token, None)

    def send(self, device_id, artifact):
        self.calls.append(("send", device_id, artifact))
        return "h1"

    def snapshot(self, handoff_id):
        return self.snapshots.get(handoff_id)

    def cancel(self, handoff_id):
        self.calls.append(("cancel", handoff_id))
        return True

    def emit(self, event):
        for cb in list(self._subs.values()):
            cb(event)


class _FakeDiscovery:
    def __init__(self, fail=False):
        self.fail, self.started, self.stopped, self.on_found, self.on_removed = fail, False, False, None, None

    def subscribe(self, *, on_found=None, on_removed=None):
        self.on_found, self.on_removed = on_found, on_removed

    def start(self):
        if self.fail:
            raise OSError("no multicast")
        self.started = True

    def stop(self):
        self.stopped = True


def _trusted(device_id="d1", port=41000):
    return TrustedFriendSendDevice(device_id=device_id, display_name="Pixel 8", tls_spki_sha256="ab" * 32, protocol_version=1, security_profile=PROFILE,
                                   endpoint_host="192.168.1.20", endpoint_port=port, paired_at_utc=datetime.now(timezone.utc).isoformat())


@pytest.fixture
def controller(qapp, tmp_path):
    identity = DesktopIdentityStore(tmp_path / "id").load_or_create()
    store = FriendSendTrustStore(tmp_path / "trust.json")
    store.upsert(_trusted())
    handoff, discovery = _FakeHandoff(), _FakeDiscovery()
    c = DeviceModeController(trust_store=store, identity=identity, handoff_service=handoff, discovery=discovery, probe=lambda t: True, probe_async=False)
    c.start()
    yield c, handoff, discovery, store
    c.stop()


def test_start_wires_discovery_and_handoff_and_stop_tears_down(controller):
    c, handoff, discovery, _ = controller
    assert handoff.started and discovery.started and discovery.on_found is not None
    c.stop()
    assert handoff.stopped and discovery.stopped


def test_discovery_problem_is_reported_not_fatal(qapp, tmp_path):
    identity = DesktopIdentityStore(tmp_path / "id").load_or_create()
    c = DeviceModeController(trust_store=FriendSendTrustStore(tmp_path / "t.json"), identity=identity, handoff_service=_FakeHandoff(), discovery=_FakeDiscovery(fail=True), probe=lambda t: True, probe_async=False)
    problems = []
    c.discovery_problem.connect(problems.append)
    c.start()
    assert problems and "unavailable" in problems[0]
    c.stop()


def test_devices_follow_trust_and_live_discovery_and_emit_change(controller):
    c, _, discovery, _ = controller
    changed = []
    c.devices_changed.connect(lambda: changed.append(1))
    assert c.rows()[0].state == DeviceState.TRUSTED_OFFLINE
    discovery.on_found(DiscoveredFriendSendDevice("d1", FriendSendEndpoint("192.168.1.20", 41000), 1, PROFILE))
    assert c.rows()[0].state == DeviceState.TRUSTED_ONLINE and changed
    discovery.on_removed("d1")
    assert c.rows()[0].state == DeviceState.TRUSTED_OFFLINE


def test_send_refreshes_the_endpoint_from_discovery_then_starts_the_real_handoff(controller):
    c, handoff, discovery, store = controller
    discovery.on_found(DiscoveredFriendSendDevice("d1", FriendSendEndpoint("192.168.1.31", 55555), 1, PROFILE))
    artifact = object()
    assert c.send("d1", artifact) == "h1"
    assert handoff.calls == [("send", "d1", artifact)]
    assert (store.get("d1").endpoint_host, store.get("d1").endpoint_port) == ("192.168.1.31", 55555)
    assert store.get("d1").tls_spki_sha256 == "ab" * 32  # identity untouched


def test_pin_failure_marks_identity_changed_and_a_later_success_clears_it(controller):
    c, handoff, discovery, _ = controller
    discovery.on_found(DiscoveredFriendSendDevice("d1", FriendSendEndpoint("192.168.1.20", 41000), 1, PROFILE))
    updates = []
    c.handoff_updated.connect(updates.append)
    handoff.snapshots["h1"] = snap(HandoffState.FAILED, failure_code=HandoffErrorCode.TLS_PIN_MISMATCH)
    handoff.emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_FAILED, "h1", "d1"))
    assert c.rows()[0].state == DeviceState.IDENTITY_CHANGED and updates == ["h1"]
    handoff.snapshots["h2"] = snap(HandoffState.RECEIVED, handoff_id="h2")
    handoff.emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_RECEIVED, "h2", "d1"))
    assert c.rows()[0].state == DeviceState.TRUSTED_ONLINE


def test_an_ordinary_connection_failure_does_not_mark_identity_changed(controller):
    c, handoff, _, _ = controller
    handoff.snapshots["h1"] = snap(HandoffState.FAILED, failure_code=HandoffErrorCode.CONNECTION_FAILED)
    handoff.emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_FAILED, "h1", "d1"))
    assert c.rows()[0].state == DeviceState.TRUSTED_OFFLINE


def test_forget_removes_trust_and_cancel_is_forwarded(controller):
    c, handoff, _, store = controller
    c.forget_device("d1")
    assert store.get("d1") is None and c.rows() == []
    assert c.cancel("h9") and ("cancel", "h9") in handoff.calls


def test_real_pairing_session_is_created_from_the_production_manager_and_completion_is_detected(qapp, tmp_path):
    from rychlik.device.security.pairing_bootstrap import SecurePairingManager

    identity = DesktopIdentityStore(tmp_path / "id").load_or_create()
    store = FriendSendTrustStore(tmp_path / "trust.json")
    c = DeviceModeController(trust_store=store, identity=identity, handoff_service=_FakeHandoff(), discovery=_FakeDiscovery(),
                             pairing_manager_factory=lambda ident, ts: SecurePairingManager(identity=ident, trust_store=ts, bind_host="127.0.0.1"))
    c.start()
    try:
        session = c.create_pairing_session()
        wire = json.loads(session.code_text)
        assert wire["security_profile"] == PROFILE and wire["protocol_version"] == 1 and len(wire["secret"]) == 32
        assert wire["desktop_instance_id"] == identity.desktop_instance_id
        assert session.seconds_left() > 250 and c.completed_pairing(session) is None
        again = c.create_pairing_session()  # a new session replaces the old listener; secrets are never reused
        assert json.loads(again.code_text)["secret"] != wire["secret"]
        store.upsert(_trusted("new-phone"))  # what a successful pairing confirm does
        row = c.completed_pairing(again)
        assert row is not None and row.device_id == "new-phone" and row.trusted
    finally:
        c.stop()


def test_pairing_expiry_text():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    s = PairingSession("x", now + timedelta(seconds=272), frozenset())
    assert pairing_expiry_text(s, now) == "Expires in 4:32"
    assert pairing_expiry_text(PairingSession("x", now - timedelta(seconds=5), frozenset()), now) == "Expires in 0:00"
