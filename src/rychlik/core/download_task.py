"""Pure domain model for the lifecycle of one download task (Prompt A2).

This module answers exactly one question, deterministically:

    What phase is this download task in? Which transitions are legal?
    Is it terminal? Is a real transfer active? How many attempts began?

It does NOT answer:

    Which queued task starts next? How many may run concurrently? Which
    worker owns it? When should a retry actually happen? How much
    bandwidth is available?

Those belong to a future scheduler/dispatch phase (A3+).

Hard architectural boundary (see docs/DOWNLOAD_TASK_LIFECYCLE.md): this is
a SECOND, independent state machine from rychlik.core.download_queue's
QueueEntryState. QUEUED is never a DownloadTaskState. Most importantly:

    QueueEntryState.PAUSED   = held in the queue, not eligible for a
                               future scheduler; no transfer needs to exist.
    DownloadTaskState.PAUSED = a real transfer had already started
                               (TRANSFERRING) and has now been paused.

`DownloadTask READY` + `QueueEntry PAUSED` is a perfectly valid, common
combination. Nothing in this module imports or mutates DownloadQueue, and
nothing here performs networking, threading, or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum


class DownloadTaskState(Enum):
    CREATED = "CREATED"
    RESOLVING = "RESOLVING"
    READY = "READY"
    TRANSFERRING = "TRANSFERRING"
    PAUSED = "PAUSED"
    RETRY_WAIT = "RETRY_WAIT"
    VERIFYING = "VERIFYING"
    POST_PROCESSING = "POST_PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = frozenset(
    {DownloadTaskState.COMPLETED, DownloadTaskState.FAILED, DownloadTaskState.CANCELLED}
)

_NONTERMINAL_STATES = frozenset(DownloadTaskState) - TERMINAL_STATES

_MARK_READY_SOURCES = frozenset(
    {DownloadTaskState.CREATED, DownloadTaskState.RESOLVING, DownloadTaskState.RETRY_WAIT}
)
_RETRY_WAIT_SOURCES = frozenset(
    {DownloadTaskState.RESOLVING, DownloadTaskState.TRANSFERRING, DownloadTaskState.VERIFYING}
)
_POST_PROCESSING_SOURCES = frozenset({DownloadTaskState.TRANSFERRING, DownloadTaskState.VERIFYING})
_COMPLETE_SOURCES = frozenset(
    {DownloadTaskState.TRANSFERRING, DownloadTaskState.VERIFYING, DownloadTaskState.POST_PROCESSING}
)


class DownloadTaskDomainError(Exception):
    """Base type for all DownloadTask domain errors."""


class InvalidTaskTransitionError(DownloadTaskDomainError):
    """Raised for a lifecycle transition not permitted from the current state."""


class InvalidRetryFailureError(DownloadTaskDomainError):
    """Raised when wait_for_retry() is called with a non-retryable failure."""


class InvalidTaskOperationError(DownloadTaskDomainError):
    """Raised for structurally invalid operations not covered by a more
    specific error (e.g. a non-DownloadTaskFailure passed to fail())."""


@dataclass(frozen=True)
class DownloadTaskFailure:
    """Pure value object. Never holds raw Exception/traceback/Response
    objects — infrastructure failures are mapped into this shape by the
    (future) execution layer, keeping the lifecycle serializable and
    backend-independent (§10)."""

    code: str
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("code must not be empty")


@dataclass(frozen=True)
class DownloadTask:
    task_id: str
    state: DownloadTaskState
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempt_count: int = 0
    last_failure: DownloadTaskFailure | None = None

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
        for name in ("created_at", "updated_at", "started_at", "finished_at"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.state == DownloadTaskState.FAILED and self.last_failure is None:
            raise ValueError("a FAILED task must have last_failure set (§11)")

    # --- pure query helpers (§40) --------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_active_transfer(self) -> bool:
        return self.state == DownloadTaskState.TRANSFERRING

    @property
    def is_successful(self) -> bool:
        return self.state == DownloadTaskState.COMPLETED

    @property
    def can_retry(self) -> bool:
        return self.state == DownloadTaskState.RETRY_WAIT

    # --- lifecycle operations (§25) -------------------------------------
    # Every mutation returns a NEW DownloadTask (frozen dataclass); nothing
    # ever assigns to `.state`/`.attempt_count`/etc. from outside this
    # class. `now` is always explicit — no hidden global clock, matching
    # ShareLink's established convention (Prompt 06).

    def start_resolving(self, *, now: datetime) -> "DownloadTask":
        if self.state == DownloadTaskState.RESOLVING:
            return self  # idempotent no-op (§27)
        self._require_source(DownloadTaskState.CREATED, target=DownloadTaskState.RESOLVING)
        return replace(self, state=DownloadTaskState.RESOLVING, updated_at=now)

    def mark_ready(self, *, now: datetime) -> "DownloadTask":
        """General entry point: CREATED, RESOLVING, or RETRY_WAIT -> READY."""
        if self.state == DownloadTaskState.READY:
            return self  # idempotent no-op
        self._require_source(_MARK_READY_SOURCES, target=DownloadTaskState.READY)
        return replace(self, state=DownloadTaskState.READY, updated_at=now)

    def mark_retry_ready(self, *, now: datetime) -> "DownloadTask":
        """Specific entry point restricted to RETRY_WAIT -> READY (§33), for
        callers (a future retry-policy runtime) that want to fail loudly if
        the task isn't actually waiting for retry, rather than silently
        succeeding the way the broader mark_ready() would."""
        if self.state == DownloadTaskState.READY:
            return self  # idempotent no-op
        self._require_source(DownloadTaskState.RETRY_WAIT, target=DownloadTaskState.READY)
        return replace(self, state=DownloadTaskState.READY, updated_at=now)

    def start_transfer(self, *, now: datetime) -> "DownloadTask":
        if self.state == DownloadTaskState.TRANSFERRING:
            return self  # idempotent no-op: no double attempt_count increment
        self._require_source(DownloadTaskState.READY, target=DownloadTaskState.TRANSFERRING)
        return replace(
            self,
            state=DownloadTaskState.TRANSFERRING,
            updated_at=now,
            started_at=self.started_at or now,  # first-ever start only (§21)
            attempt_count=self.attempt_count + 1,
        )

    def pause_transfer(self, *, now: datetime) -> "DownloadTask":
        """Only from TRANSFERRING (§30) -- this is NOT the queue's pause.
        A READY task cannot be task-paused; hold it via QueueEntry instead."""
        if self.state == DownloadTaskState.PAUSED:
            return self  # idempotent no-op
        self._require_source(DownloadTaskState.TRANSFERRING, target=DownloadTaskState.PAUSED)
        return replace(self, state=DownloadTaskState.PAUSED, updated_at=now)

    def resume_transfer(self, *, now: datetime) -> "DownloadTask":
        """Continues the same attempt: attempt_count and started_at unchanged (§31)."""
        if self.state == DownloadTaskState.TRANSFERRING:
            return self  # idempotent no-op
        self._require_source(DownloadTaskState.PAUSED, target=DownloadTaskState.TRANSFERRING)
        return replace(self, state=DownloadTaskState.TRANSFERRING, updated_at=now)

    def wait_for_retry(self, failure: DownloadTaskFailure, *, now: datetime) -> "DownloadTask":
        if not isinstance(failure, DownloadTaskFailure):
            raise InvalidTaskOperationError("failure must be a DownloadTaskFailure")
        if not failure.retryable:
            raise InvalidRetryFailureError("cannot enter RETRY_WAIT with a non-retryable failure")
        self._require_source(_RETRY_WAIT_SOURCES, target=DownloadTaskState.RETRY_WAIT)
        # No attempt_count change here (§32): resolver/verification/transfer
        # retries are accounted for at the next start_transfer() call, not here.
        return replace(self, state=DownloadTaskState.RETRY_WAIT, updated_at=now, last_failure=failure)

    def start_verification(self, *, now: datetime) -> "DownloadTask":
        if self.state == DownloadTaskState.VERIFYING:
            return self  # idempotent no-op
        self._require_source(DownloadTaskState.TRANSFERRING, target=DownloadTaskState.VERIFYING)
        return replace(self, state=DownloadTaskState.VERIFYING, updated_at=now)

    def start_post_processing(self, *, now: datetime) -> "DownloadTask":
        if self.state == DownloadTaskState.POST_PROCESSING:
            return self  # idempotent no-op
        self._require_source(_POST_PROCESSING_SOURCES, target=DownloadTaskState.POST_PROCESSING)
        return replace(self, state=DownloadTaskState.POST_PROCESSING, updated_at=now)

    def complete(self, *, now: datetime) -> "DownloadTask":
        if self.state == DownloadTaskState.COMPLETED:
            return self  # idempotent no-op
        self._require_source(_COMPLETE_SOURCES, target=DownloadTaskState.COMPLETED)
        return replace(self, state=DownloadTaskState.COMPLETED, updated_at=now, finished_at=now)

    def fail(self, failure: DownloadTaskFailure, *, now: datetime) -> "DownloadTask":
        if not isinstance(failure, DownloadTaskFailure):
            raise InvalidTaskOperationError("failure must be a DownloadTaskFailure")
        if self.state == DownloadTaskState.FAILED:
            return self  # idempotent no-op: keeps the originally recorded failure/finished_at
        self._require_source(_NONTERMINAL_STATES, target=DownloadTaskState.FAILED)
        return replace(
            self, state=DownloadTaskState.FAILED, updated_at=now, finished_at=now, last_failure=failure
        )

    def cancel(self, *, now: datetime) -> "DownloadTask":
        if self.state == DownloadTaskState.CANCELLED:
            return self  # idempotent no-op
        self._require_source(_NONTERMINAL_STATES, target=DownloadTaskState.CANCELLED)
        return replace(self, state=DownloadTaskState.CANCELLED, updated_at=now, finished_at=now)

    # --- internals -----------------------------------------------------

    def _require_source(
        self, allowed: DownloadTaskState | frozenset[DownloadTaskState], *, target: DownloadTaskState
    ) -> None:
        allowed_set = allowed if isinstance(allowed, frozenset) else frozenset({allowed})
        if self.state not in allowed_set:
            raise InvalidTaskTransitionError(
                f"cannot transition DownloadTask from {self.state.name} to {target.name}"
            )


def create_task(task_id: str, *, now: datetime) -> DownloadTask:
    """Factory: a brand-new task always starts CREATED (§19/§55)."""
    return DownloadTask(
        task_id=task_id,
        state=DownloadTaskState.CREATED,
        created_at=now,
        updated_at=now,
    )
