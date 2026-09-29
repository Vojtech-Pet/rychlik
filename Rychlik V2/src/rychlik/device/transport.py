"""FriendSend transport abstraction + a real HTTP transport (Prompt A13).

Two-phase handoff over the wire (mirrors docs/FRIENDSEND_PROTOCOL_V1.md):

    POST /handoff/offer   -- JSON metadata only, no payload bytes (§50/
                             §93/§102: a receiver rejection -- unknown
                             protocol, unsupported capability/media,
                             oversized payload -- is known BEFORE any byte
                             of the artifact is streamed)
    POST /handoff/stream  -- the raw bytes, sent only after acceptance

`HttpFriendSendTransport` is real: sockets, real HTTP/1.1, bounded
chunked reads from disk (never the whole file in memory, §42/§43/§147).
No other transport is force-HTTP-specific at the service layer -- future
transports only need to implement `FriendSendTransport`.
"""

from __future__ import annotations

import hashlib
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from rychlik.device.contracts import (
    FRIENDSEND_PROTOCOL_VERSION,
    DeviceCapability,
    FriendSendDevice,
    FriendSendEndpoint,
    HandoffErrorCode,
    HandoffState,
)

_DEFAULT_CHUNK_SIZE = 256 * 1024  # §44
_TIMEOUT = (10, 30)


class TransportCancelled(Exception):
    """Internal sentinel: the chunk generator raises this the moment it
    observes cancel_event set, so send() can distinguish a self-initiated
    cancel from a genuine network failure (§62)."""


@dataclass(frozen=True)
class ProbeResult:
    protocol_version: int
    capabilities: frozenset[DeviceCapability]
    platform: str
    device_id: str
    display_name: str


@dataclass(frozen=True)
class SendOutcome:
    state: HandoffState
    bytes_sent: int
    failure_code: HandoffErrorCode | None = None


ProgressCallback = Callable[[int, int], None]  # (bytes_sent, total_bytes)


class FriendSendTransport(ABC):
    @abstractmethod
    def probe(self, endpoint: FriendSendEndpoint) -> ProbeResult: ...

    @abstractmethod
    def send(
        self,
        device: FriendSendDevice,
        request,  # rychlik.device.contracts.DeviceHandoffRequest
        artifact_path: Path,
        *,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        chunk_delay: float = 0.0,
    ) -> SendOutcome: ...


_ERROR_CODE_BY_NAME = {code.value: code for code in HandoffErrorCode}


def _error_code_from_body(body: dict, default: HandoffErrorCode) -> HandoffErrorCode:
    name = body.get("error_code")
    return _ERROR_CODE_BY_NAME.get(name, default)


class HttpFriendSendTransport(FriendSendTransport):
    """The real, non-mock transport (§39). EXPERIMENTAL/TEST TRANSPORT
    (§37): plain HTTP + a bearer-style auth token established at pairing
    time -- there is no TLS, certificate pinning, or Noise-protocol-grade
    channel here. See docs/DEVICE_MODE_FOUNDATION.md "Security boundary"
    and docs/OPEN_VALIDATION_DEBT.md (A13-PRODUCTION-SECURE-CHANNEL)."""

    def probe(self, endpoint: FriendSendEndpoint) -> ProbeResult:
        url = f"http://{endpoint.host}:{endpoint.port}/hello"
        try:
            response = requests.get(url, timeout=_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ConnectionError(f"failed to probe FriendSend endpoint {endpoint!r}: {exc}") from exc
        data = response.json()
        return ProbeResult(
            protocol_version=data["protocol_version"],
            capabilities=frozenset(DeviceCapability[name] for name in data["capabilities"]),
            platform=data["platform"],
            device_id=data["device_id"],
            display_name=data["display_name"],
        )

    def send(
        self,
        device: FriendSendDevice,
        request,
        artifact_path: Path,
        *,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        chunk_delay: float = 0.0,
    ) -> SendOutcome:
        base = f"http://{device.endpoint.host}:{device.endpoint.port}"
        headers = {
            "X-FriendSend-Protocol-Version": str(FRIENDSEND_PROTOCOL_VERSION),
            "X-FriendSend-Auth-Token": device.auth_token,
            "X-FriendSend-Handoff-Id": request.handoff_id,
        }

        offer_body = {
            "handoff_id": request.handoff_id,
            "display_name": request.display_name,
            "mime_type": request.mime_type,
            "size_bytes": request.size_bytes,
            "sha256": request.sha256,
            "preferred_filename": request.preferred_filename,
        }
        try:
            offer_response = requests.post(
                f"{base}/handoff/offer", json=offer_body, headers=headers, timeout=_TIMEOUT
            )
        except requests.RequestException:
            return SendOutcome(HandoffState.FAILED, 0, HandoffErrorCode.CONNECTION_FAILED)

        if offer_response.status_code != 200:
            body = _safe_json(offer_response)
            code = _error_code_from_body(body, HandoffErrorCode.RECEIVER_REJECTED)
            return SendOutcome(HandoffState.FAILED, 0, code)
        if not _safe_json(offer_response).get("accepted"):
            body = _safe_json(offer_response)
            code = _error_code_from_body(body, HandoffErrorCode.RECEIVER_REJECTED)
            return SendOutcome(HandoffState.FAILED, 0, code)

        # Offer accepted -- NOW stream bytes, never before (§50/§93/§102).
        hasher = hashlib.sha256()
        sent = {"n": 0}

        def _iter_chunks():
            with artifact_path.open("rb") as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise TransportCancelled()
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        return
                    hasher.update(chunk)
                    sent["n"] += len(chunk)
                    if progress_callback is not None:
                        progress_callback(sent["n"], request.size_bytes)
                    yield chunk
                    if chunk_delay:
                        # Test-only pacing knob (never used in production
                        # calls, which default to 0.0): makes a multi-chunk
                        # transfer's progress/cancel timing deterministic
                        # over a fast loopback connection, exactly like the
                        # existing acquisition test fixtures' slow-pacing
                        # routes -- still a real socket, real bytes.
                        time.sleep(chunk_delay)

        # A bare generator has no __len__, so `requests`/urllib3 cannot
        # determine its size and falls back to `Transfer-Encoding:
        # chunked` -- while ALSO leaving the `Content-Length` header this
        # method sets below in place, producing a request with both
        # headers set simultaneously. That is invalid per RFC 7230
        # §3.3.3, and Python's own `http.server`-based test fixture
        # happens to tolerate it (it only ever reads by Content-Length
        # and never parses Transfer-Encoding at all), but a real,
        # standards-conformant HTTP/1.1 server does not have to -- this
        # was found via the real Python<->Dart FriendSend Android E2E in
        # Prompt A14, which requires an actually correct wire framing
        # against a spec-conformant peer. `_ChunkedFile` fixes this by
        # giving the iterable a real `__len__`, so `requests` sends a
        # single, unambiguous Content-Length-framed body instead.
        stream_headers = dict(headers)
        stream_headers["Content-Type"] = request.mime_type
        stream_headers["Content-Length"] = str(request.size_bytes)

        try:
            stream_response = requests.post(
                f"{base}/handoff/stream",
                data=_SizedChunks(_iter_chunks(), request.size_bytes),
                headers=stream_headers,
                timeout=_TIMEOUT,
            )
        except TransportCancelled:
            return SendOutcome(HandoffState.CANCELLED, sent["n"])
        except requests.RequestException:
            return SendOutcome(HandoffState.FAILED, sent["n"], HandoffErrorCode.CONNECTION_FAILED)

        # §59/§60: independent LOCAL integrity self-check -- if the source
        # file was mutated during transfer, the running hash we computed
        # while streaming will not match the Artifact's declared hash,
        # regardless of what the receiver reports.
        if hasher.hexdigest() != request.sha256:
            return SendOutcome(HandoffState.FAILED, sent["n"], HandoffErrorCode.INTEGRITY_MISMATCH)

        body = _safe_json(stream_response)
        if stream_response.status_code == 200 and body.get("state") == "RECEIVED":
            return SendOutcome(HandoffState.RECEIVED, sent["n"])
        code = _error_code_from_body(body, HandoffErrorCode.RECEIVER_REJECTED)
        return SendOutcome(HandoffState.FAILED, sent["n"], code)


class _SizedChunks:
    """Wraps a chunk-yielding generator with a `__len__`, so `requests`
    sends a single, correctly-framed `Content-Length` body instead of
    also adding `Transfer-Encoding: chunked` for a body it otherwise
    cannot measure (see the send() comment above for why this matters)."""

    def __init__(self, chunks, total_bytes: int) -> None:
        self._chunks = chunks
        self._total_bytes = total_bytes

    def __len__(self) -> int:
        return self._total_bytes

    def __iter__(self):
        return iter(self._chunks)


def _safe_json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}
