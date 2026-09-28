"""Flat, GUI-safe presentation snapshots (Prompt A7).

Combines facts from three independent domains -- DownloadQueue (A1),
DownloadTask (A2), and ProgressRegistry (A7 raw telemetry) -- into a
single immutable, flat `DownloadViewSnapshot` that a future GUI (or any
other consumer) can read without knowing anything about `QueueEntry`,
`DownloadTask`, `Future`, `Thread`, `AcquisitionService`, or
`ProgressRegistry` internals. This module never mutates anything it is
given; it only reads and composes.

Task-state-driven overrides (this is deliberately separate from
ProgressRegistry's own raw staleness handling, which knows nothing about
DownloadTask lifecycle):

    COMPLETED           -> speed=0.0, eta=0.0 (never a stale positive rate)
    FAILED / CANCELLED  -> speed=0.0, eta=None
    RETRY_WAIT          -> speed=0.0, eta=None (retry backoff is NOT download ETA)
    anything else       -> raw ProgressRegistry values (already stale-aware)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence
from urllib.parse import unquote, urlparse

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_queue import DownloadQueue, QueueEntry, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTask, DownloadTaskState
from rychlik.core.progress import ProgressRegistry, TransferProgressSnapshot

_ZERO_SPEED_STATES = frozenset(
    {DownloadTaskState.COMPLETED, DownloadTaskState.FAILED, DownloadTaskState.CANCELLED, DownloadTaskState.RETRY_WAIT}
)


@dataclass(frozen=True)
class DownloadViewSnapshot:
    """Flat, safe, immutable. No local_path/source_url by default (§45/§86)
    -- only `filename_hint` (never the full URL/query string) is used for
    `display_name`."""

    task_id: str
    queue_entry_id: str
    display_name: str | None

    task_state: DownloadTaskState
    queue_state: QueueEntryState
    priority: QueuePriority
    position: int

    attempt_count: int

    bytes_downloaded: int
    total_bytes: int | None
    progress_fraction: float | None
    speed_bps: float | None
    eta_seconds: float | None

    last_failure_code: str | None = None
    # Final GUI/UX implementation: additive, privacy-preserving presentation fields.
    # `source_host` is the URL host only (never userinfo, path or query); `added_at` is the
    # queue occurrence's enqueue time.
    source_host: str | None = None
    added_at: datetime | None = None
    # Seconds until the pending automatic retry, only while RETRY_WAIT with a live schedule.
    retry_in_seconds: float | None = None


@dataclass(frozen=True)
class DownloadManagerSnapshot:
    items: tuple[DownloadViewSnapshot, ...]
    active_transfer_count: int
    aggregate_speed_bps: float | None


def safe_name_from_url(url: str) -> str | None:
    """Last URL path segment as a display-only name: no query, no fragment, no
    directory parts, no control characters. Never used as a filesystem path."""
    try:
        segment = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    except ValueError:
        return None
    cleaned = "".join(ch for ch in segment.replace("\\", "/").rsplit("/", 1)[-1] if ch.isprintable()).strip()
    if not cleaned or cleaned in (".", ".."):
        return None
    return cleaned[:255]


def source_host_from_url(url: str) -> str | None:
    try:
        return urlparse(url).hostname or None
    except ValueError:
        return None


def build_view_snapshot(
    *,
    queue_entry: QueueEntry,
    task: DownloadTask | None,
    request: DownloadRequest | None,
    progress: TransferProgressSnapshot | None,
) -> DownloadViewSnapshot:
    """Pure composition -- no locking, no I/O, no side effects (§49). The
    caller is responsible for obtaining `queue_entry`/`task`/`progress` as
    a mutually consistent-enough snapshot (see ConcurrentDownloadRuntime's
    manager_snapshot() for the copy-then-compose locking strategy)."""
    display_name = None
    source_host = None
    if request is not None:
        display_name = request.filename_hint or safe_name_from_url(request.url)
        source_host = source_host_from_url(request.url)

    if task is None:
        # Structurally shouldn't happen for a live QueueEntry (A3's own
        # snapshot invariant already requires this), but a REMOVED historical
        # entry's task may genuinely be absent from the caller's map.
        return DownloadViewSnapshot(
            task_id=queue_entry.task_id,
            queue_entry_id=queue_entry.queue_entry_id,
            display_name=display_name,
            task_state=DownloadTaskState.CREATED,  # unknown; least-alarming placeholder
            queue_state=queue_entry.state,
            priority=queue_entry.priority,
            position=queue_entry.position,
            attempt_count=0,
            bytes_downloaded=0,
            total_bytes=None,
            progress_fraction=None,
            speed_bps=None,
            eta_seconds=None,
            source_host=source_host,
            added_at=queue_entry.enqueued_at,
        )

    # Consistency check (§73/§90): progress telemetry must belong to the
    # exact same attempt generation as the task we're building a view for.
    # A mismatch (e.g. a snapshot taken mid-transition) is treated as "no
    # progress available yet" rather than lying with stale numbers.
    if progress is not None and progress.attempt_number != task.attempt_count:
        progress = None

    if progress is not None:
        bytes_downloaded = progress.bytes_downloaded
        total_bytes = progress.total_bytes
        progress_fraction = progress.progress_fraction
        speed_bps = progress.speed_bps
        eta_seconds = progress.eta_seconds
    else:
        bytes_downloaded = 0
        total_bytes = None
        progress_fraction = None
        speed_bps = None
        eta_seconds = None

    if task.state in _ZERO_SPEED_STATES:
        speed_bps = 0.0
        eta_seconds = 0.0 if task.state == DownloadTaskState.COMPLETED else None
        if task.state == DownloadTaskState.COMPLETED and total_bytes is not None:
            progress_fraction = 1.0

    return DownloadViewSnapshot(
        task_id=task.task_id,
        queue_entry_id=queue_entry.queue_entry_id,
        display_name=display_name,
        task_state=task.state,
        queue_state=queue_entry.state,
        priority=queue_entry.priority,
        position=queue_entry.position,
        attempt_count=task.attempt_count,
        bytes_downloaded=bytes_downloaded,
        total_bytes=total_bytes,
        progress_fraction=progress_fraction,
        speed_bps=speed_bps,
        eta_seconds=eta_seconds,
        last_failure_code=task.last_failure.code if task.last_failure is not None else None,
        source_host=source_host,
        added_at=queue_entry.enqueued_at,
    )


def build_manager_snapshot(
    *,
    entries: Sequence[QueueEntry],
    tasks: Mapping[str, DownloadTask],
    requests: Mapping[str, DownloadRequest],
    progress_registry: ProgressRegistry | None,
) -> DownloadManagerSnapshot:
    """`entries` must already be in the desired order (canonical A1 order
    for active entries, by convention) -- this function never reorders."""
    items = []
    for entry in entries:
        task = tasks.get(entry.task_id)
        progress = progress_registry.snapshot(entry.queue_entry_id) if progress_registry is not None else None
        items.append(
            build_view_snapshot(
                queue_entry=entry, task=task, request=requests.get(entry.task_id), progress=progress
            )
        )

    active_transfer_count = sum(1 for item in items if item.task_state == DownloadTaskState.TRANSFERRING)
    speeds = [item.speed_bps for item in items if item.task_state == DownloadTaskState.TRANSFERRING]
    known_speeds = [s for s in speeds if s is not None]
    aggregate_speed_bps = sum(known_speeds) if known_speeds else None

    return DownloadManagerSnapshot(
        items=tuple(items), active_transfer_count=active_transfer_count, aggregate_speed_bps=aggregate_speed_bps
    )
