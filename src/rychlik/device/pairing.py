"""Pairing session contract (Prompt A13).

A cryptographically-secure, one-time, expiring handshake that produces a
trusted FriendSendDevice. Deliberately runtime-only (§34/§75) -- no raw
pairing secret is ever persisted to SQLite (§76); long-term device trust
persistence is explicitly deferred to A14, once real Android credential
semantics exist.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable

from rychlik.device.contracts import (
    FRIENDSEND_PROTOCOL_VERSION,
    DeviceCapability,
    DeviceModeError,
    FriendSendDevice,
    FriendSendEndpoint,
)

_PAIRING_SECRET_BYTES = 16  # 128 bits (§30)
DEFAULT_PAIRING_TTL_SECONDS = 300  # §32


class PairingExpiredError(DeviceModeError):
    """Raised on an expired or already-consumed (replayed, §33/§91)
    pairing session -- no silent extension, no reuse."""


class PairingAuthenticationFailedError(DeviceModeError):
    """Raised for an unknown session id or a secret that does not match
    (§89) -- never distinguishes "wrong secret" from "unknown session" in
    its message, to avoid leaking which is the case."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PairingPayload:
    """What a future receiver needs to initiate/verify pairing (§28).
    Exact wire representation (JSON) is documented in
    docs/FRIENDSEND_PROTOCOL_V1.md; this is the Python shape only."""

    protocol_version: int
    pairing_session_id: str
    desktop_instance_id: str
    endpoint: FriendSendEndpoint
    secret: str
    expires_at_utc: datetime

    def redacted(self) -> dict:
        """§129: a debug/log-safe representation with the secret masked.
        Explicit full serialization for actual pairing use is a separate,
        deliberate call (`to_wire_dict()`), never this one."""
        return {
            "protocol_version": self.protocol_version,
            "pairing_session_id": self.pairing_session_id,
            "desktop_instance_id": self.desktop_instance_id,
            "endpoint": {"host": self.endpoint.host, "port": self.endpoint.port},
            "secret": "***REDACTED***",
            "expires_at_utc": self.expires_at_utc.isoformat(),
        }

    def to_wire_dict(self) -> dict:
        """The actual pairing payload (e.g. serialized into a QR code in
        a future phase) -- callers must not log this."""
        return {
            "protocol_version": self.protocol_version,
            "pairing_session_id": self.pairing_session_id,
            "desktop_instance_id": self.desktop_instance_id,
            "endpoint": {"host": self.endpoint.host, "port": self.endpoint.port},
            "secret": self.secret,
            "expires_at_utc": self.expires_at_utc.isoformat(),
        }


class _SessionState(Enum):
    PENDING = "PENDING"
    CONSUMED = "CONSUMED"


@dataclass
class _SessionRecord:
    secret: str
    expires_at_utc: datetime
    state: _SessionState


class PairingManager:
    """Owns the desktop side of the pairing handshake. `complete_pairing()`
    is the domain operation a future network endpoint calls once it has
    received the secret back from the peer (§27) -- A13 proves this
    operation's security properties directly; wiring it to a real network
    listener is a bounded, separate concern (see docs/DEVICE_MODE_
    FOUNDATION.md, "Pairing transport scoping")."""

    def __init__(
        self,
        *,
        desktop_instance_id: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        ttl_seconds: float = DEFAULT_PAIRING_TTL_SECONDS,
    ) -> None:
        self._desktop_instance_id = desktop_instance_id or str(uuid.uuid4())
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionRecord] = {}

    def create_session(self, *, endpoint: FriendSendEndpoint) -> PairingPayload:
        session_id = str(uuid.uuid4())
        secret = secrets.token_hex(_PAIRING_SECRET_BYTES)
        now = self._clock()
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        with self._lock:
            self._sessions[session_id] = _SessionRecord(secret, expires_at, _SessionState.PENDING)
        return PairingPayload(
            protocol_version=FRIENDSEND_PROTOCOL_VERSION,
            pairing_session_id=session_id,
            desktop_instance_id=self._desktop_instance_id,
            endpoint=endpoint,
            secret=secret,
            expires_at_utc=expires_at,
        )

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
        """One-time (§33): a session can only ever complete successfully
        once; both expiry and replay raise (never silently re-validate an
        already-consumed session, §91)."""
        with self._lock:
            record = self._sessions.get(pairing_session_id)
            if record is None:
                raise PairingAuthenticationFailedError("unknown pairing session")
            if record.state == _SessionState.CONSUMED:
                raise PairingExpiredError("pairing session already consumed")
            if self._clock() > record.expires_at_utc:
                del self._sessions[pairing_session_id]
                raise PairingExpiredError("pairing session expired")
            if not secrets.compare_digest(secret, record.secret):
                raise PairingAuthenticationFailedError("pairing secret does not match")
            record.state = _SessionState.CONSUMED

        return FriendSendDevice(
            device_id=device_id,
            display_name=display_name,
            platform=platform,
            endpoint=endpoint,
            protocol_version=protocol_version,
            capabilities=frozenset(capabilities),
            auth_token=secrets.token_hex(_PAIRING_SECRET_BYTES),
            paired_at_utc=self._clock(),
        )
