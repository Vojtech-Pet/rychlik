"""SharePreview domain model (Prompt 08).

No Open Graph page exists yet. No public thumbnail URL exists yet. No
public internet exposure exists yet. thumbnail_path is an internal
filesystem reference only — a future HTTP layer maps it to a public route
(e.g. /preview/<share_id>), it is never exposed directly (see to_public_dict).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path

PREVIEW_VERSION = 1


class MediaKind(Enum):
    VIDEO = auto()
    AUDIO = auto()
    IMAGE = auto()
    DOCUMENT = auto()
    GENERIC_FILE = auto()


def classify_media_kind(mime_type: str | None) -> MediaKind:
    mime = (mime_type or "").lower()
    if mime.startswith("video/"):
        return MediaKind.VIDEO
    if mime.startswith("audio/"):
        return MediaKind.AUDIO
    if mime.startswith("image/"):
        return MediaKind.IMAGE
    if mime == "application/pdf":
        return MediaKind.DOCUMENT
    return MediaKind.GENERIC_FILE


def derive_title(filename: str) -> str:
    """filename is always Artifact.filename (a bare basename, never a path
    per Artifact's own construction rule), so this can never leak a directory."""
    stem = Path(filename).stem.strip()
    return stem or "Shared file"


def select_thumbnail_timestamp(duration_seconds: float | None) -> float:
    """Representative-frame strategy: ~15% into the video for anything >= 10s
    (never past duration - 0.5s), else a safe early point for short clips."""
    if duration_seconds is None or duration_seconds <= 0:
        return 0.0
    if duration_seconds >= 10:
        return min(duration_seconds * 0.15, duration_seconds - 0.5)
    return min(1.0, duration_seconds / 2)


def derive_description(
    media_kind: MediaKind,
    *,
    duration_seconds: float | None,
    width: int | None,
    height: int | None,
    size_bytes: int,
) -> str:
    if media_kind is MediaKind.VIDEO:
        parts = ["Video"]
        if width and height:
            parts.append(f"{width}×{height}")
        if duration_seconds is not None:
            parts.append(_format_duration(duration_seconds))
        return " • ".join(parts)
    if media_kind is MediaKind.AUDIO:
        if duration_seconds is not None:
            return f"Audio • {_format_duration(duration_seconds)}"
        return "Audio"
    if media_kind is MediaKind.IMAGE:
        if width and height:
            return f"Image • {width}×{height}"
        return "Image"
    if media_kind is MediaKind.DOCUMENT:
        return "PDF document"
    return f"File • {_human_size(size_bytes)}"


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@dataclass(frozen=True)
class SharePreview:
    artifact_id: str
    title: str
    description: str
    media_kind: MediaKind
    created_at: datetime
    preview_version: int = PREVIEW_VERSION
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    thumbnail_path: Path | None = None
    thumbnail_mime: str | None = None

    def to_public_dict(self) -> dict:
        """Safe for a future Open Graph / preview HTTP response. Excludes
        artifact_id, thumbnail_path (internal filesystem reference), and
        thumbnail_mime (paired with the internal path, not useful alone)."""
        return {
            "title": self.title,
            "description": self.description,
            "media_kind": self.media_kind.name,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
        }
