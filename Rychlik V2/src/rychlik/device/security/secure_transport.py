"""The production Device Mode transport after Prompt A15:
`pinned-tls-signature-v1` -- TLS with mandatory SPKI-pin verification
before ANY application byte, followed by Ed25519 desktop authentication,
then the same offer/stream Protocol v1 exchange (A13/A14) over that now
trusted, encrypted channel.

Implements the same `FriendSendTransport` ABC as A13's
`HttpFriendSendTransport` (see rychlik.device.transport) so it composes
directly into `DeviceHandoffService` -- callers do not need to know which
transport a given trusted device uses (§117 of the A15 prompt).
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import threading
import time
from base64 import b64decode, b64encode
from pathlib import Path

from rychlik.device.contracts import (
    FriendSendDevice,
    HandoffErrorCode,
    HandoffState,
)
from rychlik.device.security.canonical import auth_signature_input
from rychlik.device.security.identity import DesktopIdentity
from rychlik.device.security.minimal_http import read_json_response, send_request, send_request_head_only
from rychlik.device.security.trust_store import FriendSendTrustStore
from rychlik.device.transport import FriendSendTransport, ProbeResult, SendOutcome, TransportCancelled

_DEFAULT_CHUNK_SIZE = 256 * 1024
_CONNECT_TIMEOUT = 10
SECURITY_PROFILE = "pinned-tls-signature-v1"
PROTOCOL_VERSION = 1


class TlsPinMismatchError(Exception):
    """Raised internally the moment a peer's certificate SPKI does not
    match the persisted pin -- caught by `send()`/`probe()`, never
    allowed to send application bytes (§38/§97)."""


def _spki_sha256_from_der_cert(der_cert: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_der_x509_certificate(der_cert)
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def connect_and_verify_pin(host: str, port: int, expected_spki_sha256: str, *, timeout: float = _CONNECT_TIMEOUT) -> ssl.SSLSocket:
    """§38/§39/§40: connects TLS, obtains the peer certificate, computes
    its SPKI SHA-256, and compares it to the persisted pin -- BEFORE
    returning a usable socket to any caller that might send a request.
    `verify=False`-and-check-afterward is exactly what this function
    exists to avoid (§39/§80): the raw socket is never handed back if
    the pin does not match.

    This deliberately never validates the certificate against a CA
    chain (there is no CA here -- FriendSend's certificate is
    self-signed, §7) and never checks hostname; SPKI pinning IS the
    entire trust model for a paired device (§8/§9/§40)."""
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    # `create_connection`'s timeout only covers the TCP connect step --
    # without re-arming it, the TLS handshake below would block forever
    # against an unresponsive/misbehaving peer instead of failing fast.
    raw_sock.settimeout(timeout)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    tls_sock = context.wrap_socket(raw_sock, server_hostname=host)
    tls_sock.settimeout(timeout)
    try:
        der_cert = tls_sock.getpeercert(binary_form=True)
        if der_cert is None:
            raise TlsPinMismatchError("no peer certificate presented")
        actual_pin = _spki_sha256_from_der_cert(der_cert)
        if actual_pin != expected_spki_sha256:
            raise TlsPinMismatchError(f"expected {expected_spki_sha256}, got {actual_pin}")
    except Exception:
        tls_sock.close()
        raise
    return tls_sock


class SecureFriendSendTransport(FriendSendTransport):
    def __init__(self, *, identity: DesktopIdentity, trust_store: FriendSendTrustStore) -> None:
        self._identity = identity
        self._trust_store = trust_store

    @property
    def trust_store(self) -> FriendSendTrustStore:
        """Exposed read-only so other Device Mode components (e.g. the
        Prompt A16 media capability query) can resolve a trusted device's
        pin/endpoint using this transport's own trust store, without a
        second, possibly-inconsistent one."""
        return self._trust_store

    def probe(self, endpoint) -> ProbeResult:
        raise NotImplementedError(
            "SecureFriendSendTransport.probe() is not part of the A15 acceptance gate; "
            "device discovery/pin bootstrapping happens through SecurePairingManager instead."
        )

    def send(
        self,
        device: FriendSendDevice,
        request,
        artifact_path: Path,
        *,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        progress_callback=None,
        cancel_event: threading.Event | None = None,
        chunk_delay: float = 0.0,
    ) -> SendOutcome:
        trusted = self._trust_store.get(device.device_id)
        if trusted is None:
            return SendOutcome(HandoffState.FAILED, 0, HandoffErrorCode.UNKNOWN_DEVICE)

        try:
            sock = connect_and_verify_pin(trusted.endpoint_host, trusted.endpoint_port, trusted.tls_spki_sha256)
        except TlsPinMismatchError:
            return SendOutcome(HandoffState.FAILED, 0, HandoffErrorCode.TLS_PIN_MISMATCH)
        except OSError:
            return SendOutcome(HandoffState.FAILED, 0, HandoffErrorCode.CONNECTION_FAILED)

        reader = sock.makefile("rb")
        sent = 0
        try:
            # --- challenge (§42-45) ------------------------------------------
            send_request(sock, "GET", "/auth/challenge", {})
            status, challenge_body = read_json_response(reader)
            if status != 200:
                return SendOutcome(HandoffState.FAILED, 0, HandoffErrorCode.CONNECTION_FAILED)
            challenge_id = challenge_body["challenge_id"]
            challenge_nonce = b64decode(challenge_body["nonce"])

            # --- sign (§46/§47) ------------------------------------------------
            sig_input = auth_signature_input(
                security_profile=SECURITY_PROFILE,
                protocol_version=PROTOCOL_VERSION,
                desktop_instance_id=self._identity.desktop_instance_id,
                device_id=device.device_id,
                challenge_id=challenge_id,
                challenge_nonce=challenge_nonce,
                handoff_id=request.handoff_id,
                artifact_sha256=bytes.fromhex(request.sha256),
                artifact_size_bytes=request.size_bytes,
            )
            signature = self._identity.sign(sig_input)

            # --- offer (§48/§96-98) ---------------------------------------------
            offer_headers = {
                "Content-Type": "application/json",
                "X-FriendSend-Desktop-Id": self._identity.desktop_instance_id,
                "X-FriendSend-Challenge-Id": challenge_id,
                "X-FriendSend-Signature": b64encode(signature).decode(),
            }
            import json as _json

            offer_body = _json.dumps(
                {
                    "handoff_id": request.handoff_id,
                    "display_name": request.display_name,
                    "mime_type": request.mime_type,
                    "size_bytes": request.size_bytes,
                    "sha256": request.sha256,
                    "preferred_filename": request.preferred_filename,
                }
            ).encode()
            send_request(sock, "POST", "/handoff/offer", offer_headers, offer_body)
            status, offer_response = read_json_response(reader)
            if status != 200 or not offer_response.get("accepted"):
                code = _error_code_from_wire(offer_response)
                return SendOutcome(HandoffState.FAILED, 0, code)

            # --- stream (§50-52/§97-98) -----------------------------------------
            hasher = hashlib.sha256()
            sent = 0
            stream_headers = {
                "Content-Type": request.mime_type,
                "Content-Length": str(request.size_bytes),
                "X-FriendSend-Handoff-Id": request.handoff_id,
            }
            send_request_head_only(sock, "POST", "/handoff/stream", stream_headers)
            with artifact_path.open("rb") as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise TransportCancelled()
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    sent += len(chunk)
                    sock.sendall(chunk)
                    if progress_callback is not None:
                        progress_callback(sent, request.size_bytes)
                    if chunk_delay:
                        time.sleep(chunk_delay)

            status, stream_response = read_json_response(reader)
        except TransportCancelled:
            try:
                sock.close()
            except OSError:
                pass
            return SendOutcome(HandoffState.CANCELLED, sent)
        except (OSError, ConnectionError, ValueError, KeyError):
            return SendOutcome(HandoffState.FAILED, 0, HandoffErrorCode.CONNECTION_FAILED)
        finally:
            try:
                reader.close()
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

        if hasher.hexdigest() != request.sha256:
            return SendOutcome(HandoffState.FAILED, sent, HandoffErrorCode.INTEGRITY_MISMATCH)

        if status == 200 and stream_response.get("state") == "RECEIVED":
            return SendOutcome(HandoffState.RECEIVED, sent)
        code = _error_code_from_wire(stream_response)
        return SendOutcome(HandoffState.FAILED, sent, code)


_ERROR_CODE_BY_NAME = {code.value: code for code in HandoffErrorCode}


def _error_code_from_wire(body: dict) -> HandoffErrorCode:
    return _ERROR_CODE_BY_NAME.get(body.get("error_code"), HandoffErrorCode.RECEIVER_REJECTED)
