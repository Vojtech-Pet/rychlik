"""Smallest persistence seam for ShareLink. In-memory only for this phase."""

from __future__ import annotations

from typing import Protocol

from rychlik.share.share_link import ShareLink


class ShareLinkRepository(Protocol):
    def save(self, link: ShareLink) -> None: ...

    def get(self, share_id: str) -> ShareLink | None: ...


class InMemoryShareLinkRepository:
    def __init__(self) -> None:
        self._links: dict[str, ShareLink] = {}

    def save(self, link: ShareLink) -> None:
        self._links[link.share_id] = link

    def get(self, share_id: str) -> ShareLink | None:
        return self._links.get(share_id)
