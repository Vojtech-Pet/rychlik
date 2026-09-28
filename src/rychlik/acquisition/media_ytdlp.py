"""Media acquisition backend: page/stream URLs resolved and downloaded with yt-dlp.

Same contract as DirectHttpAcquisition (progress callback, cancel/pause events, CompletedDownload or an
AcquisitionError). Pause and cancel are cooperative and checked in yt-dlp's progress hook: a pause stops the
run and keeps yt-dlp's own `.part` file (yt-dlp continues it on the next attempt); a cancel deletes the files
this attempt created. The core never learns anything about yt-dlp beyond this module.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable

from rychlik.acquisition.contracts import (
    AcquisitionError,
    CompletedDownload,
    DownloadCancelled,
    DownloadPaused,
    DownloadRequest,
    ResumeRequest,
)

ProgressCallback = Callable[[int, int | None], None]

_ALLOWED_EXTENSIONS = frozenset({"mp4", "webm", "mkv", "mov", "m4a", "mp3", "ts"})
_STREAM_PROTOCOLS = ("m3u8", "dash", "http_dash_segments")
_HTML_EXTENSIONS = frozenset({"html", "htm", "php", "asp", "aspx"})
_UNSAFE_NAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


class _Stopped(Exception):
    """Raised inside yt-dlp's hook to unwind its run; translated to the acquisition exceptions below."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def reject_non_media(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
    if incomplete:
        return None
    extension = str(info.get("ext") or "").lower()
    protocol = str(info.get("protocol") or "").lower()
    if extension in _HTML_EXTENSIONS:
        return "The address points to an HTML page, not a video stream"
    if extension not in _ALLOWED_EXTENSIONS and not any(name in protocol for name in _STREAM_PROTOCOLS):
        return f"Unsupported media type: {extension or 'unknown'}"
    return None


def output_template(filename_hint: str | None) -> str:
    if filename_hint:
        stem = _UNSAFE_NAME.sub("_", Path(filename_hint).stem if Path(filename_hint).suffix else filename_hint).strip(" .")[:180]
        if stem:
            return stem.replace("%", "%%") + ".%(ext)s"
    return "%(title).180B.%(ext)s"


class MediaAcquisition:
    def acquire(
        self,
        request: DownloadRequest,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        resume: ResumeRequest | None = None,  # yt-dlp owns its own continuation; core partial state is not used
    ) -> CompletedDownload:
        if request.media is None:
            raise ValueError("MediaAcquisition needs request.media")
        try:
            import yt_dlp
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise AcquisitionError("Media downloads need yt-dlp, which is not installed") from exc

        request.destination_dir.mkdir(parents=True, exist_ok=True)
        touched: set[str] = set()

        def hook(data: dict[str, Any]) -> None:
            for key in ("filename", "tmpfilename"):
                if data.get(key):
                    touched.add(str(data[key]))
            if cancel_event is not None and cancel_event.is_set():
                raise _Stopped("cancel")
            if pause_event is not None and pause_event.is_set():
                raise _Stopped("pause")
            if data.get("status") == "downloading" and progress_callback is not None:
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                progress_callback(int(data.get("downloaded_bytes") or 0), int(total) if total else None)

        options: dict[str, Any] = {
            "ignoreconfig": True,
            "format": request.media.video_format,
            "merge_output_format": "mp4",
            "outtmpl": str(request.destination_dir / output_template(request.filename_hint)),
            "noplaylist": True,
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "match_filter": reject_non_media,
            "continuedl": True,
        }
        if request.media.referer:
            options["http_headers"] = {"Referer": request.media.referer}
        if request.media.rate_limit_bytes_per_second:
            options["ratelimit"] = request.media.rate_limit_bytes_per_second

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(request.url, download=True)
                requested = info.get("requested_downloads") or []
                final = Path(requested[0]["filepath"] if requested else ydl.prepare_filename(info))
        except _Stopped as stop:
            self._finish_stop(stop, touched)
            raise  # unreachable: _finish_stop always raises
        except yt_dlp.utils.DownloadError as exc:
            cause = exc.exc_info[1] if exc.exc_info else None
            if isinstance(cause, _Stopped):
                self._finish_stop(cause, touched)
            if cancel_event is not None and cancel_event.is_set():
                self._finish_stop(_Stopped("cancel"), touched)
            raise AcquisitionError(_clean_message(exc)) from exc

        if not final.exists():
            raise AcquisitionError("The download finished but the file is missing")
        size = final.stat().st_size
        if progress_callback is not None:
            progress_callback(size, size)
        return CompletedDownload(final_path=final, display_name=final.name, source_url=request.url, size=size)

    @staticmethod
    def _finish_stop(stop: _Stopped, touched: set[str]) -> None:
        if stop.kind == "pause":
            raise DownloadPaused("media download paused")
        for name in touched:  # cancel: remove what this attempt wrote, including yt-dlp's resume file
            for candidate in (name, name + ".part", name + ".ytdl"):
                try:
                    Path(candidate).unlink()
                except OSError:
                    pass
        raise DownloadCancelled("media download cancelled")


def _clean_message(exc: Exception) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    return (text.splitlines()[-1] if text else "The media download failed")[:300]
