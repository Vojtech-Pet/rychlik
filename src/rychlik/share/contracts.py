"""Shared contracts for DeviceShareService and ShareLinkService.

Both services consume Artifact instances and return ShareResult. Neither
service owns download acquisition (see docs/SHARE_ARCHITECTURE_CURRENT.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from rychlik.core.artifact import Artifact


class ShareStatus(Enum):
    CREATING = auto()
    ACTIVE = auto()
    OFFLINE = auto()
    EXPIRED = auto()
    REVOKED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class ShareRequest:
    artifact: Artifact


@dataclass(frozen=True)
class DeviceShareRequest(ShareRequest):
    target_device_id: str | None = None


@dataclass(frozen=True)
class LinkShareRequest(ShareRequest):
    pass


@dataclass(frozen=True)
class ShareResult:
    status: ShareStatus
    share_id: str
    error: str | None = None
