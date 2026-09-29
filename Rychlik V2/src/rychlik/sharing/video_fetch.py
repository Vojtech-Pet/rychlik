"""Fetches the video behind a link (YouTube, Vimeo, ...) into a private temp cache, for "Send video" -- never the
download queue/history: this is a throwaway copy meant to be handed to another app (via the clipboard) and
forgotten, not a download the user manages. Mirrors FriendSend's on-phone video cache (`VideoDownloader.kt`):
same private directory + purge-after-an-hour policy, same yt-dlp-recognised-page precondition.
"""

from __future__ import annotations

import mimetypes
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rychlik.acquisition.contracts import AcquisitionError, DownloadCancelled, DownloadRequest, MediaOptions
from rychlik.acquisition.media_ytdlp import MediaAcquisition, known_video_page

ProgressCallback = "Callable[[int, int | None], None]"  # documentation only; see acquire()'s real signature


class VideoFetchError(Exception):
    """A bounded, user-facing reason; never a raw exception message from yt-dlp/requests."""


class VideoFetchCancelled(Exception):
    pass


@dataclass(frozen=True)
class FetchedVideo:
    path: Path
    display_name: str
    mime_type: str
    size: int


def _default_cache_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "rychlik" / "video_cache"


class VideoFetchService:
    def __init__(self, cache_dir: Path | None = None, *, max_age_seconds: float = 3600.0) -> None:
        self._dir = cache_dir or _default_cache_dir()
        self._max_age = max_age_seconds
        self._backend = MediaAcquisition()

    @property
    def directory(self) -> Path:
        return self._dir

    def purge_old(self) -> None:
        if not self._dir.is_dir():
            return
        cutoff = time.time() - self._max_age
        for entry in self._dir.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                pass

    def fetch(self, url: str, *, progress_callback=None, cancel_event: threading.Event | None = None) -> FetchedVideo:
        """Blocking; call off the GUI thread. Raises VideoFetchError or VideoFetchCancelled."""
        url = url.strip()
        if not known_video_page(url):
            raise VideoFetchError("This does not look like a link to a video (no site recognised).")
        self.purge_old()
        self._dir.mkdir(parents=True, exist_ok=True)
        request = DownloadRequest(url=url, destination_dir=self._dir, media=MediaOptions())
        try:
            completed = self._backend.acquire(request, progress_callback=progress_callback, cancel_event=cancel_event)
        except DownloadCancelled as exc:
            raise VideoFetchCancelled() from exc
        except AcquisitionError as exc:
            raise VideoFetchError(str(exc) or "The video could not be fetched.") from exc
        mime, _ = mimetypes.guess_type(completed.final_path.name)
        return FetchedVideo(
            path=completed.final_path,
            display_name=completed.display_name,
            mime_type=mime or "application/octet-stream",
            size=completed.size if completed.size is not None else completed.final_path.stat().st_size,
        )
