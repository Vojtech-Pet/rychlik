"""Final GUI/UX implementation: a device paired through the real A15 flow lives only in the
persistent trust store; DeviceHandoffService.send() must resolve it from there (owner-level fix)."""

import time
from datetime import datetime, timezone

import pytest

from rychlik.core.artifact import Artifact
from rychlik.device.contracts import HandoffState, UnknownDeviceError
from rychlik.device.device_handoff_service import DeviceHandoffService
from rychlik.device.security.trust_store import FriendSendTrustStore, TrustedFriendSendDevice
from rychlik.device.transport import FriendSendTransport, SendOutcome


class _RecordingSecureLikeTransport(FriendSendTransport):
    def __init__(self, trust_store):
        self.trust_store = trust_store
        self.sent_to = []

    def probe(self, endpoint):  # pragma: no cover - unused
        raise NotImplementedError

    def send(self, device, request, artifact_path, **kwargs):
        # exactly what SecureFriendSendTransport does: endpoint/pin come from the trust store, not the descriptor
        self.sent_to.append((device.device_id, self.trust_store.get(device.device_id).endpoint_port))
        return SendOutcome(HandoffState.RECEIVED, artifact_path.stat().st_size)


def _trusted(device_id="dev-1", port=41000):
    return TrustedFriendSendDevice(
        device_id=device_id, display_name="Pixel 8", tls_spki_sha256="ab" * 32, protocol_version=1,
        security_profile="pinned-tls-signature-v1", endpoint_host="192.168.1.20", endpoint_port=port,
        paired_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def _artifact(tmp_path):
    path = tmp_path / "clip.bin"
    path.write_bytes(b"x" * 100)
    return Artifact.from_completed_download(path)


def _wait(service, handoff_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if service.snapshot(handoff_id).is_terminal:
            return service.snapshot(handoff_id)
        time.sleep(0.02)
    raise AssertionError("handoff did not finish")


def test_send_resolves_a_securely_paired_device_from_the_trust_store(tmp_path):
    store = FriendSendTrustStore(tmp_path / "trust.json")
    store.upsert(_trusted())
    transport = _RecordingSecureLikeTransport(store)
    service = DeviceHandoffService(transport=transport)
    service.start()
    try:
        snapshot = _wait(service, service.send("dev-1", _artifact(tmp_path)))
        assert snapshot.state == HandoffState.RECEIVED
        assert transport.sent_to == [("dev-1", 41000)]
        assert service.devices() == ()  # nothing was copied into the legacy registry
    finally:
        service.stop()


def test_send_uses_the_refreshed_endpoint_from_the_trust_store(tmp_path):
    from rychlik.device.contracts import FriendSendEndpoint

    store = FriendSendTrustStore(tmp_path / "trust.json")
    trusted = _trusted(port=41000)
    store.upsert(trusted)
    store.upsert(trusted.with_endpoint(FriendSendEndpoint("192.168.1.99", 45555)))  # the phone restarted on a new port
    transport = _RecordingSecureLikeTransport(store)
    service = DeviceHandoffService(transport=transport)
    service.start()
    try:
        _wait(service, service.send("dev-1", _artifact(tmp_path)))
        assert transport.sent_to == [("dev-1", 45555)]
        assert store.get("dev-1").tls_spki_sha256 == "ab" * 32  # the pin (identity) never changed
    finally:
        service.stop()


def test_unknown_device_is_still_rejected(tmp_path):
    store = FriendSendTrustStore(tmp_path / "trust.json")
    service = DeviceHandoffService(transport=_RecordingSecureLikeTransport(store))
    service.start()
    try:
        with pytest.raises(UnknownDeviceError):
            service.send("ghost", _artifact(tmp_path))
    finally:
        service.stop()


def test_forgotten_device_can_no_longer_be_sent_to(tmp_path):
    store = FriendSendTrustStore(tmp_path / "trust.json")
    store.upsert(_trusted())
    service = DeviceHandoffService(transport=_RecordingSecureLikeTransport(store))
    service.start()
    try:
        store.remove("dev-1")
        with pytest.raises(UnknownDeviceError):
            service.send("dev-1", _artifact(tmp_path))
    finally:
        service.stop()
