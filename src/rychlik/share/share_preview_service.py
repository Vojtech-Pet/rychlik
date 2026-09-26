"""SharePreviewService (Prompt 08).

Resolves an Artifact, derives safe title/description, probes optional
video/image metadata, generates and caches a thumbnail. Does not create
ShareLinks, run an HTTP server, generate Open Graph HTML, or allocate public
URLs — those are later phases.

Cache layout:

    cache_dir/<artifact.sha256>/preview-v<PREVIEW_VERSION>.json  (metadata)
    cache_dir/<artifact.sha256>/preview-v<PREVIEW_VERSION>.jpg   (thumbnail, if any)

Keyed by artifact identity (sha256) + preview_version, per Prompt 08 §12/§14.
A changed Artifact produces a different sha256 -> a different cache
directory -> no stale reuse, structurally, without needing extra bookkeeping.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rychlik.core.artifact import Artifact
from rychlik.core.artifact_integrity import ArtifactChanged, resolve_artifact_file
from rychlik.core.artifact_repository import ArtifactRepository
from rychlik.share.media_probe import probe_media
from rychlik.share.share_preview import (
    PREVIEW_VERSION,
    MediaKind,
    SharePreview,
    classify_media_kind,
    derive_description,
    derive_title,
    select_thumbnail_timestamp,
)
from rychlik.share.thumbnail_generator import generate_thumbnail

Clock = Callable[[], datetime]

_THUMBNAIL_KINDS = (MediaKind.VIDEO, MediaKind.IMAGE)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SharePreviewService:
    def __init__(
        self,
        *,
        artifact_repository: ArtifactRepository,
        cache_dir: Path,
        ffprobe_path: str = "ffprobe",
        ffmpeg_path: str = "ffmpeg",
        clock: Clock = _utc_now,
    ) -> None:
        self._artifact_repository = artifact_repository
        self._cache_dir = cache_dir
        self._ffprobe_path = ffprobe_path
        self._ffmpeg_path = ffmpeg_path
        self._clock = clock

    def get_or_create_preview(self, artifact_id: str) -> SharePreview | None:
        artifact = self._artifact_repository.get(artifact_id)
        if artifact is None:
            return None

        try:
            resolve_artifact_file(artifact)
        except (FileNotFoundError, ArtifactChanged):
            return None  # Prompt 07's Artifact-change policy, reused as-is

        artifact_cache_dir = self._cache_dir / artifact.sha256
        meta_path = artifact_cache_dir / f"preview-v{PREVIEW_VERSION}.json"
        thumb_path = artifact_cache_dir / f"preview-v{PREVIEW_VERSION}.jpg"

        cached = self._load_cache(meta_path, thumb_path)
        if cached is not None:
            return cached

        return self._generate(artifact, meta_path, thumb_path)

    def _generate(self, artifact: Artifact, meta_path: Path, thumb_path: Path) -> SharePreview:
        media_kind = classify_media_kind(artifact.mime_type)
        title = derive_title(artifact.filename)

        duration: float | None = None
        width: int | None = None
        height: int | None = None
        has_thumbnail = False

        if media_kind in _THUMBNAIL_KINDS:
            probe = probe_media(artifact.local_path, ffprobe_path=self._ffprobe_path)
            if probe is not None:
                duration, width, height = probe.duration, probe.width, probe.height

            timestamp = select_thumbnail_timestamp(duration) if media_kind is MediaKind.VIDEO else 0.0
            has_thumbnail = generate_thumbnail(
                artifact.local_path,
                output_path=thumb_path,
                timestamp=timestamp,
                ffmpeg_path=self._ffmpeg_path,
            )

        description = derive_description(
            media_kind,
            duration_seconds=duration,
            width=width,
            height=height,
            size_bytes=artifact.size,
        )

        preview = SharePreview(
            artifact_id=artifact.artifact_id,
            title=title,
            description=description,
            media_kind=media_kind,
            created_at=self._clock(),
            preview_version=PREVIEW_VERSION,
            duration_seconds=duration,
            width=width,
            height=height,
            thumbnail_path=thumb_path if has_thumbnail else None,
            thumbnail_mime="image/jpeg" if has_thumbnail else None,
        )
        self._save_cache(meta_path, preview, has_thumbnail=has_thumbnail)
        return preview

    @staticmethod
    def _load_cache(meta_path: Path, thumb_path: Path) -> SharePreview | None:
        if not meta_path.is_file():
            return None
        try:
            data = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        if data.get("preview_version") != PREVIEW_VERSION:
            return None

        has_thumbnail = bool(data.get("has_thumbnail"))
        if has_thumbnail and not (thumb_path.is_file() and thumb_path.stat().st_size > 0):
            return None  # cache says a thumbnail exists but the file is gone/empty: regenerate

        try:
            return SharePreview(
                artifact_id=data["artifact_id"],
                title=data["title"],
                description=data["description"],
                media_kind=MediaKind[data["media_kind"]],
                created_at=datetime.fromisoformat(data["created_at"]),
                preview_version=data["preview_version"],
                duration_seconds=data.get("duration_seconds"),
                width=data.get("width"),
                height=data.get("height"),
                thumbnail_path=thumb_path if has_thumbnail else None,
                thumbnail_mime=data.get("thumbnail_mime"),
            )
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _save_cache(meta_path: Path, preview: SharePreview, *, has_thumbnail: bool) -> None:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "artifact_id": preview.artifact_id,
            "title": preview.title,
            "description": preview.description,
            "media_kind": preview.media_kind.name,
            "created_at": preview.created_at.isoformat(),
            "preview_version": preview.preview_version,
            "duration_seconds": preview.duration_seconds,
            "width": preview.width,
            "height": preview.height,
            "has_thumbnail": has_thumbnail,
            "thumbnail_mime": preview.thumbnail_mime,
        }
        tmp_path = meta_path.with_name(meta_path.name + ".tmp")
        tmp_path.write_text(json.dumps(data))
        tmp_path.replace(meta_path)
