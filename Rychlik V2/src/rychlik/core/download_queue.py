"""Pure domain model for the Rýchlik download queue (Prompt A1).

This module answers exactly one question, deterministically:

    Which download tasks are in the queue, in what order, with what
    priority, and which of them are currently eligible?

It does NOT answer:

    Which one should start now? How many may run? How is it downloaded?
    What happens when it fails?

Those belong to a future scheduler / Task-Download-lifecycle phase (A2+).

No scheduler, no dispatch/claim semantics, no parallelism, no actual HTTP
transfer pause/resume, no retry, no persistence, no threads/locks, no GUI.
`task_id` is treated as a fully opaque reference — this module never
resolves it against Artifact/AcquisitionService/anything network-related.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QueueEntryState(Enum):
    """Queue-membership state only — never a download-transfer state.

    QUEUED  = eligible for future scheduler consideration
    PAUSED  = still in the queue, held, not eligible
    REMOVED = terminal; no longer participates in queue operation
    """

    QUEUED = "QUEUED"
    PAUSED = "PAUSED"
    REMOVED = "REMOVED"


class QueuePriority(Enum):
    """Bounded named priority. Numeric values are internal ranking only —
    callers must use the named values, never arbitrary integers."""

    LOW = 100
    NORMAL = 200
    HIGH = 300


_ALLOWED_TRANSITIONS: dict[QueueEntryState, frozenset[QueueEntryState]] = {
    QueueEntryState.QUEUED: frozenset({QueueEntryState.PAUSED, QueueEntryState.REMOVED}),
    QueueEntryState.PAUSED: frozenset({QueueEntryState.QUEUED, QueueEntryState.REMOVED}),
    QueueEntryState.REMOVED: frozenset(),
}


class QueueDomainError(Exception):
    """Base type for all Queue domain errors."""


class UnknownQueueEntryError(QueueDomainError):
    """Raised when a queue_entry_id does not refer to any known entry."""


class DuplicateQueuedTaskError(QueueDomainError):
    """Raised when a task_id already has a live (non-REMOVED) QueueEntry."""


class InvalidQueueTransitionError(QueueDomainError):
    """Raised for a state transition not in _ALLOWED_TRANSITIONS (and never
    for a same-state call, which is idempotent, not an error)."""


class CrossPriorityReorderError(QueueDomainError):
    """Raised when move_before/move_after is attempted across priority bands."""


class InvalidQueueOperationError(QueueDomainError):
    """Raised for structurally invalid operations not covered by a more
    specific error (e.g. reordering a REMOVED entry, moving an entry
    relative to itself, an unrecognized priority value)."""


def generate_queue_entry_id() -> str:
    """queue_entry_id is intentionally distinct from task_id (§5): the same
    task may leave the queue and later be re-enqueued as a new occurrence."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class QueueEntry:
    queue_entry_id: str
    task_id: str
    state: QueueEntryState
    priority: QueuePriority
    position: int
    enqueued_at: datetime
    updated_at: datetime
    paused_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.queue_entry_id:
            raise ValueError("queue_entry_id must not be empty")
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if self.position < 0:
            raise ValueError("position must not be negative")
        if self.enqueued_at.tzinfo is None:
            raise ValueError("enqueued_at must be timezone-aware")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.paused_at is not None and self.paused_at.tzinfo is None:
            raise ValueError("paused_at must be timezone-aware")


def _canonical_key(entry: QueueEntry) -> tuple:
    # priority rank descending, position ascending, enqueued_at ascending,
    # queue_entry_id as a final deterministic tie-break (§14). Positions are
    # kept contiguous/unique within a band by the aggregate, so the last two
    # keys are a safety net against malformed/imported state, not the
    # primary ordering mechanism.
    return (-entry.priority.value, entry.position, entry.enqueued_at, entry.queue_entry_id)


class DownloadQueue:
    """Aggregate root. All mutation goes through this class — QueueEntry
    fields must never be assigned to directly from outside this module."""

    def __init__(self, *, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._entries: dict[str, QueueEntry] = {}  # every entry ever created, including REMOVED
        self._bands: dict[QueuePriority, list[str]] = {p: [] for p in QueuePriority}

    @classmethod
    def restore(cls, entries: "list[QueueEntry] | tuple[QueueEntry, ...]", *, clock: Clock = _utc_now) -> "DownloadQueue":
        """Rehydrate a queue from previously-persisted entries (Prompt A8).

        This is NOT a sequence of enqueue()/pause()/... calls -- it directly
        reconstructs the aggregate's internal bands from already-valid
        QueueEntry facts, preserving exact queue_entry_id/task_id/priority/
        position/state. It re-validates the same invariants enqueue() itself
        guards (§14/§66): each band's positions must be a contiguous 0..n-1
        permutation, and no task_id may have more than one live (non-REMOVED)
        entry. A malformed persisted database must fail loudly here rather
        than silently pick one of two conflicting live entries."""
        queue = cls(clock=clock)
        live_task_ids: dict[str, str] = {}  # task_id -> queue_entry_id, live entries only
        for entry in entries:
            queue._entries[entry.queue_entry_id] = entry
            if entry.state != QueueEntryState.REMOVED:
                existing = live_task_ids.get(entry.task_id)
                if existing is not None:
                    raise DuplicateQueuedTaskError(
                        f"task_id {entry.task_id!r} has more than one live queue entry "
                        f"on restore: {existing!r} and {entry.queue_entry_id!r}"
                    )
                live_task_ids[entry.task_id] = entry.queue_entry_id

        for priority in QueuePriority:
            band_entries = sorted(
                (e for e in entries if e.priority == priority and e.state != QueueEntryState.REMOVED),
                key=lambda e: e.position,
            )
            expected_positions = list(range(len(band_entries)))
            actual_positions = [e.position for e in band_entries]
            if actual_positions != expected_positions:
                raise InvalidQueueOperationError(
                    f"corrupt persisted queue: {priority.name} band positions {actual_positions!r} "
                    f"are not a contiguous 0..{len(band_entries) - 1} permutation"
                )
            queue._bands[priority] = [e.queue_entry_id for e in band_entries]
        return queue

    # --- read-only projections ---------------------------------------

    def get(self, entry_id: str) -> QueueEntry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise UnknownQueueEntryError(entry_id)
        return entry

    def all_entries(self) -> tuple[QueueEntry, ...]:
        """Every entry ever created, including REMOVED. For history/audit
        only — never used for scheduling or ordering decisions."""
        return tuple(self._entries.values())

    def active_entries(self) -> tuple[QueueEntry, ...]:
        """QUEUED + PAUSED, canonical order. What a future queue UI may show."""
        entries = [e for e in self._entries.values() if e.state != QueueEntryState.REMOVED]
        return tuple(sorted(entries, key=_canonical_key))

    def eligible_entries(self) -> tuple[QueueEntry, ...]:
        """QUEUED only, canonical order. Read-only projection for a future
        scheduler — this method does not dispatch anything."""
        entries = [e for e in self._entries.values() if e.state == QueueEntryState.QUEUED]
        return tuple(sorted(entries, key=_canonical_key))

    # --- mutations ------------------------------------------------------

    def enqueue(
        self,
        task_id: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        *,
        now: datetime | None = None,
    ) -> QueueEntry:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        _require_priority(priority)
        if self._has_live_entry(task_id):
            raise DuplicateQueuedTaskError(task_id)

        moment = now or self._clock()
        band = self._bands[priority]
        entry = QueueEntry(
            queue_entry_id=generate_queue_entry_id(),
            task_id=task_id,
            state=QueueEntryState.QUEUED,
            priority=priority,
            position=len(band),
            enqueued_at=moment,
            updated_at=moment,
        )
        self._entries[entry.queue_entry_id] = entry
        band.append(entry.queue_entry_id)
        return entry

    def pause(self, entry_id: str, *, now: datetime | None = None) -> QueueEntry:
        return self._transition(entry_id, QueueEntryState.PAUSED, now=now, mark_paused=True)

    def resume(self, entry_id: str, *, now: datetime | None = None) -> QueueEntry:
        return self._transition(entry_id, QueueEntryState.QUEUED, now=now, clear_paused=True)

    def remove(self, entry_id: str, *, now: datetime | None = None) -> QueueEntry:
        return self._transition(entry_id, QueueEntryState.REMOVED, now=now, unband=True)

    def set_priority(
        self, entry_id: str, new_priority: QueuePriority, *, now: datetime | None = None
    ) -> QueueEntry:
        _require_priority(new_priority)
        entry = self.get(entry_id)
        if entry.state == QueueEntryState.REMOVED:
            raise InvalidQueueOperationError("cannot change priority of a REMOVED entry")
        if entry.priority == new_priority:
            return entry  # idempotent no-op: same priority never moves the entry (§24)

        moment = now or self._clock()
        self._bands[entry.priority].remove(entry.queue_entry_id)
        self._renumber(entry.priority)

        new_band = self._bands[new_priority]
        updated = replace(entry, priority=new_priority, position=len(new_band), updated_at=moment)
        self._entries[entry_id] = updated
        new_band.append(entry.queue_entry_id)
        return updated

    def move_before(
        self, entry_id: str, target_entry_id: str, *, now: datetime | None = None
    ) -> QueueEntry:
        return self._move(entry_id, target_entry_id, before=True, now=now)

    def move_after(
        self, entry_id: str, target_entry_id: str, *, now: datetime | None = None
    ) -> QueueEntry:
        return self._move(entry_id, target_entry_id, before=False, now=now)

    # --- internals --------------------------------------------------------

    def _has_live_entry(self, task_id: str) -> bool:
        return any(
            e.task_id == task_id and e.state != QueueEntryState.REMOVED
            for e in self._entries.values()
        )

    def _transition(
        self,
        entry_id: str,
        new_state: QueueEntryState,
        *,
        now: datetime | None,
        mark_paused: bool = False,
        clear_paused: bool = False,
        unband: bool = False,
    ) -> QueueEntry:
        entry = self.get(entry_id)

        if entry.state == new_state:
            return entry  # idempotent no-op: no mutation, updated_at unchanged (§9/§31)

        if new_state not in _ALLOWED_TRANSITIONS[entry.state]:
            raise InvalidQueueTransitionError(
                f"cannot transition QueueEntry from {entry.state.name} to {new_state.name}"
            )

        moment = now or self._clock()
        paused_at = entry.paused_at
        if mark_paused:
            paused_at = moment
        elif clear_paused:
            paused_at = None

        updated = replace(entry, state=new_state, updated_at=moment, paused_at=paused_at)
        self._entries[entry_id] = updated

        if unband:
            self._bands[entry.priority].remove(entry_id)
            self._renumber(entry.priority)

        return updated

    def _move(
        self, entry_id: str, target_entry_id: str, *, before: bool, now: datetime | None
    ) -> QueueEntry:
        entry = self.get(entry_id)
        target = self.get(target_entry_id)

        if entry.queue_entry_id == target.queue_entry_id:
            raise InvalidQueueOperationError("cannot move an entry relative to itself")
        if entry.state == QueueEntryState.REMOVED or target.state == QueueEntryState.REMOVED:
            raise InvalidQueueOperationError("cannot reorder a REMOVED entry")
        if entry.priority != target.priority:
            raise CrossPriorityReorderError(
                f"cannot reorder across priority bands: {entry.priority.name} vs {target.priority.name}"
            )

        moment = now or self._clock()
        band = self._bands[entry.priority]
        band.remove(entry.queue_entry_id)
        target_index = band.index(target.queue_entry_id)
        band.insert(target_index if before else target_index + 1, entry.queue_entry_id)
        self._renumber(entry.priority)

        current = self._entries[entry.queue_entry_id]
        updated = replace(current, updated_at=moment)
        self._entries[entry.queue_entry_id] = updated
        return updated

    def _renumber(self, priority: QueuePriority) -> None:
        # Reassigns position 0..n-1 within a band. Siblings shifted purely
        # as a side effect of another entry's removal/move do NOT get their
        # updated_at bumped -- only the entry an operation directly targets
        # does (see callers).
        for index, entry_id in enumerate(self._bands[priority]):
            entry = self._entries[entry_id]
            if entry.position != index:
                self._entries[entry_id] = replace(entry, position=index)


def _require_priority(priority: QueuePriority) -> None:
    if not isinstance(priority, QueuePriority):
        raise InvalidQueueOperationError(f"priority must be a QueuePriority, got {priority!r}")
