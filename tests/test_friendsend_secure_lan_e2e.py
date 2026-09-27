"""Prompt A15: the real Python<->Dart secure LAN E2E suite.

    real desktop SecurePairingManager (bootstrap listener)
        <-> real Dart pairing client (bin/pairing_client_harness.dart)
    real desktop SecureFriendSendTransport
        -> real TLS (pinned SPKI)
        -> real Dart SecureFriendSendReceiverServer
           (bin/secure_receiver_harness.dart, the SAME classes the
           Flutter app ships)

No mocked transport, no mocked crypto, no Python FriendSend fixture
anywhere in this file. Skipped (with an explicit reason) if no Dart SDK
is available, exactly like the A14 cross-language suite.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time

import pytest
from rychlik.core.artifact import Artifact
from rychlik.device.contracts import (
    DeviceCapability,
    DeviceHandoffRequest,
    FriendSendDevice,
    FriendSendEndpoint,
    HandoffErrorCode,
    HandoffState,
)
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.pairing_bootstrap import SecurePairingManager
from rychlik.device.security.secure_transport import SecureFriendSendTransport
from rychlik.device.security.trust_store import FriendSendTrustStore

from friendsend_secure_dart_harness import FriendSendSecureDartHarness, run_dart_pairing_client

pytestmark = pytest.mark.skipif(
    shutil.which("dart") is None
    and not __import__("os").environ.get("FRIENDSEND_DART_EXECUTABLE")
    and not __import__("pathlib").Path(
        "/mnt/Basic_data_partition1/vojtech/flutter/bin/cache/dart-sdk/bin/dart"
    ).exists(),
    reason="no Dart SDK available for the real Python<->Dart secure LAN E2E",
)


def _wait_until(predicate, timeout=15.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _dummy_device(device_id: str) -> FriendSendDevice:
    """SecureFriendSendTransport only ever uses `device.device_id` --
    routing/pin come from the trust store -- so the other fields just
    need to satisfy the dataclass, never actually used (§117)."""
    from datetime import datetime, timezone

    return FriendSendDevice(
        device_id=device_id,
        display_name="unused",
        platform="unused",
        endpoint=FriendSendEndpoint(host="127.0.0.1", port=1),
        protocol_version=1,
        capabilities=frozenset({DeviceCapability.RECEIVE_STREAM}),
        auth_token="unused",
        paired_at_utc=datetime.now(timezone.utc),
    )


def _pair(tmp_path, *, receiver: FriendSendSecureDartHarness, identity, trust_store):
    """Pairs against `receiver` using the REAL pairing client, run
    against `receiver.identity_dir` -- the same directory that receiver
    process loaded its real TLS identity from, so the pairing client
    observes and persists the receiver's actual SPKI pin, not an
    unrelated freshly-generated one."""
    manager = SecurePairingManager(identity=identity, trust_store=trust_store)
    payload = manager.create_session()
    try:
        import json

        result = run_dart_pairing_client(
            identity_dir=receiver.identity_dir,
            device_id=receiver.device_id,
            display_name="Test Phone",
            friendsend_host=receiver.host,
            friendsend_port=receiver.port,
            payload_json_line=json.dumps(payload.to_wire_dict()),
        )
        assert result["pairing_result"] == "ok", result
    finally:
        manager.stop()
    return manager


def _artifact(tmp_path, payload: bytes):
    file_path = tmp_path / "artifact.bin"
    file_path.write_bytes(payload)
    return Artifact.from_completed_download(file_path)


def test_real_pairing_e2e_persists_mutual_trust(tmp_path):
    """§153: real desktop pairing service <-> real Dart pairing client,
    no fixture on either side."""
    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        _pair(tmp_path, receiver=receiver, identity=identity, trust_store=trust_store)

        trusted = trust_store.get(receiver.device_id)
        assert trusted is not None
        assert trusted.tls_spki_sha256 == receiver.tls_spki_sha256
    finally:
        receiver.stop()


def test_real_persistent_trust_restart_e2e(tmp_path):
    """§155: pair once, destroy both processes, reload persisted
    identities/trust, transfer again without re-pairing."""
    identity_store = DesktopIdentityStore(tmp_path / "desktop_identity")
    identity = identity_store.load_or_create()
    trust_store_path = tmp_path / "trust.json"
    trust_store = FriendSendTrustStore(trust_store_path)

    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        _pair(tmp_path, receiver=receiver, identity=identity, trust_store=trust_store)
        device_id = receiver.device_id
    finally:
        receiver.stop()  # destroy the FriendSend process entirely

    # Reload everything from disk in fresh objects (simulating a real
    # restart of both sides) -- no re-pairing.
    reloaded_identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    assert reloaded_identity.desktop_instance_id == identity.desktop_instance_id
    reloaded_trust_store = FriendSendTrustStore(trust_store_path)
    reloaded_trust_store.load()
    assert reloaded_trust_store.get(device_id) is not None

    restarted_receiver = FriendSendSecureDartHarness(tmp_path)  # fresh process, same identity_dir
    try:
        assert restarted_receiver.device_id == device_id
        trusted = reloaded_trust_store.get(device_id)
        assert restarted_receiver.tls_spki_sha256 == trusted.tls_spki_sha256

        # A restarted receiver binds a fresh ephemeral port. §59/§68: the
        # candidate endpoint may be updated ONLY after independently
        # re-verifying the SAME TLS pin at the new address (never simply
        # because the same device_id turned up somewhere new) -- this is
        # exactly what real mDNS rediscovery would trigger; simulated
        # here directly since A15's mDNS work is desktop-discovery-only
        # in this phase (no real Android NSD advertiser available).
        from rychlik.device.security.secure_transport import connect_and_verify_pin

        probe_sock = connect_and_verify_pin(restarted_receiver.host, restarted_receiver.port, trusted.tls_spki_sha256)
        probe_sock.close()
        reloaded_trust_store.upsert(
            trusted.with_endpoint(FriendSendEndpoint(host=restarted_receiver.host, port=restarted_receiver.port))
        )

        transport = SecureFriendSendTransport(identity=reloaded_identity, trust_store=reloaded_trust_store)
        payload = b"restart-proof payload " * 100
        artifact = _artifact(tmp_path, payload)
        request = DeviceHandoffRequest(
            handoff_id="restart-handoff-1",
            device_id=device_id,
            artifact_id=artifact.artifact_id,
            display_name=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size,
            sha256=artifact.sha256,
        )
        outcome = transport.send(_dummy_device(device_id), request, artifact.local_path)
        assert outcome.state == HandoffState.RECEIVED, outcome
        assert outcome.bytes_sent == len(payload)
    finally:
        restarted_receiver.stop()


def test_real_secure_handoff_e2e(tmp_path):
    """§154: full secure handoff after pairing -- TLS + pin + challenge
    + Ed25519 auth + real streamed Artifact -> RECEIVED."""
    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        _pair(tmp_path, receiver=receiver, identity=identity, trust_store=trust_store)

        transport = SecureFriendSendTransport(identity=identity, trust_store=trust_store)
        payload = b"secure handoff payload " * 5000
        artifact = _artifact(tmp_path, payload)
        request = DeviceHandoffRequest(
            handoff_id="secure-handoff-1",
            device_id=receiver.device_id,
            artifact_id=artifact.artifact_id,
            display_name=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size,
            sha256=artifact.sha256,
        )
        outcome = transport.send(_dummy_device(receiver.device_id), request, artifact.local_path)
        assert outcome.state == HandoffState.RECEIVED, outcome
        assert outcome.bytes_sent == len(payload)

        received_event = receiver.wait_for_event("received", handoff_id="secure-handoff-1")
        assert received_event["sha256"] == artifact.sha256
    finally:
        receiver.stop()


def test_real_wrong_pin_e2e_sends_zero_payload(tmp_path):
    """§100/§156: trust store expects pin A, real server presents a
    DIFFERENT real TLS identity (key B) -- connection must be rejected
    before any payload byte, no automatic re-pin."""
    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    receiver = FriendSendSecureDartHarness(tmp_path)  # a real server with its OWN real pin
    try:
        from datetime import datetime, timezone
        from rychlik.device.security.trust_store import (
            SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1,
            TrustedFriendSendDevice,
        )

        # Trust record deliberately carries the WRONG pin (64 zeros can
        # never be a real SHA-256 of anything reachable here).
        trust_store.upsert(
            TrustedFriendSendDevice(
                device_id=receiver.device_id,
                display_name="Test Phone",
                tls_spki_sha256="0" * 64,
                protocol_version=1,
                security_profile=SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1,
                endpoint_host=receiver.host,
                endpoint_port=receiver.port,
                paired_at_utc=datetime.now(timezone.utc).isoformat(),
            )
        )

        transport = SecureFriendSendTransport(identity=identity, trust_store=trust_store)
        artifact = _artifact(tmp_path, b"should never be sent")
        request = DeviceHandoffRequest(
            handoff_id="wrong-pin-handoff",
            device_id=receiver.device_id,
            artifact_id=artifact.artifact_id,
            display_name=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size,
            sha256=artifact.sha256,
        )
        outcome = transport.send(_dummy_device(receiver.device_id), request, artifact.local_path)
        assert outcome.state == HandoffState.FAILED
        assert outcome.failure_code == HandoffErrorCode.TLS_PIN_MISMATCH
        assert outcome.bytes_sent == 0
    finally:
        receiver.stop()


def test_real_wrong_signature_e2e_sends_zero_payload(tmp_path):
    """§102/§157: a desktop identity DIFFERENT from the one paired signs
    the request -- real Ed25519 verification on the real Dart receiver
    must reject it before any payload byte."""
    real_identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    impostor_identity = DesktopIdentityStore(tmp_path / "impostor_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        _pair(tmp_path, receiver=receiver, identity=real_identity, trust_store=trust_store)

        # Use the IMPOSTOR's identity to sign, but the trust store still
        # only knows about `real_identity`'s desktop_instance_id -- a
        # signature from a different key for the SAME desktop_instance_id
        # is what "wrong signing key" actually looks like on the wire, so
        # build a transport whose identity object carries the real
        # desktop_instance_id but the impostor's private key.
        from rychlik.device.security.identity import DesktopIdentity

        forged_identity = DesktopIdentity(
            desktop_instance_id=real_identity.desktop_instance_id,
            private_key=impostor_identity.private_key,
            public_key_bytes=impostor_identity.public_key_bytes,
        )
        transport = SecureFriendSendTransport(identity=forged_identity, trust_store=trust_store)
        artifact = _artifact(tmp_path, b"should never be sent")
        request = DeviceHandoffRequest(
            handoff_id="wrong-sig-handoff",
            device_id=receiver.device_id,
            artifact_id=artifact.artifact_id,
            display_name=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size,
            sha256=artifact.sha256,
        )
        outcome = transport.send(_dummy_device(receiver.device_id), request, artifact.local_path)
        assert outcome.state == HandoffState.FAILED
        assert outcome.failure_code == HandoffErrorCode.AUTHENTICATION_FAILED
        assert outcome.bytes_sent == 0
    finally:
        receiver.stop()


def test_real_auth_replay_e2e(tmp_path):
    """§103/§158: capturing a valid signed challenge response and
    replaying it for a second handoff must fail -- proven here by
    reusing SecureFriendSendTransport's own internal connect+challenge
    flow twice against the SAME challenge via the low-level helpers."""
    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        _pair(tmp_path, receiver=receiver, identity=identity, trust_store=trust_store)

        from rychlik.device.security.canonical import auth_signature_input
        from rychlik.device.security.secure_transport import connect_and_verify_pin
        from rychlik.device.security.minimal_http import read_json_response, send_request
        from base64 import b64decode, b64encode
        import hashlib

        trusted = trust_store.get(receiver.device_id)
        sock = connect_and_verify_pin(trusted.endpoint_host, trusted.endpoint_port, trusted.tls_spki_sha256)
        reader = sock.makefile("rb")
        send_request(sock, "GET", "/auth/challenge", {})
        _, challenge_body = read_json_response(reader)
        challenge_id = challenge_body["challenge_id"]
        nonce = b64decode(challenge_body["nonce"])

        artifact = _artifact(tmp_path, b"replay test payload")
        sig_input = auth_signature_input(
            security_profile="pinned-tls-signature-v1",
            protocol_version=1,
            desktop_instance_id=identity.desktop_instance_id,
            device_id=receiver.device_id,
            challenge_id=challenge_id,
            challenge_nonce=nonce,
            handoff_id="replay-handoff-1",
            artifact_sha256=bytes.fromhex(artifact.sha256),
            artifact_size_bytes=artifact.size,
        )
        signature = identity.sign(sig_input)
        headers = {
            "Content-Type": "application/json",
            "X-FriendSend-Desktop-Id": identity.desktop_instance_id,
            "X-FriendSend-Challenge-Id": challenge_id,
            "X-FriendSend-Signature": b64encode(signature).decode(),
        }
        import json as _json

        body = _json.dumps(
            {
                "handoff_id": "replay-handoff-1",
                "display_name": "x",
                "mime_type": "application/octet-stream",
                "size_bytes": artifact.size,
                "sha256": artifact.sha256,
            }
        ).encode()
        send_request(sock, "POST", "/handoff/offer", headers, body)
        status1, body1 = read_json_response(reader)
        assert status1 == 200 and body1.get("accepted") is True
        sock.close()

        # Now REPLAY the exact same challenge_id + signature on a fresh
        # connection for a second, different handoff.
        sock2 = connect_and_verify_pin(trusted.endpoint_host, trusted.endpoint_port, trusted.tls_spki_sha256)
        reader2 = sock2.makefile("rb")
        body2_json = _json.dumps(
            {
                "handoff_id": "replay-handoff-2",
                "display_name": "x",
                "mime_type": "application/octet-stream",
                "size_bytes": artifact.size,
                "sha256": artifact.sha256,
            }
        ).encode()
        send_request(sock2, "POST", "/handoff/offer", headers, body2_json)
        status2, body2 = read_json_response(reader2)
        sock2.close()
        assert status2 == 400
        assert body2.get("error_code") == "AUTH_REPLAY"
    finally:
        receiver.stop()


def test_real_tls_wire_encryption_e2e(tmp_path):
    """§99/§161: a real TCP relay proxy sits between the real Python
    sender and the real Dart receiver -- the sender is pointed at the
    proxy, which forwards raw bytes without terminating TLS. A
    distinctive plaintext marker in the media payload must never appear
    in the bytes actually observed on the wire."""
    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    receiver = FriendSendSecureDartHarness(tmp_path)
    captured = bytearray()
    proxy_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    proxy_server.bind(("127.0.0.1", 0))
    proxy_server.listen(1)
    proxy_port = proxy_server.getsockname()[1]
    stop_proxy = threading.Event()

    def _relay(src, dst, record=False):
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                if record:
                    captured.extend(chunk)
                dst.sendall(chunk)
        except OSError:
            pass

    def _proxy_loop():
        proxy_server.settimeout(1.0)
        while not stop_proxy.is_set():
            try:
                client_sock, _ = proxy_server.accept()
            except socket.timeout:
                continue
            except OSError:
                # The listening socket was closed from the main thread
                # during teardown while accept() was blocked -- expected
                # at shutdown, not a real failure.
                break
            upstream = socket.create_connection((receiver.host, receiver.port))
            t1 = threading.Thread(target=_relay, args=(client_sock, upstream, True), daemon=True)
            t2 = threading.Thread(target=_relay, args=(upstream, client_sock, False), daemon=True)
            t1.start()
            t2.start()

    proxy_thread = threading.Thread(target=_proxy_loop, daemon=True)
    proxy_thread.start()

    try:
        _pair(tmp_path, receiver=receiver, identity=identity, trust_store=trust_store)

        # Redirect the trust record's endpoint through the proxy --
        # the TLS session is still end-to-end between the Python client
        # and the real Dart server; the proxy only relays raw bytes.
        trusted = trust_store.get(receiver.device_id)
        from rychlik.device.security.trust_store import TrustedFriendSendDevice

        trust_store.upsert(
            TrustedFriendSendDevice(
                device_id=trusted.device_id,
                display_name=trusted.display_name,
                tls_spki_sha256=trusted.tls_spki_sha256,
                protocol_version=trusted.protocol_version,
                security_profile=trusted.security_profile,
                endpoint_host="127.0.0.1",
                endpoint_port=proxy_port,
                paired_at_utc=trusted.paired_at_utc,
            )
        )

        marker = b"FRIENDSEND_SECRET_MEDIA_MARKER_" + b"Z" * 200
        artifact = _artifact(tmp_path, marker)
        transport = SecureFriendSendTransport(identity=identity, trust_store=trust_store)
        request = DeviceHandoffRequest(
            handoff_id="wire-encryption-handoff",
            device_id=receiver.device_id,
            artifact_id=artifact.artifact_id,
            display_name=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size,
            sha256=artifact.sha256,
        )
        outcome = transport.send(_dummy_device(receiver.device_id), request, artifact.local_path)
        assert outcome.state == HandoffState.RECEIVED, outcome

        assert b"FRIENDSEND_SECRET_MEDIA_MARKER_" not in bytes(captured)
        assert len(captured) > 0  # sanity: the proxy actually saw traffic
    finally:
        stop_proxy.set()
        proxy_server.close()
        receiver.stop()


def test_real_mdns_spoof_combined_with_tls_pin_e2e(tmp_path):
    """§110/§160: a real mDNS advertisement claims the SAME device_id as
    an already-trusted device, but points at a DIFFERENT real endpoint
    (a second, unrelated real Dart secure receiver with its own real TLS
    identity -- not a fake/mock listener). Discovery must find it
    (proving discovery itself is not the failure point), but the SPKI
    pin check must reject it before any payload byte, proving discovery
    alone never substitutes for the TLS pin as the actual trust anchor."""
    from zeroconf import ServiceInfo, Zeroconf

    from rychlik.device.security.discovery import SERVICE_TYPE, FriendSendDiscoveryService

    identity_dir = tmp_path / "identity"
    identity_dir.mkdir()
    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")

    real_receiver = FriendSendSecureDartHarness(tmp_path)
    impostor_receiver = FriendSendSecureDartHarness(tmp_path / "impostor")
    try:
        _pair(tmp_path, receiver=real_receiver, identity=identity, trust_store=trust_store)
        trusted = trust_store.get(real_receiver.device_id)
        assert trusted.tls_spki_sha256 != impostor_receiver.tls_spki_sha256  # genuinely different identities

        # A spoofed mDNS advertisement: claims the TRUSTED device_id, but
        # actually points at the impostor's real (different) endpoint.
        zc = Zeroconf()
        spoof_info = ServiceInfo(
            SERVICE_TYPE,
            f"{real_receiver.device_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton("127.0.0.1")],
            port=impostor_receiver.port,
            properties={
                "device_id": real_receiver.device_id,
                "protocol_version": "1",
                "security_profile": "pinned-tls-signature-v1",
            },
        )
        zc.register_service(spoof_info)

        discovered = []
        discovery = FriendSendDiscoveryService()
        discovery.subscribe(on_found=discovered.append)
        discovery.start()
        try:
            assert _wait_until(lambda: any(d.device_id == real_receiver.device_id for d in discovered), timeout=15)
            spoofed = next(d for d in discovered if d.device_id == real_receiver.device_id)
            assert spoofed.endpoint.port == impostor_receiver.port  # discovery DID find the spoof

            # The application must never trust the discovered endpoint
            # directly -- sending still goes through the persisted trust
            # record's pin, which still (correctly) points at the real
            # endpoint's real pin, so connecting to the SPOOFED endpoint
            # via the pin-verified path must fail.
            from rychlik.device.security.secure_transport import TlsPinMismatchError, connect_and_verify_pin

            with pytest.raises(TlsPinMismatchError):
                sock = connect_and_verify_pin(
                    spoofed.endpoint.host, spoofed.endpoint.port, trusted.tls_spki_sha256
                )
                sock.close()
        finally:
            discovery.stop()
            zc.unregister_service(spoof_info)
            zc.close()
    finally:
        real_receiver.stop()
        impostor_receiver.stop()
