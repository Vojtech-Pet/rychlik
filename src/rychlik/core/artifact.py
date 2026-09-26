"""Artifact: the shared boundary object between download acquisition and sharing."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class ArtifactError(Exception):
    """Raised when an Artifact cannot be constructed from a given file."""


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    local_path: Path
    filename: str
    mime_type: str
    size: int
    sha256: str
    created_at: datetime
    source_url: str | None = None
    duration: float | None = None
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def from_completed_download(
        local_path: str | Path,
        *,
        source_url: str | None = None,
        duration: float | None = None,
        metadata: dict | None = None,
    ) -> "Artifact":
        path = Path(local_path)
        if not path.is_file():
            raise ArtifactError(f"file does not exist: {path}")

        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type is None:
            mime_type = "application/octet-stream"

        return Artifact(
            artifact_id=str(uuid.uuid4()),
            local_path=path,
            filename=path.name,
            mime_type=mime_type,
            size=path.stat().st_size,
            sha256=_sha256_of(path),
            created_at=datetime.now(timezone.utc),
            source_url=source_url,
            duration=duration,
            metadata=dict(metadata or {}),
        )

    def to_public_dict(self) -> dict:
        """Fields safe to expose to a share recipient. Never includes local_path or source_url."""
        return {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "duration": self.duration,
        }


def _sha256_of(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
