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
    HandoffErrorCode,
    HandoffState,
    UnknownDeviceError,
    UnsupportedCapabilityError,
    UnsupportedProtocolError,
)
from rychlik.device.media.capability_profile import DeviceMediaCapabilities
from rychlik.device.media.capability_query import CapabilityQueryFailedError, fetch_device_media_capabilities
from rychlik.device.media.preparation_service import (
    DeviceMediaPreparationService,
    HdrTranscodeUnsupported,
    MediaPreparationError,
    MediaProbeFailed,
    NoCompatibleProfile,
    PreparedMediaInvalid,
    TempStorageError,
    TranscodeFailed,
    TranscoderUnavailable,
)
from rychlik.device.pairing import PairingManager, PairingPayload
from rychlik.device.registry import PairedDeviceRegistry
from rychlik.device.security.secure_transport import SecureFriendSendTransport
from rychlik.device.transport import FriendSendTransport, HttpFriendSendTransport, SendOutcome

_DEFAULT_CHUNK_SIZE = 256 * 1024
_PROGRESS_EVENT_MIN_INTERVAL_SECONDS = 0.1  # §115: coalesced, not per-chunk

_PREPARATION_FAILURE_CODES = {
    MediaProbeFailed: HandoffErrorCode.MEDIA_PROBE_FAILED,
    NoCompatibleProfile: HandoffErrorCode.NO_COMPATIBLE_PROFILE,
    HdrTranscodeUnsupported: HandoffErrorCode.HDR_TRANSCODE_UNSUPPORTED,
    TranscoderUnavailable: HandoffErrorCode.TRANSCODER_UNAVAILABLE,
    TranscodeFailed: HandoffErrorCode.TRANSCODE_FAILED,
    PreparedMediaInvalid: HandoffErrorCode.PREPARED_MEDIA_INVALID,
    TempStorageError: HandoffErrorCode.TEMP_STORAGE_ERROR,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceHandoffEventKind(Enum):
    DEVICE_PAIRED = "DEVICE_PAIRED"
    HANDOFF_STARTED = "HANDOFF_STARTED"
    HANDOFF_PREPARING = "HANDOFF_PREPARING"  # Prompt A16
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
        media_preparation_service: DeviceMediaPreparationService | None = None,
        max_concurrent_handoffs: int = 1,  # §68
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_delay: float = 0.0,
        clock: Callable[[], datetime] = _utc_now,
        executor_factory=None,
    ) -> None:
        # Prompt A15 §3/§37: plain-http-bearer-v1 (HttpFriendSendTransport)
        # must not remain the default normal paired-device transport --
        # it is only ever used when a caller explicitly asks for it
        # (isolated tests, legacy fixtures, explicit dev mode). Production
        # default is the pinned-tls-signature-v1 secure transport.
        self._transport = transport or self._default_secure_transport()
        self._registry = registry or PairedDeviceRegistry()
        # Prompt A16 §102: automatic compatibility preparation is the
        # default for a normal send -- no separate expert opt-in needed.
        # Only ever consulted when self._transport is the secure profile
        # (see _run_handoff) -- the legacy plain-HTTP profile never
        # attempts media preparation.
        self._media_preparation_service = media_preparation_service or DeviceMediaPreparationService()
        self._media_preparation_service.start()
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

    @staticmethod
    def _default_secure_transport() -> FriendSendTransport:
        """Auto-provisions the desktop's persistent identity and trust
        store the same way `DesktopIdentityStore`/`FriendSendTrustStore`
        would be constructed standalone -- a caller never has to wire
        these up manually just to get the secure, production-default
        transport."""
        from rychlik.device.security.identity import DesktopIdentityStore
        from rychlik.device.security.secure_transport import SecureFriendSendTransport
        from rychlik.device.security.trust_store import FriendSendTrustStore

        identity_store = DesktopIdentityStore()
        identity = identity_store.load_or_create()
        trust_store = FriendSendTrustStore(identity_store.data_dir / "trust.json")
        return SecureFriendSendTransport(identity=identity, trust_store=trust_store)

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
        # The actual DeviceHandoffRequest (sha256/size/mime/filename) is
        # only built once preparation (if any) finalizes which Artifact
        # -- original or derived -- is actually sent (§63/§79 of the A16
        # prompt: it would be wrong to advertise the source hash while
        # streaming transcoded bytes).
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
        self._executor.submit(self._run_handoff, device, artifact, handoff_id, cancel_event)
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

    def _run_handoff(self, device: FriendSendDevice, artifact: Artifact, handoff_id: str, cancel_event: threading.Event) -> None:
        preparation_id = handoff_id  # one preparation per handoff, §95
        send_artifact = artifact
        media_kind = artifact.mime_type.split("/", 1)[0]

        # Prompt A16 §20/§21: only attempted for the secure profile,
        # using its own trust store as the authenticated capability
        # source -- the legacy plain-HTTP profile never runs this.
        if isinstance(self._transport, SecureFriendSendTransport) and media_kind in ("video", "audio"):
            trusted = self._transport.trust_store.get(device.device_id)
            if trusted is not None:
                try:
                    capabilities = fetch_device_media_capabilities(trusted)
                except CapabilityQueryFailedError:
                    capabilities = None  # §24: query failure falls back to direct passthrough, never a hard failure

                if capabilities is not None and not capabilities.is_empty:
                    prepared = self._prepare_media(handoff_id, device.device_id, preparation_id, artifact, capabilities, cancel_event)
                    if prepared is None:
                        return  # terminal failure/cancel already recorded by _prepare_media
                    send_artifact = prepared.artifact

        request = DeviceHandoffRequest(
            handoff_id=handoff_id,
            device_id=device.device_id,
            artifact_id=send_artifact.artifact_id,
            display_name=send_artifact.filename,
            mime_type=send_artifact.mime_type,
            size_bytes=send_artifact.size,
            sha256=send_artifact.sha256,
            preferred_filename=send_artifact.filename,
        )

        self._update_snapshot(handoff_id, state=HandoffState.CONNECTING, total_bytes=send_artifact.size)

        def progress_cb(bytes_sent: int, total_bytes: int) -> None:
            self._update_snapshot(
                handoff_id, state=HandoffState.TRANSFERRING, bytes_sent=bytes_sent, total_bytes=total_bytes
            )
            self._maybe_emit_progress(handoff_id, device.device_id)

        outcome: SendOutcome = self._transport.send(
            device,
            request,
            send_artifact.local_path,
            chunk_size=self._chunk_size,
            progress_callback=progress_cb,
            cancel_event=cancel_event,
            chunk_delay=self._chunk_delay,
        )

        self._finish_handoff(handoff_id, device.device_id, outcome.state, outcome.bytes_sent, outcome.failure_code)
        self._media_preparation_service.discard(preparation_id)  # §86/§87: no-op if PASSTHROUGH (never allocated)

    def _prepare_media(self, handoff_id, device_id, preparation_id, artifact, capabilities: DeviceMediaCapabilities, cancel_event):
        """Returns the `PreparedDeviceMedia`, or `None` if a terminal
        failure/cancellation was already recorded (caller must return
        immediately in that case)."""
        self._update_snapshot(handoff_id, state=HandoffState.PREPARING)
        self._emit(DeviceHandoffEvent(DeviceHandoffEventKind.HANDOFF_PREPARING, handoff_id=handoff_id, device_id=device_id))

        def progress_cb(processed_seconds, duration_seconds):
            fraction = (processed_seconds / duration_seconds) if (processed_seconds and duration_seconds) else None
            self._update_snapshot(handoff_id, preparation_progress=fraction)

        try:
            prepared = self._media_preparation_service.prepare(
                artifact, capabilities, preparation_id=preparation_id, progress_callback=progress_cb, cancel_event=cancel_event
            )
        except MediaPreparationError as exc:
            if isinstance(exc, TranscodeFailed) and exc.cancelled:
                self._finish_handoff(handoff_id, device_id, HandoffState.CANCELLED, 0, None)
            else:
                code = _PREPARATION_FAILURE_CODES.get(type(exc), HandoffErrorCode.TRANSCODE_FAILED)
                self._finish_handoff(handoff_id, device_id, HandoffState.FAILED, 0, code)
            return None

        self._update_snapshot(
            handoff_id,
            preparation_kind=prepared.plan_kind.value,
            target_profile_id=prepared.target_profile_id,
            preparation_warnings=tuple(w.value for w in prepared.warnings),
            preparation_progress=1.0,
        )
        return prepared

    def _finish_handoff(self, handoff_id, device_id, state, bytes_sent, failure_code) -> None:
        self._update_snapshot(handoff_id, state=state, bytes_sent=bytes_sent, failure_code=failure_code)
        kind = {
            HandoffState.RECEIVED: DeviceHandoffEventKind.HANDOFF_RECEIVED,
            HandoffState.FAILED: DeviceHandoffEventKind.HANDOFF_FAILED,
            HandoffState.CANCELLED: DeviceHandoffEventKind.HANDOFF_CANCELLED,
        }[state]
        self._emit(DeviceHandoffEvent(kind, handoff_id=handoff_id, device_id=device_id))

        with self._lock:
            self._cancel_events.pop(handoff_id, None)
            self._last_progress_emit.pop(handoff_id, None)

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
