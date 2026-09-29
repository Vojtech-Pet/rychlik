"""Pure (Qt-free) presentation rules for the approved Rýchlik Desktop design.

Everything here is a function of DownloadViewSnapshot values: how a status is
labelled, which actions are valid, where a row sits inside its priority band,
which columns fit a window width. Nothing here mutates backend state, and
nothing guesses: an action is offered only when the existing
DownloadManagerService contract allows it for that state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot

_TERMINAL = frozenset({DownloadTaskState.COMPLETED, DownloadTaskState.FAILED, DownloadTaskState.CANCELLED})

# --- status ------------------------------------------------------------------


@dataclass(frozen=True)
class StatusInfo:
    key: str  # downloading | retrying | waiting | paused | completed | failed | cancelled | resolving | verifying | finishing
    label: str
    glyph: str  # icon registry name
    tone: str  # info | neutral | warning | success | error
    held: bool  # queue-level hold; a separate attribute, never a replacement for the task status


def is_terminal(item: DownloadViewSnapshot) -> bool:
    return item.task_state in _TERMINAL


def is_live(item: DownloadViewSnapshot) -> bool:
    return item.queue_state != QueueEntryState.REMOVED and not is_terminal(item)


def status_info(item: DownloadViewSnapshot) -> StatusInfo:
    held = item.queue_state == QueueEntryState.PAUSED and not is_terminal(item)
    state = item.task_state
    if state == DownloadTaskState.TRANSFERRING:
        return StatusInfo("downloading", "Downloading", "arrow-down", "info", held)
    if state == DownloadTaskState.RETRY_WAIT:
        seconds = item.retry_in_seconds
        label = "Retrying" if seconds is None else f"Retrying in {max(0, round(seconds))} s"
        return StatusInfo("retrying", label, "refresh", "info", held)
    if state == DownloadTaskState.PAUSED:
        return StatusInfo("paused", "Paused", "pause", "warning", held)
    if state == DownloadTaskState.COMPLETED:
        return StatusInfo("completed", "Completed", "check", "success", False)
    if state == DownloadTaskState.FAILED:
        return StatusInfo("failed", "Failed", "x", "error", False)
    if state == DownloadTaskState.CANCELLED:
        return StatusInfo("cancelled", "Cancelled", "x-circle", "neutral", False)
    if state == DownloadTaskState.RESOLVING:
        return StatusInfo("resolving", "Resolving", "clock", "info", held)
    if state == DownloadTaskState.VERIFYING:
        return StatusInfo("verifying", "Verifying", "clock", "info", held)
    if state == DownloadTaskState.POST_PROCESSING:
        return StatusInfo("finishing", "Finishing", "clock", "info", held)
    return StatusInfo("waiting", "Waiting", "clock", "neutral", held)  # CREATED / READY


SIDEBAR_FILTERS = ("All", "Downloading", "Waiting", "Paused", "Completed", "Failed")
STATUS_COMBO_FILTERS = ("All", "Downloading", "Waiting", "Paused", "Completed", "Failed", "Cancelled")


def matches_status_filter(item: DownloadViewSnapshot, key: str) -> bool:
    if key == "All":
        return True
    info = status_info(item)
    if key == "Downloading":
        return info.key in ("downloading", "resolving", "verifying", "finishing")
    if key == "Waiting":
        return info.key in ("waiting", "retrying")
    if key == "Paused":
        return info.key == "paused"
    if key == "Completed":
        return info.key == "completed"
    if key == "Failed":
        return info.key == "failed"
    if key == "Cancelled":
        return info.key == "cancelled"
    raise ValueError(f"unknown status filter {key!r}")


# --- file type / category -------------------------------------------------------

CATEGORIES = ("Video", "Music", "Images", "Documents", "Archives", "Other")
_EXT_CATEGORY = {
    **dict.fromkeys(("mp4", "mkv", "webm", "avi", "mov", "m4v", "wmv", "flv", "mpg", "mpeg", "ts"), "Video"),
    **dict.fromkeys(("mp3", "flac", "wav", "ogg", "m4a", "aac", "opus", "wma", "aiff"), "Music"),
    **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tif", "tiff", "heic", "avif"), "Images"),
    **dict.fromkeys(("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "odt", "ods", "odp", "csv", "md"), "Documents"),
    **dict.fromkeys(("zip", "rar", "7z", "tar", "gz", "xz", "zst", "bz2", "tgz", "tbz2", "lz4"), "Archives"),
}
_CATEGORY_ICON = {"Video": "video", "Music": "music", "Images": "image", "Documents": "doc", "Archives": "archive", "Other": "box"}


def extension_of(name: str | None) -> str:
    if not name:
        return ""
    stem, dot, ext = name.rstrip().rpartition(".")
    return ext.lower() if dot and stem else ""


def category_for(name: str | None) -> str:
    return _EXT_CATEGORY.get(extension_of(name), "Other")


def category_icon(category: str) -> str:
    return _CATEGORY_ICON.get(category, "box")


def type_label(name: str | None) -> str:
    ext = extension_of(name)
    return ext.upper() if 0 < len(ext) <= 8 else "—"


# --- priority bands ----------------------------------------------------------------

PRIORITY_ORDER = (QueuePriority.HIGH, QueuePriority.NORMAL, QueuePriority.LOW)


def band_positions(items) -> dict[str, tuple[int, int]]:
    """queue_entry_id -> (1-based position inside its priority band, band size), live items only.
    Uses the snapshot's own order, which is the scheduler's canonical order."""
    by_band: dict[QueuePriority, list[str]] = {p: [] for p in PRIORITY_ORDER}
    for item in items:
        if is_live(item):
            by_band[item.priority].append(item.queue_entry_id)
    out: dict[str, tuple[int, int]] = {}
    for ids in by_band.values():
        for index, entry_id in enumerate(ids, 1):
            out[entry_id] = (index, len(ids))
    return out


def band_neighbor(items, queue_entry_id: str, direction: int) -> str | None:
    """The queue_entry_id directly before (-1) / after (+1) `queue_entry_id` in its own band."""
    live = [i for i in items if is_live(i)]
    target = next((i for i in live if i.queue_entry_id == queue_entry_id), None)
    if target is None:
        return None
    band = [i.queue_entry_id for i in live if i.priority == target.priority]
    index = band.index(queue_entry_id) + direction
    return band[index] if 0 <= index < len(band) else None


# --- actions -----------------------------------------------------------------------


class RowAction(Enum):
    PAUSE = "pause"
    RESUME = "resume"
    HOLD = "hold"
    RELEASE = "release"
    RETRY_NOW = "retry_now"
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    CANCEL = "cancel"
    OPEN = "open"
    OPEN_FOLDER = "open_folder"
    SHARE = "share"
    DETAILS = "details"


@dataclass(frozen=True)
class ActionState:
    visible: bool
    enabled: bool = True
    reason: str | None = None


def available_actions(item: DownloadViewSnapshot, *, band_position: tuple[int, int] | None = None) -> dict[RowAction, ActionState]:
    """Which actions this occurrence supports right now.

    Mirrors the DownloadManagerService contract: Pause only while TRANSFERRING, Resume only when the
    task is PAUSED, Retry now only in RETRY_WAIT, Hold/Release are the queue-level flag (never Pause),
    reorder only inside the priority band, Open/Share only for COMPLETED.
    FAILED and CANCELLED items have no queue actions (the backend has no retry for them)."""
    live = is_live(item)
    held = item.queue_state == QueueEntryState.PAUSED
    completed = item.task_state == DownloadTaskState.COMPLETED
    hidden = ActionState(False, False)
    out = {a: hidden for a in RowAction}
    out[RowAction.DETAILS] = ActionState(True)
    if live:
        out[RowAction.PAUSE] = ActionState(item.task_state == DownloadTaskState.TRANSFERRING)
        out[RowAction.RESUME] = ActionState(item.task_state == DownloadTaskState.PAUSED)
        out[RowAction.RETRY_NOW] = ActionState(item.task_state == DownloadTaskState.RETRY_WAIT)
        out[RowAction.HOLD] = ActionState(not held)
        out[RowAction.RELEASE] = ActionState(held)
        out[RowAction.CANCEL] = ActionState(True)
        index, size = band_position if band_position is not None else (1, 1)
        out[RowAction.MOVE_UP] = ActionState(True, index > 1, None if index > 1 else "Already first in its priority")
        out[RowAction.MOVE_DOWN] = ActionState(True, index < size, None if index < size else "Already last in its priority")
    if completed:
        out[RowAction.OPEN] = ActionState(True)
        out[RowAction.OPEN_FOLDER] = ActionState(True)
        out[RowAction.SHARE] = ActionState(True)
    return out


def hover_actions(item: DownloadViewSnapshot) -> tuple[RowAction, ...]:
    """The (at most two) primary state-valid actions shown on row hover, in display order."""
    a = available_actions(item)
    status = status_info(item).key
    if status == "downloading":
        return (RowAction.PAUSE, RowAction.CANCEL)
    if status == "retrying":
        return (RowAction.RETRY_NOW, RowAction.CANCEL)
    if status == "paused":
        return (RowAction.RESUME, RowAction.CANCEL)
    if item.task_state in (DownloadTaskState.CREATED, DownloadTaskState.READY, DownloadTaskState.RESOLVING):
        if a[RowAction.RELEASE].visible and a[RowAction.RELEASE].enabled:
            return (RowAction.RELEASE, RowAction.CANCEL)
        return (RowAction.HOLD, RowAction.CANCEL)
    if item.task_state == DownloadTaskState.COMPLETED:
        return (RowAction.OPEN_FOLDER, RowAction.SHARE)
    return ()


def bulk_actions(items) -> dict[RowAction, bool]:
    """Enabled state of the bulk toolbar buttons for a selection (Send handled separately)."""
    acts = [available_actions(i) for i in items]

    def any_enabled(action: RowAction) -> bool:
        return any(a[action].visible and a[action].enabled for a in acts)

    return {
        RowAction.PAUSE: any_enabled(RowAction.PAUSE), RowAction.RESUME: any_enabled(RowAction.RESUME),
        RowAction.HOLD: any_enabled(RowAction.HOLD), RowAction.RELEASE: any_enabled(RowAction.RELEASE),
        RowAction.CANCEL: any_enabled(RowAction.CANCEL),
    }


def can_change_priority(item: DownloadViewSnapshot) -> bool:
    return is_live(item)


# --- responsive columns ---------------------------------------------------------------

COL_SELECT, COL_NAME, COL_CATEGORY, COL_TYPE, COL_SIZE, COL_PROGRESS, COL_STATUS, COL_SPEED, COL_ETA, COL_ADDED, COL_ACTIONS = range(11)
COLUMN_KEYS = ("select", "name", "category", "type", "size", "progress", "status", "speed", "eta", "added", "actions")


@dataclass(frozen=True)
class ColumnLayout:
    tier: str  # wide | normal | compact | rail
    visible: frozenset[int]
    merge_speed_eta: bool
    sidebar_collapsed: bool


def column_layout(width: int) -> ColumnLayout:
    """Approved responsive rules: >=2200 adds Category and Added; 1280-2199 all eight columns;
    1100-1279 hides Type and merges Speed+Remaining; <1100 also collapses the sidebar to an icon
    rail and moves Size out. Name, Progress and Status never disappear."""
    core = {COL_SELECT, COL_NAME, COL_PROGRESS, COL_STATUS, COL_SPEED, COL_ACTIONS}
    if width >= 2200:
        return ColumnLayout("wide", frozenset(core | {COL_CATEGORY, COL_TYPE, COL_SIZE, COL_ETA, COL_ADDED}), False, False)
    if width >= 1280:
        return ColumnLayout("normal", frozenset(core | {COL_TYPE, COL_SIZE, COL_ETA}), False, False)
    if width >= 1100:
        return ColumnLayout("compact", frozenset(core | {COL_SIZE}), True, False)
    return ColumnLayout("rail", frozenset(core), True, True)


# --- search / sort --------------------------------------------------------------------

SORT_KEYS = ("Queue order", "Newest", "Name", "Size", "Progress", "Status")


def matches_search(item: DownloadViewSnapshot, text: str) -> bool:
    needle = text.strip().casefold()
    if not needle:
        return True
    return needle in (item.display_name or "").casefold() or needle in (item.source_host or "").casefold()


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_added(when: datetime | None, now: datetime | None = None) -> str:
    """Relative, locale-independent English text ("Today 09:12", "Yesterday", "Fri", "05 Mar")."""
    if when is None:
        return "—"
    local = when.astimezone() if when.tzinfo else when
    now = now or datetime.now(local.tzinfo)
    delta_days = (now.date() - local.date()).days
    if delta_days == 0:
        return f"Today {local:%H:%M}"
    if delta_days == 1:
        return "Yesterday"
    if 1 < delta_days < 7:
        return _WEEKDAYS[local.weekday()]
    month = _MONTHS[local.month - 1]
    return f"{local.day:02d} {month}" if local.year == now.year else f"{local.day:02d} {month} {local.year}"
