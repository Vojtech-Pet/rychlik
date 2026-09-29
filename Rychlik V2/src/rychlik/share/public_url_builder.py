"""Centralized absolute-URL construction for Link Mode routes (Prompt 09 §6/§7).

Domain models (ShareLink, SharePreview) never hardcode a host — this is the
only place route strings are assembled. For Prompt 09, callers typically
construct this from the local server's bound 127.0.0.1:<port>; a future
tunnel/public-domain phase supplies a different base_url without touching
anything else.
"""

from __future__ import annotations


class PublicUrlBuilder:
    def __init__(self, base_url: str) -> None:
        base = base_url.rstrip("/")
        if not base:
            raise ValueError("base_url must not be empty")
        self.base_url = base

    def share_page_url(self, share_id: str) -> str:
        return f"{self.base_url}/s/{share_id}"

    def preview_url(self, share_id: str) -> str:
        return f"{self.base_url}/preview/{share_id}"

    def media_url(self, share_id: str) -> str:
        return f"{self.base_url}/media/{share_id}"
