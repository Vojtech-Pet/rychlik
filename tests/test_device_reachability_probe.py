"""A17-STALE-NSD-ONLINE: mDNS presence alone must never mean Online; a real pinned-TLS probe decides."""

from __future__ import annotations

import datetime
import hashlib
import socket
import ssl
import threading
from datetime import timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from rychlik.device.contracts import FriendSendEndpoint
from rychlik.device.desktop_devices import DesktopDeviceDirectory, DeviceState
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.secure_transport import connect_and_verify_pin
from rychlik.device.security.trust_store import FriendSendTrustStore, TrustedFriendSendDevice

PROFILE = "pinned-tls-signature-v1"


def _make_cert(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "friendsend-test")])
    now = datetime.datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert_path, key_path, hashlib.sha256(spki).hexdigest()


class _TlsListener:
    """A real TLS listener (the stand-in for FriendSend's receiver socket); stop() makes the port refuse connections."""

    def __init__(self, cert_path, key_path) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        self._raw = socket.socket()
        self._raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._raw.bind(("127.0.0.1", 0))
        self._raw.listen(8)
        self.port = self._raw.getsockname()[1]
        self._context = context
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._raw.accept()
            except OSError:
                return
            try:
                self._context.wrap_socket(conn, server_side=True).close()
            except (ssl.SSLError, OSError):
                conn.close()

    def stop(self) -> None:
        self._running = False
        try:
            self._raw.shutdown(socket.SHUT_RDWR)  # wakes the blocked accept(); close() alone leaves the port listening
        except OSError:
            pass
        self._raw.close()
        self._thread.join(timeout=2)


def _probe(trusted: TrustedFriendSendDevice) -> bool:
    connect_and_verify_pin(trusted.endpoint_host, trusted.endpoint_port, trusted.tls_spki_sha256, timeout=2.0).close()
    return True


def _directory(tmp_path, spki, port):
    store = FriendSendTrustStore(tmp_path / "trust.json")
    store.upsert(TrustedFriendSendDevice(device_id="d1", display_name="Pixel", tls_spki_sha256=spki, protocol_version=1, security_profile=PROFILE,
                                         endpoint_host="127.0.0.1", endpoint_port=port, paired_at_utc=datetime.datetime.now(timezone.utc).isoformat()))
    return DesktopDeviceDirectory(store, probe=_probe, probe_async=False), store


def _discover(directory, port):
    directory.on_discovered(DiscoveredFriendSendDevice("d1", FriendSendEndpoint("127.0.0.1", port), 1, PROFILE))


def _state(directory):
    return directory.row_for("d1").state


def test_mdns_record_plus_listener_alive_is_online(tmp_path):
    cert, key, spki = _make_cert(tmp_path)
    listener = _TlsListener(cert, key)
    try:
        directory, _ = _directory(tmp_path, spki, listener.port)
        _discover(directory, listener.port)
        assert _state(directory) == DeviceState.TRUSTED_ONLINE
    finally:
        listener.stop()


def test_mdns_record_plus_closed_port_is_not_online(tmp_path):
    cert, key, spki = _make_cert(tmp_path)
    listener = _TlsListener(cert, key)
    port = listener.port
    listener.stop()  # the record outlives the receiver, exactly the stale-NSD case seen on the phone
    directory, _ = _directory(tmp_path, spki, port)
    _discover(directory, port)
    assert _state(directory) == DeviceState.TRUSTED_OFFLINE
    assert not directory.row_for("d1").can_send


def test_discovered_but_not_yet_probed_is_checking_not_online(tmp_path):
    store = FriendSendTrustStore(tmp_path / "trust.json")
    store.upsert(TrustedFriendSendDevice(device_id="d1", display_name="Pixel", tls_spki_sha256="ab" * 32, protocol_version=1, security_profile=PROFILE,
                                         endpoint_host="127.0.0.1", endpoint_port=1, paired_at_utc="2026-01-01T00:00:00+00:00"))
    gate = threading.Event()
    directory = DesktopDeviceDirectory(store, probe=lambda t: gate.wait(5), probe_async=True)
    _discover(directory, 1)
    assert _state(directory) == DeviceState.TRUSTED_CHECKING and not directory.row_for("d1").can_send
    gate.set()


def test_online_then_listener_disappears_degrades_on_next_probe_and_on_failed_send(tmp_path):
    cert, key, spki = _make_cert(tmp_path)
    listener = _TlsListener(cert, key)
    directory, _ = _directory(tmp_path, spki, listener.port)
    _discover(directory, listener.port)
    assert _state(directory) == DeviceState.TRUSTED_ONLINE
    listener.stop()  # the phone left FriendSend; its mDNS record is still there
    assert _state(directory) == DeviceState.TRUSTED_ONLINE  # nothing has noticed yet ...
    directory.probe_all()  # ... the next periodic probe does
    assert _state(directory) == DeviceState.TRUSTED_OFFLINE
    listener2 = _TlsListener(cert, key)
    try:
        directory._trust_store.upsert(directory._trust_store.get("d1").with_endpoint(FriendSendEndpoint("127.0.0.1", listener2.port)))
        _discover(directory, listener2.port)  # the phone came back (new endpoint): probed again, Online again
        assert _state(directory) == DeviceState.TRUSTED_ONLINE
        directory.mark_unreachable("d1")  # a real send hit CONNECTION_FAILED
        assert _state(directory) == DeviceState.TRUSTED_OFFLINE
        directory.probe_all()
        assert _state(directory) == DeviceState.TRUSTED_ONLINE  # and it recovers once reachable
    finally:
        listener2.stop()


def test_wrong_certificate_at_the_endpoint_is_identity_changed_never_online(tmp_path):
    cert, key, _ = _make_cert(tmp_path)
    listener = _TlsListener(cert, key)
    try:
        directory, _ = _directory(tmp_path, "cd" * 32, listener.port)  # pinned identity differs from what answers
        _discover(directory, listener.port)
        assert _state(directory) == DeviceState.IDENTITY_CHANGED
    finally:
        listener.stop()
