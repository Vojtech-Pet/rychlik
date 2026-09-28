"""Acquisition boundary: DownloadRequest -> AcquisitionService -> CompletedDownload.

GUI does not own acquisition state; it only observes progress callbacks and
receives CompletedDownload/AcquisitionError.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from rychlik.core.partial_transfer import PartialTransferState


class AcquisitionError(Exception):
    """Raised when a download cannot be completed. Never yields CompletedDownload."""


class DownloadCancelled(AcquisitionError):
    """Raised when a download is cancelled before completion."""


class DownloadPaused(AcquisitionError):
    """Raised when a download stops because of a cooperative pause request
    (Prompt A9) -- deliberately distinct from DownloadCancelled: a pause is
    NONTERMINAL (the task becomes PAUSED, resumable later), a cancellation
    is terminal. Only raised once the transfer loop has actually stopped
    and any forced durable checkpoint has been attempted -- never as an
    immediate reaction to the pause request alone (§7)."""


@dataclass(frozen=True)
class MediaOptions:
    """Typed options for a media (yt-dlp) acquisition: a page/stream URL rather than a plain file URL.
    Presence of these options on a DownloadRequest is what selects the media backend."""

    video_format: str = "bestvideo+bestaudio/best"
    referer: str | None = None
    rate_limit_bytes_per_second: int | None = None

    def __post_init__(self) -> None:
        if not self.video_format.strip():
            raise ValueError("video_format must not be empty")
        if self.rate_limit_bytes_per_second is not None and self.rate_limit_bytes_per_second <= 0:
            raise ValueError("rate_limit_bytes_per_second must be positive")


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    destination_dir: Path
    filename_hint: str | None = None
    media: MediaOptions | None = None

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


@dataclass(frozen=True)
class ResumeRequest:
    """Prompt A9: bridges DispatchCoordinator's durable partial-transfer
    checkpointing to the acquisition backend, without the backend ever
    importing sqlite3/state_store directly (mirrors the A8 `checkpoint`
    callback pattern). `initial_partial` has ALREADY been locally validated
    (path ownership, size, prefix SHA-256) by the caller via
    `rychlik.core.partial_transfer.plan_resume()` before this is
    constructed -- DirectHttpAcquisition only performs the REMOTE half of
    validation (If-Range / 206 / Content-Range) using it. `initial_partial
    =None` means "no resumable prefix, but still checkpoint this transfer
    for a possible future pause/resume/retry/crash"."""

    queue_entry_id: str
    task_id: str
    attempt_count: int
    initial_partial: "PartialTransferState | None"
    save_partial: Callable[["PartialTransferState"], None]
    clear_partial: Callable[[], None]
    checkpoint_bytes_threshold: int = 8 * 1024 * 1024
