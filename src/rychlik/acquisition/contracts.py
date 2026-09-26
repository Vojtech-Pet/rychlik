"""Acquisition boundary: DownloadRequest -> AcquisitionService -> CompletedDownload.

GUI does not own acquisition state; it only observes progress callbacks and
receives CompletedDownload/AcquisitionError.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class AcquisitionError(Exception):
    """Raised when a download cannot be completed. Never yields CompletedDownload."""


class DownloadCancelled(AcquisitionError):
    """Raised when a download is cancelled before completion."""


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    destination_dir: Path
    filename_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url must not be empty")
        if not str(self.url).lower().startswith(("http://", "https://")):
            raise ValueError(f"only http/https URLs are supported in this phase: {self.url!r}")


@dataclass(frozen=True)
class CompletedDownload:
    final_path: Path
    display_name: str
    source_url: str
    mime_type: str | None = None
    size: int | None = None
