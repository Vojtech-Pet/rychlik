"""Link Mode: Artifact -> ShareLink (Prompt 06).

Skeleton only. Does not depend on DeviceShareService in any way.
Transport (local origin, tunnel, web player) lands in later prompts.
"""

from __future__ import annotations

import secrets

from rychlik.share.contracts import LinkShareRequest, ShareResult, ShareStatus

_SHARE_ID_BYTES = 16  # 128 bits of entropy, per Prompt 06 requirement


class ShareLinkService:
    def __init__(self) -> None:
        self._links: dict[str, LinkShareRequest] = {}

    def create_link(self, request: LinkShareRequest) -> ShareResult:
        share_id = secrets.token_urlsafe(_SHARE_ID_BYTES)
        self._links[share_id] = request
        return ShareResult(status=ShareStatus.CREATING, share_id=share_id)

    def revoke_link(self, share_id: str) -> ShareResult:
        if share_id not in self._links:
            return ShareResult(status=ShareStatus.FAILED, share_id=share_id, error="unknown share_id")
        del self._links[share_id]
        return ShareResult(status=ShareStatus.REVOKED, share_id=share_id)
