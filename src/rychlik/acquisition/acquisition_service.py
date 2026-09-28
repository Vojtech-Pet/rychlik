"""AcquisitionService: thin dispatcher in front of acquisition backends.

Owns nothing about sharing. Produces CompletedDownload only; the GUI/caller
is responsible for turning that into an Artifact via
Artifact.from_completed_download(...).
"""

from __future__ import annotations

import threading

from rychlik.acquisition.contracts import CompletedDownload, DownloadRequest, ResumeRequest
from rychlik.acquisition.direct_http import DirectHttpAcquisition, ProgressCallback
from rychlik.acquisition.media_ytdlp import MediaAcquisition


class AcquisitionService:
    def __init__(self, *, http_backend: DirectHttpAcquisition | None = None, media_backend: MediaAcquisition | None = None) -> None:
        self._http_backend = http_backend or DirectHttpAcquisition()
        self._media_backend = media_backend or MediaAcquisition()

    def acquire(
        self,
        request: DownloadRequest,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        resume: ResumeRequest | None = None,
    ) -> CompletedDownload:
        # A request carrying MediaOptions goes to the yt-dlp media backend; everything else is a plain
        # HTTP file. `pause_event`/`resume` are optional passthroughs; this dispatcher owns no resume logic.
        backend = self._media_backend if request.media is not None else self._http_backend
        return backend.acquire(
            request,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            pause_event=pause_event,
            resume=resume,
        )
