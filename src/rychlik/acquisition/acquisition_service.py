"""AcquisitionService: thin dispatcher in front of acquisition backends.

Owns nothing about sharing. Produces CompletedDownload only; the GUI/caller
is responsible for turning that into an Artifact via
Artifact.from_completed_download(...).
"""

from __future__ import annotations

import threading

from rychlik.acquisition.contracts import CompletedDownload, DownloadRequest
from rychlik.acquisition.direct_http import DirectHttpAcquisition, ProgressCallback


class AcquisitionService:
    def __init__(self, *, http_backend: DirectHttpAcquisition | None = None) -> None:
        self._http_backend = http_backend or DirectHttpAcquisition()

    def acquire(
        self,
        request: DownloadRequest,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CompletedDownload:
        # Only one backend exists in this phase (Prompt 04.5). Backend selection
        # by URL scheme/site (yt-dlp, etc.) is deferred.
        return self._http_backend.acquire(
            request, progress_callback=progress_callback, cancel_event=cancel_event
        )
