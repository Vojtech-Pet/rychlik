"""Deterministic FriendSend receiver fixture (Prompt A13).

THIS IS TEST/DEV SUPPORT CODE ONLY -- it is not, and must never be
presented as, the FriendSend Android application (§41/§108). It stands in
for the future mobile peer just enough to prove the desktop-side handoff
contract against a real HTTP server on a real (OS-assigned, loopback-
only) socket. See docs/FRIENDSEND_PROTOCOL_V1.md for the wire contract
this implements.

Models the "no permanent storage" contract (§46/§47/§49): received bytes
are spooled to a private temp file and deleted immediately after
verification (success, failure, or cancellation) -- never accumulated as
a fake inbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rychlik.device.contracts import FRIENDSEND_PROTOCOL_VERSION

_READ_CHUNK = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence test output
        pass

    def do_GET(self):
        if self.path == "/hello":
            server = self.server
            body = json.dumps(
                {
                    "protocol_version": server.protocol_version_reported,
                    "capabilities": [c.value for c in server.capabilities],
                    "platform": "test-fixture",
                    "device_id": server.device_id,
                    "display_name": server.display_name,
                }
            ).encode()
            self._send_json(200, body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/handoff/offer":
            self._handle_offer()
        elif self.path == "/handoff/stream":
            self._handle_stream()
        else:
            self.send_response(404)
            self.end_headers()

    # --- offer (preflight, no body bytes consumed) --------------------------

    def _handle_offer(self):
        server = self.server
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            offer = json.loads(raw)
        except ValueError:
            self._reject(400, "RECEIVER_REJECTED")
            return

        rejection = self._preflight_reject_reason(offer)
        if rejection is not None:
            self._reject(*rejection)
            return

        with server.lock:
            server.pending_offers[offer["handoff_id"]] = offer
        self._send_json(200, json.dumps({"accepted": True}).encode())

    def _preflight_reject_reason(self, offer: dict):
        server = self.server
        protocol_header = self.headers.get("X-FriendSend-Protocol-Version")
        if protocol_header != str(server.protocol_version_reported):
            return 400, "UNSUPPORTED_PROTOCOL"
        token = self.headers.get("X-FriendSend-Auth-Token")
        with server.lock:
            token_ok = token in server.accepted_tokens
        if not token_ok:
            return 401, "AUTHENTICATION_FAILED"
        if server.force_capability_rejection:
            return 400, "UNSUPPORTED_CAPABILITY"
        if server.max_payload_bytes is not None and offer.get("size_bytes", 0) > server.max_payload_bytes:
            return 413, "PAYLOAD_TOO_LARGE"
        if server.supported_mime_types is not None and offer.get("mime_type") not in server.supported_mime_types:
            return 415, "UNSUPPORTED_MEDIA"
        if server.force_receiver_rejection:
            return 400, "RECEIVER_REJECTED"
        return None

    # --- stream (actual bytes) -----------------------------------------------

    def _handle_stream(self):
        server = self.server
        handoff_id = self.headers.get("X-FriendSend-Handoff-Id")
        token = self.headers.get("X-FriendSend-Auth-Token")
        declared_length = int(self.headers.get("Content-Length", "0"))

        with server.lock:
            offer = server.pending_offers.pop(handoff_id, None)
            token_ok = token in server.accepted_tokens

        if offer is None or not token_ok:
            self._drain_and_reject(declared_length, 401, "AUTHENTICATION_FAILED")
            return
        if offer.get("size_bytes") != declared_length or offer.get("sha256") is None:
            self._drain_and_reject(declared_length, 400, "RECEIVER_REJECTED")
            return

        declared_sha256 = offer["sha256"]
        fd, tmp_path = tempfile.mkstemp(dir=server.temp_dir, prefix="incoming-")
        # §58: the declared filename is NEVER used to construct this path.
        bytes_received = 0
        hasher = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as handle:
                remaining = declared_length
                while remaining > 0:
                    chunk = self.rfile.read(min(_READ_CHUNK, remaining))
                    if not chunk:
                        break  # sender stopped early (cancel or real failure)
                    handle.write(chunk)
                    hasher.update(chunk)
                    bytes_received += len(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except (ConnectionError, OSError):
            self._cleanup_temp(tmp_path)
            self.close_connection = True
            return

        if server.force_corrupt_next:
            server.force_corrupt_next = False
            with open(tmp_path, "r+b") as handle:
                handle.seek(0)
                first = handle.read(1)
                handle.seek(0)
                handle.write(bytes([first[0] ^ 0xFF]) if first else b"\xff")
            actual_sha256 = _hash_file(tmp_path)
        else:
            actual_sha256 = hasher.hexdigest()

        if bytes_received != declared_length:
            self._cleanup_temp(tmp_path)
            self._reject(200, "INCOMPLETE_TRANSFER", state="FAILED")
            return
        if actual_sha256 != declared_sha256:
            self._cleanup_temp(tmp_path)
            self._reject(200, "INTEGRITY_MISMATCH", state="FAILED")
            return

        with server.lock:
            server.received_log.append(
                {
                    "handoff_id": handoff_id,
                    "filename": offer.get("preferred_filename") or offer.get("display_name"),
                    "size": bytes_received,
                    "sha256": actual_sha256,
                }
            )
        self._cleanup_temp(tmp_path)  # §47/§48: no permanent storage
        self._send_json(
            200, json.dumps({"state": "RECEIVED", "bytes_received": bytes_received, "sha256": actual_sha256}).encode()
        )

    # --- helpers ---------------------------------------------------------------

    def _cleanup_temp(self, tmp_path: str) -> None:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    def _drain_and_reject(self, length: int, status: int, error_code: str) -> None:
        # Must still consume the declared body so the connection stays
        # usable for the response (best-effort; a hard failure here is
        # fine, the client will see a connection error).
        try:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(_READ_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except (ConnectionError, OSError):
            pass
        self._reject(status, error_code, state="FAILED")

    def _reject(self, status: int, error_code: str, *, state: str = "FAILED") -> None:
        body = json.dumps({"state": state, "error_code": error_code}).encode()
        self._send_json(status, body)

    def _send_json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FriendSendReceiverFixture:
    """Real HTTP server, loopback-only, OS-assigned port (§94/§95)."""

    def __init__(
        self,
        *,
        device_id: str = "fixture-device",
        display_name: str = "Test FriendSend Device",
        capabilities=None,
        protocol_version: int = FRIENDSEND_PROTOCOL_VERSION,
        max_payload_bytes: int | None = None,
        supported_mime_types=None,
    ) -> None:
        from rychlik.device.contracts import DeviceCapability

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.device_id = device_id
        self._server.display_name = display_name
        self._server.capabilities = capabilities or {DeviceCapability.RECEIVE_STREAM}
        self._server.protocol_version_reported = protocol_version
        self._server.max_payload_bytes = max_payload_bytes
        self._server.supported_mime_types = supported_mime_types
        self._server.force_capability_rejection = False
        self._server.force_receiver_rejection = False
        self._server.force_corrupt_next = False
        self._server.temp_dir = tempfile.mkdtemp(prefix="friendsend-fixture-")
        self._server.lock = threading.Lock()
        self._server.accepted_tokens = set()
        self._server.pending_offers = {}
        self._server.received_log = []
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self):
        from rychlik.device.contracts import FriendSendEndpoint

        host, port = self._server.server_address[:2]
        return FriendSendEndpoint(host=host, port=port)

    @property
    def device_id(self) -> str:
        return self._server.device_id

    @property
    def display_name(self) -> str:
        return self._server.display_name

    def accept_token(self, token: str) -> None:
        with self._server.lock:
            self._server.accepted_tokens.add(token)

    def force_capability_rejection(self, value: bool = True) -> None:
        self._server.force_capability_rejection = value

    def force_receiver_rejection(self, value: bool = True) -> None:
        self._server.force_receiver_rejection = value

    def force_corrupt_next_transfer(self) -> None:
        self._server.force_corrupt_next = True

    def received_log(self) -> list:
        with self._server.lock:
            return list(self._server.received_log)

    def temp_dir_is_empty(self) -> bool:
        return len(os.listdir(self._server.temp_dir)) == 0

    def start(self) -> "FriendSendReceiverFixture":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        import shutil

        shutil.rmtree(self._server.temp_dir, ignore_errors=True)
