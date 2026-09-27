"""Real desktop-side pairing bootstrap listener (Prompt A15 §17/§28-35).

A15 needs a genuine wire callback for pairing completion -- Protocol v1
(A13/A14) never mandated one, since no second peer existed to validate
it against. This module is that callback: a short-lived, plain-HTTP
listener the desktop opens only while a pairing session is pending
(§17), which FriendSend connects to (having learned this address from
the `PairingPayload` the user copied to the phone) to complete a
two-round-trip, mutually-authenticated trust exchange:

    FriendSend -> POST /pairing/offer   {transcript fields + proof_a}
    Desktop    -> verifies proof_a, STAGES (does not yet persist) trust,
                  responds {proof_b}
    FriendSend -> verifies proof_b, PERSISTS TrustedDesktop, THEN:
    FriendSend -> POST /pairing/confirm {pairing_session_id}
    Desktop    -> NOW persists TrustedFriendSendDevice, session CONSUMED

This ordering (§34/§35) means a connection dropped at any point before
the desktop receives /pairing/confirm leaves the desktop side with NO
persisted trust -- restart-safe, no half-pairing.

Plain HTTP is used here deliberately and only for this bootstrap
exchange (§33): the one-time pairing secret itself never crosses this
wire (only HMAC proofs of it do), a substituted identity is caught by
the transcript HMAC binding both public keys, and no media/private
payload is ever sent over this listener.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rychlik.device.contracts import FriendSendEndpoint
from rychlik.device.security.canonical import PAIRING_PROOF_A_DOMAIN, PAIRING_PROOF_B_DOMAIN, pairing_transcript
from rychlik.device.security.identity import DesktopIdentity
from rychlik.device.security.trust_store import (
    SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1,
    FriendSendTrustStore,
    TrustedFriendSendDevice,
)

FRIENDSEND_PROTOCOL_VERSION = 1
DEFAULT_PAIRING_TTL_SECONDS = 300  # §27
_NONCE_BYTES = 32  # 256 bits, §25


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _best_effort_local_ipv4() -> str:
    """A deterministic, best-effort local IPv4 selection (A14 prompt
    §24's allowance applies here too) -- never returns the unroutable
    "0.0.0.0" a socket may have been bound to."""
    import socket as _socket

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))  # no packet actually sent (UDP)
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class PairingSecurityError(Exception):
    """Base for all bootstrap pairing failures -- never a raw stack
    trace crosses the wire."""


class UnknownPairingSessionError(PairingSecurityError):
    pass


class PairingProofMismatchError(PairingSecurityError):
    pass


class PairingSessionExpiredError(PairingSecurityError):
    pass


@dataclass
class _SessionRecord:
    secret: str
    desktop_nonce: bytes
    expires_at_utc: datetime
    state: str  # PENDING | STAGED | CONSUMED
    staged_device: TrustedFriendSendDevice | None = None


@dataclass(frozen=True)
class PairingBootstrapPayload:
    """What the user copies from desktop to FriendSend (§28's transcript
    fields the desktop side already knows)."""

    protocol_version: int
    security_profile: str
    pairing_session_id: str
    desktop_instance_id: str
    desktop_public_signing_key: bytes
    desktop_endpoint: FriendSendEndpoint
    desktop_nonce: bytes
    secret: str  # the OOB bootstrap secret (§24) -- carried only in this
    # user-copied payload, NEVER separately transmitted over the network
    # (§24/§163); redacted() below exists for any future logging path.
    expires_at_utc: datetime

    def to_wire_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "security_profile": self.security_profile,
            "pairing_session_id": self.pairing_session_id,
            "desktop_instance_id": self.desktop_instance_id,
            "desktop_public_signing_key": base64.b64encode(self.desktop_public_signing_key).decode(),
            "desktop_endpoint": {"host": self.desktop_endpoint.host, "port": self.desktop_endpoint.port},
            "desktop_nonce": base64.b64encode(self.desktop_nonce).decode(),
            "secret": self.secret,
            "expires_at_utc": self.expires_at_utc.isoformat(),
        }

    def redacted(self) -> dict:
        data = self.to_wire_dict()
        data["secret"] = "***REDACTED***"
        return data


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence test output
        pass

    def do_POST(self):
        if self.path == "/pairing/offer":
            self._handle_offer()
        elif self.path == "/pairing/confirm":
            self._handle_confirm()
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _handle_offer(self):
        manager: SecurePairingManager = self.server.manager  # type: ignore[attr-defined]
        try:
            body = self._read_json()
            proof_b_hex = manager._handle_offer(body)
            self._send_json(200, {"accepted": True, "proof_b": proof_b_hex})
        except UnknownPairingSessionError:
            self._send_json(404, {"error_code": "UNKNOWN_PAIRING_SESSION"})
        except PairingSessionExpiredError:
            self._send_json(400, {"error_code": "PAIRING_EXPIRED"})
        except PairingProofMismatchError:
            self._send_json(401, {"error_code": "AUTHENTICATION_FAILED"})
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"error_code": "RECEIVER_REJECTED"})

    def _handle_confirm(self):
        manager: SecurePairingManager = self.server.manager  # type: ignore[attr-defined]
        try:
            body = self._read_json()
            manager._handle_confirm(body.get("pairing_session_id"))
            self._send_json(200, {"trusted": True})
        except UnknownPairingSessionError:
            self._send_json(404, {"error_code": "UNKNOWN_PAIRING_SESSION"})
        except PairingSessionExpiredError:
            self._send_json(400, {"error_code": "PAIRING_EXPIRED"})
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"error_code": "RECEIVER_REJECTED"})

    def _send_json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (ConnectionError, OSError):
            pass


class SecurePairingManager:
    """Owns the desktop's short-lived pairing bootstrap listener. One
    instance may run multiple sessions sequentially; a fresh
    `create_session()` call reuses the listener if already running."""

    def __init__(
        self,
        *,
        identity: DesktopIdentity,
        trust_store: FriendSendTrustStore,
        bind_host: str = "127.0.0.1",
        ttl_seconds: float = DEFAULT_PAIRING_TTL_SECONDS,
        clock=_utc_now,
    ) -> None:
        self._identity = identity
        self._trust_store = trust_store
        self._bind_host = bind_host
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionRecord] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bootstrap_endpoint(self) -> FriendSendEndpoint:
        assert self._server is not None
        _, port = self._server.server_address[:2]
        if self._bind_host != "0.0.0.0":
            host = self._bind_host
        else:
            # "0.0.0.0" (all interfaces) is what we bound to, but it is
            # not a connectable destination -- advertise a real,
            # best-effort local address instead (A14 prompt §24's
            # "deterministic best-effort local IPv4 selection is
            # acceptable" applies equally here; real multi-interface/LAN
            # discovery is A15/A16 future work, not this bootstrap step).
            host = _best_effort_local_ipv4()
        return FriendSendEndpoint(host=host, port=port)

    def _ensure_listening(self) -> None:
        if self._server is not None:
            return
        server = ThreadingHTTPServer((self._bind_host, 0), _Handler)
        server.manager = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def create_session(self) -> PairingBootstrapPayload:
        self._ensure_listening()
        session_id = str(uuid.uuid4())
        secret = secrets.token_hex(16)  # 128 bits, §25
        desktop_nonce = secrets.token_bytes(_NONCE_BYTES)
        expires_at = self._clock() + timedelta(seconds=self._ttl_seconds)
        with self._lock:
            self._sessions[session_id] = _SessionRecord(
                secret=secret, desktop_nonce=desktop_nonce, expires_at_utc=expires_at, state="PENDING"
            )
        endpoint = self.bootstrap_endpoint
        return PairingBootstrapPayload(
            protocol_version=FRIENDSEND_PROTOCOL_VERSION,
            security_profile=SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1,
            pairing_session_id=session_id,
            desktop_instance_id=self._identity.desktop_instance_id,
            desktop_public_signing_key=self._identity.public_key_bytes,
            desktop_endpoint=endpoint,
            desktop_nonce=desktop_nonce,
            secret=secret,
            expires_at_utc=expires_at,
        )

    # --- called from the HTTP handler (server thread) ----------------------

    def _handle_offer(self, body: dict) -> str:
        session_id = body["pairing_session_id"]
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise UnknownPairingSessionError(session_id)
            if record.state != "PENDING":
                raise UnknownPairingSessionError(session_id)  # already offered/consumed -- no replay (§26)
            if self._clock() > record.expires_at_utc:
                del self._sessions[session_id]
                raise PairingSessionExpiredError(session_id)

            device_id = body["device_id"]
            friendsend_display_name = body["friendsend_display_name"]
            friendsend_tls_spki_sha256 = bytes.fromhex(body["friendsend_tls_spki_sha256"])
            endpoint = FriendSendEndpoint(
                host=body["friendsend_endpoint"]["host"], port=body["friendsend_endpoint"]["port"]
            )
            device_nonce = base64.b64decode(body["device_nonce"])
            protocol_version = body["protocol_version"]
            security_profile = body["security_profile"]
            proof_a = bytes.fromhex(body["proof_a"])

            transcript = pairing_transcript(
                security_profile=security_profile,
                protocol_version=protocol_version,
                pairing_session_id=session_id,
                desktop_instance_id=self._identity.desktop_instance_id,
                desktop_public_signing_key=self._identity.public_key_bytes,
                desktop_nonce=record.desktop_nonce,
                device_id=device_id,
                friendsend_display_name=friendsend_display_name,
                friendsend_tls_spki_sha256=friendsend_tls_spki_sha256,
                friendsend_endpoint_host=endpoint.host,
                friendsend_endpoint_port=endpoint.port,
                device_nonce=device_nonce,
            )

            import hashlib
            import hmac as hmac_mod

            expected_proof_a = hmac_mod.new(record.secret.encode(), PAIRING_PROOF_A_DOMAIN + transcript, hashlib.sha256).digest()
            if not hmac_mod.compare_digest(proof_a, expected_proof_a):
                raise PairingProofMismatchError(session_id)

            proof_b = hmac_mod.new(record.secret.encode(), PAIRING_PROOF_B_DOMAIN + transcript, hashlib.sha256).digest()

            record.staged_device = TrustedFriendSendDevice(
                device_id=device_id,
                display_name=friendsend_display_name,
                tls_spki_sha256=friendsend_tls_spki_sha256.hex(),
                protocol_version=protocol_version,
                security_profile=security_profile,
                endpoint_host=endpoint.host,
                endpoint_port=endpoint.port,
                paired_at_utc=self._clock().isoformat(),
            )
            record.state = "STAGED"  # §34: NOT yet persisted
            return proof_b.hex()

    def _handle_confirm(self, session_id: str | None) -> None:
        if not session_id:
            raise UnknownPairingSessionError("missing pairing_session_id")
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise UnknownPairingSessionError(session_id)
            if record.state != "STAGED" or record.staged_device is None:
                raise UnknownPairingSessionError(session_id)
            if self._clock() > record.expires_at_utc:
                del self._sessions[session_id]
                raise PairingSessionExpiredError(session_id)

            self._trust_store.upsert(record.staged_device)  # §34: commit only now
            record.state = "CONSUMED"
            del self._sessions[session_id]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
