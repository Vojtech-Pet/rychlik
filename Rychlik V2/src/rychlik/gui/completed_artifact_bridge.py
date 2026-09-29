"""Bridges a completed download (via DownloadManagerService's privileged
accessor) to the existing Artifact contract (Prompt A12).

Correct shape (per docs/FUNCTIONAL_GUI_HARDENING.md):

    DownloadManagerService.completed_file(queue_entry_id)
            |
            v
    Artifact.from_completed_download(local_path)   -- existing A? contract,
            |                                          reused unchanged
            v
    existing ShareDialog / ShareLinkService / DeviceShareService

This module never digs into DownloadRequest, searches the destination
folder, guesses a filename, or reaches into any A4 worker/result cache --
it only ever calls the one privileged accessor and the one existing
Artifact factory.
"""

from __future__ import annotations

from rychlik.core.artifact import Artifact, ArtifactError
from rychlik.core.download_manager_service import (
    CompletedFileResult,
    CompletedFileStatus,
    DownloadManagerService,
)


def build_artifact_for_completed(
    manager: DownloadManagerService, queue_entry_id: str
) -> tuple[Artifact | None, CompletedFileResult]:
    """Returns (artifact_or_None, the underlying CompletedFileResult) --
    callers use the result's `status`/`reason` for a truthful bounded
    message when `artifact` is None."""
    result = manager.completed_file(queue_entry_id)
    if result.status != CompletedFileStatus.AVAILABLE:
        return None, result
    try:
        artifact = Artifact.from_completed_download(result.info.local_path)
    except ArtifactError:
        return None, result
    return artifact, result
