"""Device Mode presentation boundary for the desktop GUI.

Sits between Qt widgets and the existing, unchanged Device Mode services:

    widgets -> DeviceModeController -> DeviceHandoffService (secure A15 transport, A16 preparation)
                                    -> FriendSendDiscoveryService (untrusted LAN discovery)
                                    -> DesktopDeviceDirectory   (trust + discovery -> truthful states)
                                    -> SecurePairingManager     (real one-time pairing)

Widgets never construct transports, touch the trust store or run FFmpeg; this module never reimplements
any of that. Service events arrive on background threads and are re-emitted as Qt signals, so widgets
only ever change on the GUI thread.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from rychlik.core.artifact import Artifact
from rychlik.device.contracts import DeviceHandoffSnapshot, HandoffErrorCode, HandoffState
from rychlik.device.desktop_devices import DesktopDeviceDirectory, DeviceRow
from rychlik.device.device_handoff_service import DeviceHandoffEvent, DeviceHandoffEventKind, DeviceHandoffService

PAIRING_TTL_SECONDS = 300.0

_IDENTITY_CODES = frozenset({HandoffErrorCode.TLS_PIN_MISMATCH, HandoffErrorCode.DEVICE_IDENTITY_MISMATCH})


@dataclass(frozen=True)
class PairingSession:
    code_text: str  # what the user copies into FriendSend ("pairing code")
    expires_at: datetime
    baseline_device_ids: frozenset[str]

    def seconds_left(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        return max(0, int((self.expires_at - now).total_seconds()))


# --- user-facing wording (never protocol internals) -----------------------------------------------------------------


@dataclass(frozen=True)
class FriendlyError:
    title: str
    text: str
    identity_problem: bool = False


_FRIENDLY: dict[HandoffErrorCode, tuple[str, str]] = {
    HandoffErrorCode.CONNECTION_FAILED: ("Couldn’t send the file", "The connection to {device} was interrupted. Nothing was saved on the phone."),
    HandoffErrorCode.INCOMPLETE_TRANSFER: ("Couldn’t send the file", "The transfer to {device} did not finish. Nothing was saved on the phone."),
    HandoffErrorCode.INTEGRITY_MISMATCH: ("File verification failed", "{device} received a file that did not match the original, so it was discarded."),
    HandoffErrorCode.UNKNOWN_DEVICE: ("Device not found", "{device} is no longer paired. Pair it again to send files."),
    HandoffErrorCode.UNTRUSTED_DESKTOP: ("{device} doesn’t recognise this computer", "Forget the device in Rýchlik and pair it again."),
    HandoffErrorCode.AUTHENTICATION_FAILED: ("{device} didn’t accept this computer", "Forget the device in Rýchlik and pair it again."),
    HandoffErrorCode.RECEIVER_REJECTED: ("{device} declined the file", "Open FriendSend on the phone and try again."),
    HandoffErrorCode.PAYLOAD_TOO_LARGE: ("The file is too large", "{device} can’t receive a file this size."),
    HandoffErrorCode.ARTIFACT_UNAVAILABLE: ("The file is no longer available", "Rýchlik can’t find the downloaded file any more."),
    HandoffErrorCode.UNSUPPORTED_PROTOCOL: ("Update needed", "{device} uses a different FriendSend version. Update the app and try again."),
    HandoffErrorCode.UNSUPPORTED_CAPABILITY: ("Can’t send to this device", "{device} can’t receive files."),
    HandoffErrorCode.UNSUPPORTED_MEDIA: ("This file can’t be sent", "{device} can’t receive this kind of file."),
    HandoffErrorCode.NO_COMPATIBLE_PROFILE: ("Can’t make a compatible copy", "{device} can’t play this kind of video, and Rýchlik can’t convert it for that device."),
    HandoffErrorCode.HDR_TRANSCODE_UNSUPPORTED: ("HDR video can’t be converted yet", "Converting HDR video would change how it looks, so it wasn’t sent."),
    HandoffErrorCode.MEDIA_PROBE_FAILED: ("Couldn’t read the video", "Rýchlik couldn’t read this file, so it wasn’t sent."),
    HandoffErrorCode.TRANSCODER_UNAVAILABLE: ("Can’t convert this file", "The video converter is not available on this computer."),
    HandoffErrorCode.TRANSCODE_FAILED: ("Couldn’t prepare the file", "Making a compatible copy failed. The original was not changed."),
    HandoffErrorCode.PREPARED_MEDIA_INVALID: ("Couldn’t prepare the file", "The compatible copy did not pass its checks, so it wasn’t sent. The original was not changed."),
    HandoffErrorCode.TEMP_STORAGE_ERROR: ("Couldn’t prepare the file", "There isn’t enough space for a temporary copy."),
    HandoffErrorCode.PAIRING_EXPIRED: ("Pairing expired", "Create a new pairing code."),
}


def friendly_error(code: HandoffErrorCode | None, device_name: str) -> FriendlyError:
    """Plain wording for a failed handoff; SPKI/signature/challenge terms stay in diagnostics."""
    if code in _IDENTITY_CODES:
        return FriendlyError("Device identity changed", "The saved security identity no longer matches this device. "
                             "If this change is expected, forget the device and pair it again.", identity_problem=True)
    title, text = _FRIENDLY.get(code, ("Couldn’t send the file", "Something went wrong while sending to {device}."))
    return FriendlyError(title.format(device=device_name), text.format(device=device_name))


@dataclass(frozen=True)
class SendStage:
    """What the Send dialog should show for one handoff snapshot."""

    step: int  # 0 prepare, 1 send, 2 done
    headline: str
    detail: str
    fraction: float | None
    indeterminate: bool
    reassurance: str | None = None  # "The original file will not be changed."


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def send_stage(snapshot: DeviceHandoffSnapshot, device_name: str, artifact_duration: float | None = None) -> SendStage:
    """Truthful stage text. "Sending" is shown only once bytes can flow, never while preparing."""
    state = snapshot.state
    kind = snapshot.preparation_kind
    if state in (HandoffState.CREATED, HandoffState.PREPARING) and kind in (None, "PASSTHROUGH"):
        return SendStage(0, "Checking the file…", f"Making sure it will play on {device_name}.", None, True)
    if state in (HandoffState.CREATED, HandoffState.PREPARING):
        fraction = snapshot.preparation_progress
        if kind == "REMUX":
            headline, detail = "Preparing compatible copy…", "Optimizing container…"
        else:
            headline = f"Converting video for {device_name}…"
            detail = ""
            if fraction is not None and artifact_duration:
                detail = f"Converted {_mmss(fraction * artifact_duration)} of {_mmss(artifact_duration)}"
        return SendStage(0, headline, detail, fraction, fraction is None, "The original file will not be changed.")
    if state == HandoffState.CONNECTING:
        return SendStage(1, f"Connecting to {device_name}…", "", None, True)
    if state == HandoffState.TRANSFERRING:
        return SendStage(1, f"Sending to {device_name}…", "Keep FriendSend open on the device.", snapshot.progress_fraction, snapshot.progress_fraction is None)
    if state == HandoffState.RECEIVED:
        return SendStage(2, f"Received by {device_name}", "The file arrived and was verified.", 1.0, False)
    if state == HandoffState.CANCELLED:
        return SendStage(2, "Cancelled", "The original file was not changed.", None, False)
    return SendStage(2, "Couldn’t send the file", "", None, False)


# --- controller ---------------------------------------------------------------------------------------------------------


class DeviceModeController(QObject):
    devices_changed = Signal()
    handoff_updated = Signal(str)  # handoff_id
    discovery_problem = Signal(str)

    def __init__(
        self,
        *,
        trust_store=None,
        identity=None,
        handoff_service: DeviceHandoffService | None = None,
        discovery=None,
        data_dir: Path | None = None,
        pairing_manager_factory=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if trust_store is None or identity is None:
            from rychlik.device.security.identity import DesktopIdentityStore
            from rychlik.device.security.trust_store import FriendSendTrustStore

            identity_store = DesktopIdentityStore(data_dir) if data_dir is not None else DesktopIdentityStore()
            identity = identity or identity_store.load_or_create()
            trust_store = trust_store or FriendSendTrustStore(identity_store.data_dir / "trust.json")
        self._identity = identity
        self._trust_store = trust_store
        if handoff_service is None:
            from rychlik.device.security.secure_transport import SecureFriendSendTransport

            handoff_service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        self._handoff = handoff_service
        if discovery is None:
            from rychlik.device.security.discovery import FriendSendDiscoveryService

            discovery = FriendSendDiscoveryService()
        self._discovery = discovery
        self._directory = DesktopDeviceDirectory(trust_store, on_change=self.devices_changed.emit)
        self._pairing_factory = pairing_manager_factory
        self._pairing_manager = None
        self._token: int | None = None
        self._started = False

    # --- lifecycle ---------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._handoff.start()
        self._token = self._handoff.subscribe(self._on_handoff_event)
        self._discovery.subscribe(on_found=self._directory.on_discovered, on_removed=self._directory.on_removed)
        try:
            self._discovery.start()
        except Exception as exc:  # noqa: BLE001 - LAN discovery is best effort; the app must still run
            self.discovery_problem.emit(f"Device discovery is unavailable: {exc}")

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self.cancel_pairing()
        if self._token is not None:
            self._handoff.unsubscribe(self._token)
            self._token = None
        try:
            self._discovery.stop()
        finally:
            self._handoff.stop()

    # --- devices -------------------------------------------------------------------------------------------

    @property
    def directory(self) -> DesktopDeviceDirectory:
        return self._directory

    def rows(self) -> list[DeviceRow]:
        return self._directory.rows()

    def row_for(self, device_id: str) -> DeviceRow | None:
        return self._directory.row_for(device_id)

    def forget_device(self, device_id: str) -> None:
        self._directory.forget(device_id)

    # --- pairing (real A15 flow) -------------------------------------------------------------------------------

    def create_pairing_session(self) -> PairingSession:
        self.cancel_pairing()
        if self._pairing_factory is not None:
            manager = self._pairing_factory(self._identity, self._trust_store)
        else:
            from rychlik.device.security.pairing_bootstrap import SecurePairingManager

            # The phone must reach the bootstrap listener over the LAN, so bind all interfaces; the
            # one-time secret + transcript HMAC (not the network) authorise the pairing.
            manager = SecurePairingManager(identity=self._identity, trust_store=self._trust_store, bind_host="0.0.0.0", ttl_seconds=PAIRING_TTL_SECONDS)
        self._pairing_manager = manager
        payload = manager.create_session()
        return PairingSession(
            code_text=json.dumps(payload.to_wire_dict()),
            expires_at=payload.expires_at_utc,
            baseline_device_ids=frozenset(d.device_id for d in self._trust_store.all_devices()),
        )

    def completed_pairing(self, session: PairingSession) -> DeviceRow | None:
        """The trusted device added since this session began, if pairing finished."""
        for device in self._trust_store.all_devices():
            if device.device_id not in session.baseline_device_ids:
                self.devices_changed.emit()
                return self._directory.row_for(device.device_id)
        return None

    def cancel_pairing(self) -> None:
        manager, self._pairing_manager = self._pairing_manager, None
        if manager is not None:
            try:
                manager.stop()
            except Exception:  # noqa: BLE001
                pass

    # --- sending ---------------------------------------------------------------------------------------------------

    def send(self, device_id: str, artifact: Artifact) -> str:
        """Refreshes the trusted device's endpoint from current discovery (identity is by device_id;
        the pin is still verified before any byte is sent), then starts the real handoff."""
        self._directory.refresh_endpoint_for(device_id)
        return self._handoff.send(device_id, artifact)

    def snapshot(self, handoff_id: str) -> DeviceHandoffSnapshot | None:
        return self._handoff.snapshot(handoff_id)

    def cancel(self, handoff_id: str) -> bool:
        return self._handoff.cancel(handoff_id)

    def _on_handoff_event(self, event: DeviceHandoffEvent) -> None:
        """Runs on a service thread: only state bookkeeping + a Qt signal (auto-queued to the GUI thread)."""
        if event.kind in (DeviceHandoffEventKind.HANDOFF_FAILED, DeviceHandoffEventKind.HANDOFF_RECEIVED) and event.handoff_id:
            snapshot = self._handoff.snapshot(event.handoff_id)
            if snapshot is not None and event.device_id:
                if event.kind == DeviceHandoffEventKind.HANDOFF_FAILED and snapshot.failure_code in _IDENTITY_CODES:
                    self._directory.mark_identity_changed(event.device_id)
                elif event.kind == DeviceHandoffEventKind.HANDOFF_RECEIVED:
                    self._directory.clear_identity_changed(event.device_id)
        if event.handoff_id:
            self.handoff_updated.emit(event.handoff_id)


def pairing_expiry_text(session: PairingSession, now: datetime | None = None) -> str:
    left = session.seconds_left(now)
    return f"Expires in {left // 60}:{left % 60:02d}"

