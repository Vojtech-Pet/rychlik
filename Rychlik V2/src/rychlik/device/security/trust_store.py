"""Persistent desktop-side trusted-device store (Prompt A15 §18-19,
§63-65).

Separate from the A13 in-memory `PairedDeviceRegistry` (ephemeral,
session-only) and from `state.db` (A1-A12 download persistence, §17).
Explicit, inspectable JSON with atomic replacement -- never pickle. Never
stores the one-time pairing secret (§18).
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from rychlik.device.contracts import FriendSendEndpoint

SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1 = "pinned-tls-signature-v1"
SECURITY_PROFILE_PLAIN_HTTP_BEARER_V1 = "plain-http-bearer-v1"  # legacy/dev only, §3


@dataclass(frozen=True)
class TrustedFriendSendDevice:
    device_id: str
    display_name: str
    tls_spki_sha256: str  # 64 lowercase hex chars
    protocol_version: int
    security_profile: str
    endpoint_host: str
    endpoint_port: int
    paired_at_utc: str  # ISO-8601
    last_seen_at_utc: str | None = None

    @property
    def endpoint(self) -> FriendSendEndpoint:
        return FriendSendEndpoint(host=self.endpoint_host, port=self.endpoint_port)

    def with_endpoint(self, endpoint: FriendSendEndpoint) -> "TrustedFriendSendDevice":
        """§59/§68: endpoint may be updated (e.g. after mDNS rediscovery
        + a successful pin check) without touching the TLS pin -- the
        only field that constitutes actual trust identity."""
        return TrustedFriendSendDevice(
            device_id=self.device_id,
            display_name=self.display_name,
            tls_spki_sha256=self.tls_spki_sha256,
            protocol_version=self.protocol_version,
            security_profile=self.security_profile,
            endpoint_host=endpoint.host,
            endpoint_port=endpoint.port,
            paired_at_utc=self.paired_at_utc,
            last_seen_at_utc=datetime.now(timezone.utc).isoformat(),
        )


class TrustStoreCorruptError(Exception):
    """Raised for an unparsable trust store file -- never silently
    discarded/reset, and discovered devices are never silently trusted
    as a fallback (§165)."""


class FriendSendTrustStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        # RLock, not Lock: upsert()/remove() hold the lock while calling
        # _ensure_loaded(), which itself acquires the lock again via
        # load() the first time it's called on an unloaded store -- a
        # plain Lock would self-deadlock on that first call.
        self._lock = threading.RLock()
        self._devices: dict[str, TrustedFriendSendDevice] = {}
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._devices = {}
                self._loaded = True
                return
            try:
                raw = json.loads(self._path.read_text())
                devices = {
                    entry["device_id"]: TrustedFriendSendDevice(**entry) for entry in raw.get("devices", [])
                }
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise TrustStoreCorruptError(str(self._path)) from exc
            self._devices = devices
            self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def all_devices(self) -> tuple[TrustedFriendSendDevice, ...]:
        with self._lock:
            self._ensure_loaded()
            return tuple(self._devices.values())

    def get(self, device_id: str) -> TrustedFriendSendDevice | None:
        with self._lock:
            self._ensure_loaded()
            return self._devices.get(device_id)

    def upsert(self, device: TrustedFriendSendDevice) -> None:
        """The only trust-changing write paths are explicit pairing and
        explicit endpoint updates after a successful pin check (§137) --
        callers, never this method, are responsible for enforcing that
        an existing `tls_spki_sha256` is never silently overwritten."""
        with self._lock:
            self._ensure_loaded()
            self._devices[device.device_id] = device
            self._save_locked()

    def remove(self, device_id: str) -> None:
        with self._lock:
            self._ensure_loaded()
            self._devices.pop(device_id, None)
            self._save_locked()

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, stat.S_IRWXU)
        except OSError:
            pass
        payload = json.dumps({"devices": [asdict(d) for d in self._devices.values()]}, indent=2)
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(payload)
        try:
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp_path, self._path)  # atomic (§87)
