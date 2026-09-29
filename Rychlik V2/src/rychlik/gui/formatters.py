"""Pure, UI-only presentation helpers (Prompt A11).

These format A7's raw DownloadViewSnapshot values (progress_fraction,
speed_bps, eta_seconds) and the A2/A1 state enums into short display
strings. Nothing here mutates backend state or reinterprets raw values
for logic -- callers keep using task_state/queue_state directly for any
decision, never the derived label (§27/§28).

Units: decimal (1000-based: KB/MB/GB), matching common download-manager
UX conventions -- not binary KiB/MiB. A7's raw bytes/bytes-per-second
units are never altered, only displayed.
"""

from __future__ import annotations

from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState

_UNITS = ("B", "KB", "MB", "GB", "TB")


def _scale(value: float) -> str:
    magnitude = float(value)
    unit_index = 0
    while magnitude >= 1000 and unit_index < len(_UNITS) - 1:
        magnitude /= 1000
        unit_index += 1
    if unit_index == 0:
        return f"{int(magnitude)} {_UNITS[unit_index]}"
    return f"{magnitude:.1f} {_UNITS[unit_index]}"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "—"  # em dash
    return _scale(value)


def format_speed(speed_bps: float | None) -> str:
    if speed_bps is None:
        return "—"
    return f"{_scale(speed_bps)}/s"


def format_eta(eta_seconds: float | None) -> str:
    if eta_seconds is None:
        return "—"
    total = int(eta_seconds)
    if total < 60:
        return f"{total} s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_progress(progress_fraction: float | None) -> str:
    if progress_fraction is None:
        return "—"
    return f"{round(progress_fraction * 100)} %"


def format_downloaded_total(bytes_downloaded: int, total_bytes: int | None) -> str:
    total = format_bytes(total_bytes)
    return f"{format_bytes(bytes_downloaded)} / {total}"


_PRIORITY_LABELS = {
    QueuePriority.HIGH: "High",
    QueuePriority.NORMAL: "Normal",
    QueuePriority.LOW: "Low",
}


def format_priority(priority: QueuePriority) -> str:
    return _PRIORITY_LABELS[priority]


def derive_status_text(task_state: DownloadTaskState, queue_state: QueueEntryState) -> str:
    """A UI-only derived label (§27) -- never written back into backend
    state, never used by any command/enablement decision (those always
    read task_state/queue_state directly, §28)."""
    if task_state == DownloadTaskState.COMPLETED:
        return "Completed"
    if task_state == DownloadTaskState.FAILED:
        return "Failed"
    if task_state == DownloadTaskState.CANCELLED:
        return "Cancelled"
    if task_state == DownloadTaskState.PAUSED:
        return "Paused"
    if task_state == DownloadTaskState.RETRY_WAIT:
        return "Waiting to retry"
    if task_state == DownloadTaskState.TRANSFERRING:
        return "Downloading"
    if task_state == DownloadTaskState.VERIFYING:
        return "Verifying"
    if task_state == DownloadTaskState.POST_PROCESSING:
        return "Finishing"
    if task_state == DownloadTaskState.RESOLVING:
        return "Resolving"
    # CREATED / READY
    if queue_state == QueueEntryState.PAUSED:
        return "On hold"
    return "Waiting"
