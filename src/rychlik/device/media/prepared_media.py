"""PreparedDeviceMedia + derived Artifact construction (Prompt A16
§58-64)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rychlik.core.artifact import Artifact, _sha256_of
from rychlik.device.media.planner import PlanKind, PlanWarning


@dataclass(frozen=True)
class PreparedDeviceMedia:
    source_artifact_id: str
    artifact: Artifact
    plan_kind: PlanKind
    target_profile_id: str | None
    temporary: bool
    warnings: tuple[PlanWarning, ...] = ()


def build_derived_artifact(
    output_path: Path, *, mime_type: str, display_filename: str, duration: float | None
) -> Artifact:
    """Reuses `Artifact`'s own hashing/size rules (§58) -- never a
    duplicate hash implementation. Unlike `Artifact.from_completed_
    download`, the MIME type is the preparer's own known-correct value
    (§132), never guessed from the temp cache's internal filename
    extension (§56/§57)."""
    if not output_path.is_file():
        raise FileNotFoundError(output_path)
    return Artifact(
        artifact_id=str(uuid.uuid4()),
        local_path=output_path,
        filename=display_filename,
        mime_type=mime_type,
        size=output_path.stat().st_size,
        sha256=_sha256_of(output_path),
        created_at=datetime.now(timezone.utc),
        source_url=None,
        duration=duration,
    )


def derive_display_filename(source_filename: str, media_kind: str) -> str:
    """§56: the receiver-visible filename may change extension (movie.mkv
    -> movie.mp4), but never exposes the internal cache path."""
    stem = source_filename.rsplit(".", 1)[0] if "." in source_filename else source_filename
    extension = "mp4" if media_kind == "video" else "m4a"
    return f"{stem}.{extension}"
