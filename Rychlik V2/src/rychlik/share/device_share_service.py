"""Device Mode: Artifact -> SendSession -> DeviceLink -> FriendSend Android.

Skeleton only (Prompt 05). Does not depend on ShareLinkService in any way.
"""

from __future__ import annotations

import uuid

from rychlik.share.contracts import DeviceShareRequest, ShareResult, ShareStatus


class DeviceShareService:
    def __init__(self) -> None:
        self._sessions: dict[str, DeviceShareRequest] = {}

    def start_share(self, request: DeviceShareRequest) -> ShareResult:
        share_id = str(uuid.uuid4())
        self._sessions[share_id] = request
        # Real pairing / FriendSend handoff is not implemented yet (skeleton).
        return ShareResult(status=ShareStatus.CREATING, share_id=share_id)

    def stop_share(self, share_id: str) -> ShareResult:
        if share_id not in self._sessions:
            return ShareResult(status=ShareStatus.FAILED, share_id=share_id, error="unknown share_id")
        del self._sessions[share_id]
        return ShareResult(status=ShareStatus.REVOKED, share_id=share_id)
