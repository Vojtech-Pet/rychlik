"""Shared Artifact-change detection, used by LocalShareOrigin (Prompt 07) and
SharePreviewService (Prompt 08). One policy, reused, not reinvented per caller.
"""

from __future__ import annotations

from pathlib import Path

from rychlik.core.artifact import Artifact


class ArtifactChanged(Exception):
    """Raised when the on-disk file no longer matches the Artifact's recorded identity."""


def resolve_artifact_file(artifact: Artifact) -> Path:
    """Symlink policy: resolve once, require a regular file whose size still
    matches. Not TOCTOU-proof (no filesystem read is transactional)."""
    resolved = artifact.local_path.resolve(strict=True)
    if not resolved.is_file():
        raise ArtifactChanged("resolved path is not a regular file")
    size = resolved.stat().st_size
    if size != artifact.size:
        raise ArtifactChanged(f"size mismatch: expected {artifact.size}, found {size}")
    return resolved
