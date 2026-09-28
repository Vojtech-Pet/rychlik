"""Downloads the video behind a shared link into FriendSend's private cache (runs inside the app via Chaquopy).

Single-file formats only: the phone has no ffmpeg, so nothing that needs merging is requested. Progress and
cancellation are cooperative, checked in yt-dlp's progress hook.
"""

import json
import os
import re

import yt_dlp

_ALLOWED_EXTENSIONS = {"mp4", "webm", "mkv", "mov", "m4a", "mp3", "ts"}
_STREAM_PROTOCOLS = ("m3u8", "dash", "http_dash_segments")
_UNSAFE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")
MIME = {"mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska", "mov": "video/quicktime", "m4a": "audio/mp4", "mp3": "audio/mpeg", "ts": "video/mp2t"}


class Cancelled(Exception):
    pass


def _reject_non_media(info, *, incomplete=False):
    if incomplete:
        return None
    ext = str(info.get("ext") or "").lower()
    protocol = str(info.get("protocol") or "").lower()
    if ext in ("html", "htm", "php", "asp", "aspx"):
        return "The address is a web page, not a video"
    if ext not in _ALLOWED_EXTENSIONS and not any(p in protocol for p in _STREAM_PROTOCOLS):
        return "Unsupported media type: " + (ext or "unknown")
    return None


def _safe_name(title, ext):
    stem = _UNSAFE.sub("_", title or "").strip(" .")[:100] or "video"
    return stem + "." + ext


def download(url, out_dir, control):
    """`control` is a Kotlin object with isCancelled() and onProgress(done, total). Returns a JSON string
    {"path", "display_name", "mime", "size"}. Raises Cancelled, or RuntimeError with a short reason."""
    os.makedirs(out_dir, exist_ok=True)

    def hook(data):
        if control.isCancelled():
            raise Cancelled()
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            control.onProgress(int(data.get("downloaded_bytes") or 0), int(total))

    options = {
        "ignoreconfig": True,
        "format": "best[ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, "%(id).80B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "match_filter": _reject_non_media,
        "socket_timeout": 20,
        "retries": 2,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = info.get("requested_downloads") or []
            path = requested[0]["filepath"] if requested else ydl.prepare_filename(info)
    except Cancelled:
        raise
    except yt_dlp.utils.DownloadError as exc:
        cause = exc.exc_info[1] if exc.exc_info else None
        if isinstance(cause, Cancelled):
            raise cause
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
        last = (text.splitlines() or [""])[-1]
        if "Unsupported URL" in last or "web page, not a video" in last or "No video" in last:
            raise RuntimeError("No video found in this link. You can still send the link.")
        raise RuntimeError((last or "The video could not be downloaded")[:300])
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return json.dumps({
        "path": path,
        "display_name": _safe_name(info.get("title"), ext or "mp4"),
        "mime": MIME.get(ext, "application/octet-stream"),
        "size": os.path.getsize(path),
    })
