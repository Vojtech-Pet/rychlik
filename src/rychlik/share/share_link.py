"""ShareLink domain model and lifecycle rules (Prompt 06).

No network, no public URL infrastructure, no transport implementation here.
ShareLink is a pure domain object; ShareLinkService (below in this package)
is the only thing that mutates lifecycle state, via transition_to().
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum, auto

from rychlik.share.contracts import ShareStatus

_SHARE_ID_BYTES = 16  # 128 bits of entropy


class AccessPolicy(Enum):
    ANYONE_WITH_LINK = auto()


class TransportPolicy(Enum):
    """Link Mode transport is not frozen (see research doc Prompt 07/26/27).

    UNRESOLVED = no transport chosen yet (e.g. still CREATING).
    LIVE = served live from the sender's own machine (Prompt 07+).
    Production semantics for WEBRTC/TURN/RELAY/TEMP_CLOUD are deferred.
    """

    UNRESOLVED = auto()
    LIVE = auto()


class InvalidShareLinkTransition(Exception):
    """Raised when a ShareLink status transition is not allowed."""


TERMINAL_STATUSES = frozenset({ShareStatus.EXPIRED, ShareStatus.REVOKED, ShareStatus.FAILED})

_ALLOWED_TRANSITIONS: dict[ShareStatus, frozenset[ShareStatus]] = {
    ShareStatus.CREATING: frozenset({ShareStatus.ACTIVE, ShareStatus.FAILED}),
    ShareStatus.ACTIVE: frozenset(
        {ShareStatus.OFFLINE, ShareStatus.EXPIRED, ShareStatus.REVOKED, ShareStatus.FAILED}
    ),
    ShareStatus.OFFLINE: frozenset(
        {ShareStatus.ACTIVE, ShareStatus.EXPIRED, ShareStatus.REVOKED, ShareStatus.FAILED}
    ),
    ShareStatus.EXPIRED: frozenset(),
    ShareStatus.REVOKED: frozenset(),
    ShareStatus.FAILED: frozenset(),
}


def generate_share_id() -> str:
    """>= 128 bits of cryptographically secure entropy, unrelated to any artifact."""
    return secrets.token_urlsafe(_SHARE_ID_BYTES)


@dataclass(frozen=True)
class ShareLink:
    share_id: str
    artifact_id: str
    created_at: datetime
    status: ShareStatus = ShareStatus.CREATING
    access_policy: AccessPolicy = AccessPolicy.ANYONE_WITH_LINK
    transport_policy: TransportPolicy = TransportPolicy.UNRESOLVED
    expires_at: datetime | None = None
    public_url: str | None = None
    secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.share_id:
            raise ValueError("share_id must not be empty")
        if not self.artifact_id:
            raise ValueError("artifact_id must not be empty")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def transition_to(self, new_status: ShareStatus, *, now: datetime) -> "ShareLink":
        """Returns a new ShareLink in new_status. Raises InvalidShareLinkTransition if disallowed.

        Self-transitions are not implicitly allowed; callers that want
        idempotent revoke-on-REVOKED must check for that before calling this.
        """
        allowed = _ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise InvalidShareLinkTransition(
                f"cannot transition ShareLink from {self.status.name} to {new_status.name}"
            )
        del now  # reserved for future audit trail; not stored on ShareLink itself yet
        return replace(self, status=new_status)

    def with_public_url(self, public_url: str) -> "ShareLink":
        return replace(self, public_url=public_url)

    def to_public_dict(self) -> dict:
        """Fields safe to expose to a share recipient / preview crawler.

        Never includes: secret, artifact_id, or anything Artifact-derived
        (local path, source_url) — ShareLink does not hold those at all.
        """
        return {
            "share_id": self.share_id,
            "status": self.status.name,
            "public_url": self.public_url,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
