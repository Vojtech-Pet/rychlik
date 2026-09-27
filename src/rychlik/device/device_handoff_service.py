"""DeviceHandoffService: the application-level Device Mode facade
(Prompt A13).

A separate service from DownloadManagerService (A10) -- NOT one enormous
application facade (§6). Input is always an already-validated
rychlik.core.artifact.Artifact (§7/§8/§9); this module never accepts an
arbitrary user-controlled filesystem path as its primary interface, never
mutates DownloadTask/QueueEntry/download progress/retry state (§119), and
never touches ShareLink (§120).
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from rychlik.core.artifact import Artifact
from rychlik.device.contracts import (
    FRIENDSEND_PROTOCOL_VERSION,
    ArtifactUnavailableError,
    DeviceCapability,
    DeviceHandoffRequest,
    DeviceHandoffSnapshot,
    FriendSendDevice,
    FriendSendEndpoint,
    HandoffState,
    UnknownDeviceError,
    UnsupportedCapabilityError,
    UnsupportedProtocolError,
)
from rychlik.device.pairing import PairingManager, PairingPayload
from rychlik.device.registry import PairedDeviceRegistry
from rychlik.device.transport import FriendSendTransport, HttpFriendSendTransport, SendOutcome

_DEFAULT_CHUNK_SIZE = 256 * 1024
_PROGRESS_EVENT_MIN_INTERVAL_SECONDS = 0.1  # §115: coalesced, not per-chunk


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceHandoffEventKind(Enum):
    DEVICE_PAIRED = "DEVICE_PAIRED"
    HANDOFF_STARTED = "HANDOFF_STARTED"
    HANDOFF_PROGRESS = "HANDOFF_PROGRESS"
    HANDOFF_RECEIVED = "HANDOFF_RECEIVED"
    HANDOFF_FAILED = "HANDOFF_FAILED"
    HANDOFF_CANCELLED = "HANDOFF_CANCELLED"


@dataclass(frozen=True)
class DeviceHandoffEvent:
    kind: DeviceHandoffEventKind
    handoff_id: str | None = None
    device_id: str | None = None


class DeviceHandoffService:
    """Owns: a small bounded worker pool for handoffs (§67-§69, never A5's
    download executor), the paired-device registry, and the pairing
    handshake. `start()`/`stop()` mirror A10's lifecycle shape, but this
    is a distinct object -- nothing here is reachable through
    DownloadManagerService."""

    def __init__(
        self,
        *,
        transport: FriendSendTransport | None = None,
        registry: PairedDeviceRegistry | None = None,
        pairing_manager: PairingManager | None = None,
        max_concurrent_handoffs: int = 1,  # §68
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_delay: float = 0.0,
        clock: Callable[[], datetime] = _utc_now,
        executor_factory=None,
    ) -> None:
        self._transport = transport or HttpFriendSendTransport()
        self._registry = registry or PairedDeviceRegistry()
        self._pairing_manager = pairing_manager or PairingManager(clock=clock)
        self._max_concurrent_handoffs = max_concurrent_handoffs
        self._chunk_size = chunk_size
        self._chunk_delay = chunk_delay
        self._clock = clock
        self._executor_factory = executor_factory or (
            lambda n: ThreadPoolExecutor(max_workers=n, thread_name_prefix="rychlik-device-handoff")
        )
        self._executor: ThreadPoolExecutor | None = None

        self._lock = threading.Lock()
        self._snapshots: dict[str, DeviceHandoffSnapshot] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._last_progress_emit: dict[str, float] = {}

        self._subscribers: dict[int, Callable[[DeviceHandoffEvent], None]] = {}
        self._subscribers_lock = threading.Lock()
        self._next_token = 1

        self._running = False

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._executor = self._executor_factory(self._max_concurrent_handoffs)
        self._running = True

    def stop(self, *, timeout: float | None = None) -> None:
        if not self._running:
            return
        self._running = False
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None

    # --- pairing (§27-§34) ---------------------------------------------------

    def create_pairing_session(self, *, endpoint: FriendSendEndpoint) -> PairingPayload:
        return self._pairing_manager.create_session(endpoint=endpoint)

    def complete_pairing(
        self,
        pairing_session_id: str,
        secret: str,
        *,
        device_id: str,
        display_name: str,
        platform: str,
        endpoint: FriendSendEndpoint,
        protocol_version: int,
        capabilities: frozenset[DeviceCapability],
    ) -> FriendSendDevice:
        device = self._pairing_manager.complete_pairing(
            pairing_session_id,
            secret,
            device_id=device_id,
            display_name=display_name,
            platform=platform,
            endpoint=endpoint,
            protocol_version=protocol_version,
            capabilities=capabilities,
        )
        self._registry.add(device)
        self._emit(DeviceHandoffEvent(DeviceHandoffEventKind.DEVICE_PAIRED, device_id=device.device_id))
        return device

    def devices(self) -> tuple[FriendSendDevice, ...]:
        return self._registry.all_devices()

    # --- send / cancel (§7-§26, §61-§64) --------------------------------------

    def send(self, device_id: str, artifact: Artifact) -> str:
        """Synchronous, bounded validation (§39/§78: no network here);
        the actual transfer runs on the worker pool and this returns the
        new handoff_id immediately (§39, matches A10's async-command
        philosophy)."""
        device = self._registry.get(device_id)  # raises UnknownDeviceError
        if device.protocol_version != FRIENDSEND_PROTOCOL_VERSION:
            raise UnsupportedProtocolError(
                f"device {device_id!r} reports protocol {device.protocol_version}, "
                f"desktop supports {FRIENDSEND_PROTOCOL_VERSION}"
            )
        if DeviceCapability.RECEIVE_STREAM not in device.capabilities:
            raise UnsupportedCapabilityError(f"device {device_id!r} lacks RECEIVE_STREAM")
        if not artifact.local_path.is_file():
            raise ArtifactUnavailableError(f"artifact file no longer exists: {artifact.local_path}")

        handoff_id = str(uuid.uuid4())  # §21: fresh randomness every send
        request = DeviceHandoffRequest(
            handoff_id=handoff_id,
            device_id=device_id,
            artifact_id=artifact.artifact_id,
            display_name=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size,
            sha256=artifact.sha256,
            preferred_filename=artifact.filename,
        )
        cancel_event = threading.Event()
        snapshot = DeviceHandoffSnapshot(
            handoff_id=handoff_id,
            device_id=device_id,
            artifact_id=artifact.artifact_id,
            state=HandoffState.CREATED,
            bytes_sent=0,
            total_bytes=artifact.size,
            progress_fraction=0.0 if artifact.size == 0 else 0.0,
        )
        with self._lock:
            self._snapshots[handoff_id] = snapshot
            self._cancel_events[handoff_id] = cancel_event

        self._emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_STARTED, handoff_id=handoff_id, device_id=device_id))

        if self._executor is None:
            raise RuntimeError("DeviceHandoffService is not started")
        self._executor.submit(self._run_handoff, device, request, artifact.local_path, cancel_event)
        return handoff_id

    def cancel(self, handoff_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(handoff_id)
        if event is None:
            return False
        event.set()
        return True

    def snapshot(self, handoff_id: str) -> DeviceHandoffSnapshot | None:
        with self._lock:
            return self._snapshots.get(handoff_id)

    def active_handoffs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                hid for hid, snap in self._snapshots.items() if not snap.is_terminal
            )

    # --- worker -------------------------------------------------------------

    def _run_handoff(self, device: FriendSendDevice, request: DeviceHandoffRequest, artifact_path, cancel_event) -> None:
        self._update_snapshot(request.handoff_id, state=HandoffState.CONNECTING)

        def progress_cb(bytes_sent: int, total_bytes: int) -> None:
            self._update_snapshot(
                request.handoff_id, state=HandoffState.TRANSFERRING, bytes_sent=bytes_sent, total_bytes=total_bytes
            )
            self._maybe_emit_progress(request.handoff_id, request.device_id)

        outcome: SendOutcome = self._transport.send(
            device,
            request,
            artifact_path,
            chunk_size=self._chunk_size,
            progress_callback=progress_cb,
            cancel_event=cancel_event,
            chunk_delay=self._chunk_delay,
        )

        self._update_snapshot(
            request.handoff_id, state=outcome.state, bytes_sent=outcome.bytes_sent, failure_code=outcome.failure_code
        )
        kind = {
            HandoffState.RECEIVED: DeviceHandoffEventKind.HANDOFF_RECEIVED,
            HandoffState.FAILED: DeviceHandoffEventKind.HANDOFF_FAILED,
            HandoffState.CANCELLED: DeviceHandoffEventKind.HANDOFF_CANCELLED,
        }[outcome.state]
        self._emit(DeviceHandoffEvent(kind, handoff_id=request.handoff_id, device_id=request.device_id))

        with self._lock:
            self._cancel_events.pop(request.handoff_id, None)
            self._last_progress_emit.pop(request.handoff_id, None)

    def _update_snapshot(self, handoff_id: str, **changes) -> None:
        with self._lock:
            current = self._snapshots.get(handoff_id)
            if current is None:
                return
            total = changes.get("total_bytes", current.total_bytes)
            sent = changes.get("bytes_sent", current.bytes_sent)
            fraction = (sent / total) if total else None
            updated = replace(current, **changes, progress_fraction=fraction)
            self._snapshots[handoff_id] = updated

    def _maybe_emit_progress(self, handoff_id: str, device_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_progress_emit.get(handoff_id, 0.0)
            if now - last < _PROGRESS_EVENT_MIN_INTERVAL_SECONDS:
                return
            self._last_progress_emit[handoff_id] = now
        self._emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_PROGRESS, handoff_id=handoff_id, device_id=device_id))

    # --- events (§114-§117) ---------------------------------------------------

    def subscribe(self, callback: Callable[[DeviceHandoffEvent], None]) -> int:
        with self._subscribers_lock:
            token = self._next_token
            self._next_token += 1
            self._subscribers[token] = callback
        return token

    def unsubscribe(self, token: int) -> None:
        with self._subscribers_lock:
            self._subscribers.pop(token, None)

    def _emit(self, event: DeviceHandoffEvent) -> None:
        with self._subscribers_lock:
            callbacks = list(self._subscribers.values())
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                pass  # §117: one bad subscriber must not kill an active transfer
