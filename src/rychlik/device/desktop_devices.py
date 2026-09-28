"""Desktop-side device directory: the truthful merge of persistent trust and live discovery.

Inputs (never duplicated here):
    FriendSendTrustStore              -- who is trusted (persistent, authoritative)
    FriendSendDiscoveryService events -- who is currently visible on the LAN (ephemeral, untrusted)

Rules:
    * A trusted device that discovery cannot currently see is TRUSTED_OFFLINE, never "unpaired".
    * A trusted device is TRUSTED_ONLINE only while discovery reports it with the same security profile.
    * Discovery is not trust: an unknown device_id is only ever UNPAIRED_DISCOVERED.
    * When discovery reports a trusted device at a new endpoint, ONLY the endpoint metadata is updated
      (FriendSendTrustStore's `with_endpoint`); the TLS pin/identity are never touched. The pin is still
      verified before any media byte is sent.
    * IDENTITY_CHANGED is set only from a real failed pin/identity check and can only be cleared by a
      later successful handoff or by forgetting the device. There is no "trust the new identity" path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from rychlik.device.contracts import FriendSendEndpoint
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.trust_store import FriendSendTrustStore, TrustedFriendSendDevice


class DeviceState(Enum):
    TRUSTED_ONLINE = "trusted_online"
    TRUSTED_OFFLINE = "trusted_offline"
    IDENTITY_CHANGED = "identity_changed"
    UNPAIRED_DISCOVERED = "unpaired_discovered"


@dataclass(frozen=True)
class DeviceRow:
    device_id: str
    display_name: str
    state: DeviceState
    endpoint: FriendSendEndpoint | None
    paired_at_utc: str | None
    last_seen_utc: str | None
    security_profile: str | None

    @property
    def trusted(self) -> bool:
        return self.state != DeviceState.UNPAIRED_DISCOVERED

    @property
    def can_send(self) -> bool:
        return self.state == DeviceState.TRUSTED_ONLINE


def short_id(device_id: str) -> str:
    return device_id[:8]


class DesktopDeviceDirectory:
    def __init__(self, trust_store: FriendSendTrustStore, *, on_change: Callable[[], None] | None = None) -> None:
        self._trust_store = trust_store
        self._on_change = on_change
        self._lock = threading.Lock()
        self._discovered: dict[str, DiscoveredFriendSendDevice] = {}
        self._identity_changed: set[str] = set()

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    # --- discovery events (called from the discovery dispatch thread) --------------------------------

    def on_discovered(self, device: DiscoveredFriendSendDevice) -> None:
        with self._lock:
            self._discovered[device.device_id] = device
        self._refresh_endpoint_if_trusted(device)
        self._notify()

    def on_removed(self, device_id: str) -> None:
        with self._lock:
            self._discovered.pop(device_id, None)
        self._notify()

    def _refresh_endpoint_if_trusted(self, discovered: DiscoveredFriendSendDevice) -> bool:
        trusted = self._trust_store.get(discovered.device_id)
        if trusted is None or discovered.security_profile != trusted.security_profile:
            return False  # unknown, or a different security profile than the one this device was paired with
        if discovered.endpoint == trusted.endpoint:
            return False
        self._trust_store.upsert(trusted.with_endpoint(discovered.endpoint))  # endpoint metadata only
        return True

    def refresh_endpoint_for(self, device_id: str) -> bool:
        """Called right before a send: adopt the currently discovered endpoint of this trusted device, if any."""
        with self._lock:
            discovered = self._discovered.get(device_id)
        if discovered is None:
            return False
        changed = self._refresh_endpoint_if_trusted(discovered)
        if changed:
            self._notify()
        return changed

    # --- security state -----------------------------------------------------------------------------------

    def mark_identity_changed(self, device_id: str) -> None:
        if self._trust_store.get(device_id) is None:
            return
        with self._lock:
            self._identity_changed.add(device_id)
        self._notify()

    def clear_identity_changed(self, device_id: str) -> None:
        with self._lock:
            self._identity_changed.discard(device_id)
        self._notify()

    def forget(self, device_id: str) -> None:
        self._trust_store.remove(device_id)
        with self._lock:
            self._identity_changed.discard(device_id)
        self._notify()

    # --- queries ---------------------------------------------------------------------------------------------

    def state_of(self, trusted: TrustedFriendSendDevice) -> DeviceState:
        with self._lock:
            if trusted.device_id in self._identity_changed:
                return DeviceState.IDENTITY_CHANGED
            discovered = self._discovered.get(trusted.device_id)
        if discovered is not None and discovered.security_profile == trusted.security_profile:
            return DeviceState.TRUSTED_ONLINE
        return DeviceState.TRUSTED_OFFLINE

    def rows(self) -> list[DeviceRow]:
        trusted_devices = sorted(self._trust_store.all_devices(), key=lambda d: d.display_name.casefold())
        rows = [
            DeviceRow(
                device_id=t.device_id, display_name=t.display_name, state=self.state_of(t), endpoint=t.endpoint,
                paired_at_utc=t.paired_at_utc, last_seen_utc=t.last_seen_at_utc, security_profile=t.security_profile,
            )
            for t in trusted_devices
        ]
        trusted_ids = {t.device_id for t in trusted_devices}
        with self._lock:
            unknown = [d for d in self._discovered.values() if d.device_id not in trusted_ids]
        for d in sorted(unknown, key=lambda d: d.device_id):
            rows.append(
                DeviceRow(
                    device_id=d.device_id, display_name=f"FriendSend device {short_id(d.device_id)}", state=DeviceState.UNPAIRED_DISCOVERED,
                    endpoint=d.endpoint, paired_at_utc=None, last_seen_utc=None, security_profile=d.security_profile,
                )
            )
        return rows

    def row_for(self, device_id: str) -> DeviceRow | None:
        return next((r for r in self.rows() if r.device_id == device_id), None)

    def online_trusted(self) -> list[DeviceRow]:
        return [r for r in self.rows() if r.state == DeviceState.TRUSTED_ONLINE]

    def trusted_rows(self) -> list[DeviceRow]:
        return [r for r in self.rows() if r.trusted]
