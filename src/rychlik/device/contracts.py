"""Device Mode / FriendSend domain contracts (Prompt A13).

Pure value objects and the bounded error/state taxonomy. No networking,
no threading, no file I/O here -- see transport.py / device_handoff_
service.py for the impure layers. See docs/FRIENDSEND_PROTOCOL_V1.md for
the platform-neutral wire-protocol description this Python shape mirrors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

FRIENDSEND_PROTOCOL_VERSION = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DeviceModeError(Exception):
    """Base type for all Device Mode errors."""


class UnknownDeviceError(DeviceModeError):
    """Raised for a device_id absent from the paired-device registry
    (§72) -- never silently falls back to "the first" or "the last used"
    device (§73)."""


class UnsupportedProtocolError(DeviceModeError):
    """Raised when a paired device's protocol_version does not match
    what this desktop supports -- never a silent compatibility guess
    (§12/§92)."""


class UnsupportedCapabilityError(DeviceModeError):
    """Raised when a required capability (e.g. RECEIVE_STREAM) is absent
    from the target device -- checked before any byte is streamed (§93)."""


class ArtifactUnavailableError(DeviceModeError):
    """Raised when the supplied Artifact's local file no longer exists at
    send() time (§103) -- Device Mode only ever streams an explicitly
    supplied, currently-valid Artifact, never an arbitrary path (§9)."""


class DeviceCapability(Enum):
    """Deliberately generic (§17/§18) -- never a per-social-app whitelist
    like WHATSAPP/MESSENGER/TELEGRAM. Future FriendSend uses Android's
    normal Sharesheet, so the desktop never needs to know which specific
    apps are installed on the peer."""

    RECEIVE_STREAM = "RECEIVE_STREAM"
    TEMPORARY_FILE_HANDOFF = "TEMPORARY_FILE_HANDOFF"
    SHARE_TO_OS = "SHARE_TO_OS"


@dataclass(frozen=True)
class FriendSendEndpoint:
    """Transport addressing only -- future application code never parses
    an ad hoc endpoint string (§15)."""

    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host must not be empty")
        if not (0 < self.port < 65536):
            raise ValueError(f"port out of range: {self.port}")


@dataclass(frozen=True)
class FriendSendDevice:
    """Immutable device descriptor (§16). Never holds a live socket/
    session object -- `auth_token` is a long-lived-for-this-pairing
    credential, not a connection. Identity is `device_id` alone (§13/§14)
    -- `display_name` is presentation-only and may collide/change."""

    device_id: str
    display_name: str
    platform: str
    endpoint: FriendSendEndpoint
    protocol_version: int
    capabilities: frozenset[DeviceCapability]
    auth_token: str
    paired_at_utc: datetime

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if not self.auth_token:
            raise ValueError("auth_token must not be empty")
        if self.paired_at_utc.tzinfo is None:
            raise ValueError("paired_at_utc must be timezone-aware")


class HandoffErrorCode(Enum):
    """Bounded taxonomy (§126) -- never an HTTP-specific error leaking
    into the public service API."""

    UNKNOWN_DEVICE = "UNKNOWN_DEVICE"
    UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PAIRING_EXPIRED = "PAIRING_EXPIRED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    ARTIFACT_UNAVAILABLE = "ARTIFACT_UNAVAILABLE"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    INCOMPLETE_TRANSFER = "INCOMPLETE_TRANSFER"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"
    RECEIVER_REJECTED = "RECEIVER_REJECTED"
    # Prompt A15 (pinned-tls-signature-v1 security profile):
    TLS_PIN_MISMATCH = "TLS_PIN_MISMATCH"
    UNTRUSTED_DESKTOP = "UNTRUSTED_DESKTOP"
    AUTH_REPLAY = "AUTH_REPLAY"
    CHALLENGE_EXPIRED = "CHALLENGE_EXPIRED"
    DEVICE_IDENTITY_MISMATCH = "DEVICE_IDENTITY_MISMATCH"


class HandoffState(Enum):
    """Truthful, bounded (§22-26). `HANDOFF_ACCEPTED` (OS/app accepted
    the share) is deliberately NOT a reachable state in A13 -- the
    fixture receiver never implements Android Sharesheet, so claiming it
    would be a lie (§26). `RECEIVED` means only that FriendSend verified
    the complete payload arrived intact -- never that any further app or
    person received/viewed it (§23/§24)."""

    CREATED = "CREATED"
    CONNECTING = "CONNECTING"
    TRANSFERRING = "TRANSFERRING"
    RECEIVED = "RECEIVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_TERMINAL_STATES = frozenset({HandoffState.RECEIVED, HandoffState.FAILED, HandoffState.CANCELLED})


@dataclass(frozen=True)
class DeviceHandoffRequest:
    """Only public-safe Artifact information ever crosses this boundary
    (§19/§20) -- never a local filesystem path. `handoff_id` is fresh
    cryptographic randomness per send, even for the same Artifact to the
    same device twice (§21/§122)."""

    handoff_id: str
    device_id: str
    artifact_id: str
    display_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    preferred_filename: str | None = None

    def __post_init__(self) -> None:
        if not self.handoff_id:
            raise ValueError("handoff_id must not be empty")
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if not _SHA256_RE.match(self.sha256):
            raise ValueError(f"sha256 must be 64 lowercase hex characters, got {self.sha256!r}")


@dataclass(frozen=True)
class DeviceHandoffSnapshot:
    """Immutable, GUI-safe (§66) -- no socket/thread/path reference ever
    appears here."""

    handoff_id: str
    device_id: str
    artifact_id: str
    state: HandoffState
    bytes_sent: int
    total_bytes: int
    progress_fraction: float | None
    failure_code: HandoffErrorCode | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES
