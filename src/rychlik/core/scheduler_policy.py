"""Pure scheduling/dispatch policy layer (Prompt A3).

Composes rychlik.core.download_queue (DownloadQueue) and
rychlik.core.download_task (DownloadTask) to answer exactly one question,
deterministically:

    Which READY queued tasks should receive the currently available
    transfer slots?

It must NOT: start HTTP downloads, mutate DownloadTask, mutate
DownloadQueue, create threads/workers, call AcquisitionService,
pause/resume sockets, retry automatically, persist state, or touch the
GUI. This module imports nothing beyond the two domain modules above plus
stdlib -- no PySide6, requests, httpx, yt_dlp, AcquisitionService,
ThreadPoolExecutor, asyncio, LocalShareOrigin, or ShareLink.

The fundamental dispatch rule:

    QueueEntry.state == QUEUED
    and
    DownloadTask.state == READY
    and
    an available transfer slot exists
    =>
    dispatch candidate

Only DownloadTaskState.TRANSFERRING occupies a transfer slot. VERIFYING,
POST_PROCESSING, and (task-)PAUSED do not -- see docs/SCHEDULER_POLICY.md
for the full rationale, including the deliberately deferred question of
who authorizes resuming a task-PAUSED transfer against capacity.

A3 only *decides*. A later phase (A4) *executes* a DispatchPlan by
transitioning tasks and invoking the acquisition runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from rychlik.core.download_queue import DownloadQueue
from rychlik.core.download_task import DownloadTask, DownloadTaskState


class SchedulerDomainError(Exception):
    """Base type for all SchedulerPolicy domain errors."""


class InvalidSchedulerConfigError(SchedulerDomainError):
    """Raised for a structurally invalid SchedulerConfig (e.g. negative capacity)."""


class SchedulerSnapshotError(SchedulerDomainError):
    """Raised when a non-REMOVED QueueEntry references a task_id absent from
    the supplied task snapshot -- silently skipping it could starve that
    download indefinitely, so this fails loudly instead."""


@dataclass(frozen=True)
class SchedulerConfig:
    """Deliberately minimal: no per-host limits, bandwidth, time windows,
    battery/network-type awareness, priority weights, or CPU/GPU limits.
    max_active_transfers governs transfer-slot concurrency only."""

    max_active_transfers: int

    def __post_init__(self) -> None:
        if self.max_active_transfers < 0:
            raise InvalidSchedulerConfigError("max_active_transfers must not be negative")


@dataclass(frozen=True)
class DispatchCandidate:
    """Just enough identity for a later coordinator -- never a copy of the
    full QueueEntry/DownloadTask/DownloadRequest/Artifact."""

    queue_entry_id: str
    task_id: str


@dataclass(frozen=True)
class DispatchPlan:
    selected: tuple[DispatchCandidate, ...]
    active_transfer_count: int
    available_slots_before_selection: int
    remaining_slots_after_selection: int


class SchedulerPolicy:
    """Stateless. Holds no mutable runtime state between calls -- every
    plan() call is an independent, pure computation over the snapshot it
    is given."""

    def plan(
        self,
        *,
        queue: DownloadQueue,
        tasks: Mapping[str, DownloadTask],
        config: SchedulerConfig,
    ) -> DispatchPlan:
        self._validate_snapshot(queue, tasks)

        active_transfer_count = sum(
            1 for task in tasks.values() if task.state == DownloadTaskState.TRANSFERRING
        )
        available_slots = max(0, config.max_active_transfers - active_transfer_count)

        # Ordering authority is entirely A1's: eligible_entries() is already
        # QUEUED-only, in canonical (priority, then manual/stable position)
        # order. SchedulerPolicy filters by task readiness but never re-sorts.
        candidates = [
            DispatchCandidate(queue_entry_id=entry.queue_entry_id, task_id=entry.task_id)
            for entry in queue.eligible_entries()
            if tasks[entry.task_id].state == DownloadTaskState.READY
        ]

        selected = tuple(candidates[:available_slots])

        return DispatchPlan(
            selected=selected,
            active_transfer_count=active_transfer_count,
            available_slots_before_selection=available_slots,
            remaining_slots_after_selection=available_slots - len(selected),
        )

    @staticmethod
    def _validate_snapshot(queue: DownloadQueue, tasks: Mapping[str, DownloadTask]) -> None:
        # active_entries() = QUEUED + PAUSED (non-REMOVED). REMOVED entries
        # never require a task lookup here (§29/§60) -- a stale historical
        # entry referencing a since-forgotten task must not break planning.
        for entry in queue.active_entries():
            if entry.task_id not in tasks:
                raise SchedulerSnapshotError(
                    f"QueueEntry {entry.queue_entry_id} references unknown task_id {entry.task_id!r}"
                )
