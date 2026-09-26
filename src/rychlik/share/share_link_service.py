"""Link Mode service: operates on the ShareLink domain model (Prompt 06).

Domain-only: no HTTP server, no public URL allocation, no transport
implementation. Does not depend on DeviceShareService in any way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from rychlik.share.contracts import LinkShareRequest, ShareResult, ShareStatus
from rychlik.share.share_link import InvalidShareLinkTransition, ShareLink, generate_share_id
from rychlik.share.share_link_repository import InMemoryShareLinkRepository, ShareLinkRepository

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ShareLinkService:
    def __init__(
        self,
        *,
        repository: ShareLinkRepository | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository or InMemoryShareLinkRepository()
        self._clock = clock

    def create_link(self, request: LinkShareRequest) -> ShareResult:
        artifact_id = request.artifact.artifact_id
        if not artifact_id:
            return ShareResult(status=ShareStatus.FAILED, share_id="", error="invalid artifact_id")

        link = ShareLink(
            share_id=generate_share_id(),
            artifact_id=artifact_id,
            created_at=self._clock(),
        )
        self._repository.save(link)
        return ShareResult(status=link.status, share_id=link.share_id)

    def get_link(self, share_id: str) -> ShareLink | None:
        return self._repository.get(share_id)

    def activate(self, share_id: str, *, public_url: str | None = None) -> ShareResult:
        return self._apply_transition(share_id, ShareStatus.ACTIVE, public_url=public_url)

    def mark_offline(self, share_id: str) -> ShareResult:
        return self._apply_transition(share_id, ShareStatus.OFFLINE)

    def expire(self, share_id: str) -> ShareResult:
        return self._apply_transition(share_id, ShareStatus.EXPIRED)

    def mark_failed(self, share_id: str) -> ShareResult:
        return self._apply_transition(share_id, ShareStatus.FAILED)

    def revoke_link(self, share_id: str) -> ShareResult:
        link = self._repository.get(share_id)
        if link is None:
            return ShareResult(status=ShareStatus.FAILED, share_id=share_id, error="unknown share_id")
        if link.status == ShareStatus.REVOKED:
            return ShareResult(status=ShareStatus.REVOKED, share_id=share_id)  # idempotent
        return self._apply_transition(share_id, ShareStatus.REVOKED)

    def _apply_transition(
        self, share_id: str, new_status: ShareStatus, *, public_url: str | None = None
    ) -> ShareResult:
        link = self._repository.get(share_id)
        if link is None:
            return ShareResult(status=ShareStatus.FAILED, share_id=share_id, error="unknown share_id")

        try:
            updated = link.transition_to(new_status, now=self._clock())
        except InvalidShareLinkTransition as exc:
            return ShareResult(status=link.status, share_id=share_id, error=str(exc))

        if public_url is not None:
            updated = updated.with_public_url(public_url)

        self._repository.save(updated)
        return ShareResult(status=updated.status, share_id=share_id)
