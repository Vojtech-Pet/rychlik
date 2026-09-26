"""Thread-safe download progress/speed/ETA telemetry (Prompt A7).

Converts raw acquisition progress callbacks into stable, immutable
snapshots. Explicitly NOT part of the DownloadTask lifecycle domain (A2)
-- DownloadTask never gains bytes/speed/ETA/worker/Future fields. This
module owns a small dedicated lock, separate from
ConcurrentDownloadRuntime's state lock (A5), so high-frequency per-chunk
callbacks from multiple workers never serialize against scheduling/retry
work. No PySide6 import anywhere here -- fully usable without Qt; a future
GUI adapter translates these immutable snapshots into Qt signals.

Progress identity is (task_id, queue_entry_id, attempt_number) together,
never task_id alone -- this preserves the re-enqueue and retry-generation
identity guarantees already established in A1/A2/A4/A6. `bytes_downloaded`
is attempt-local: it means bytes transferred in the CURRENT acquisition
attempt, not a cumulative resumable total (no safe Range-resume exists
yet; see docs/DOWNLOAD_PROGRESS_RUNTIME.md).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque

Clock = Callable[[], float]


@dataclass(frozen=True)
class TransferAttemptKey:
    task_id: str
    queue_entry_id: str
    attempt_number: int


class ProgressTelemetryIssueKind(Enum):
    NEGATIVE_BYTES = "NEGATIVE_BYTES"
    BYTES_REGRESSION = "BYTES_REGRESSION"
    TOTAL_CHANGED = "TOTAL_CHANGED"
    STALE_ATTEMPT = "STALE_ATTEMPT"


@dataclass(frozen=True)
class ProgressTelemetryIssue:
    """A telemetry-only anomaly. Never causes acquisition to fail (§62) --
    at most the malformed update is ignored and this is recorded."""

    task_id: str
    queue_entry_id: str
    attempt_number: int
    kind: ProgressTelemetryIssueKind
    detail: str


@dataclass(frozen=True)
class TransferProgressSnapshot:
    """Raw telemetry only -- no lifecycle awareness (COMPLETED/FAILED/etc.
    overrides live in the flat DownloadViewSnapshot builder, not here)."""

    task_id: str
    queue_entry_id: str
    attempt_number: int
    bytes_downloaded: int
    total_bytes: int | None
    progress_fraction: float | None
    speed_bps: float | None
    eta_seconds: float | None
    sample_count: int
    has_started: bool


_DEFAULT_SPEED_WINDOW_SECONDS = 5.0
_DEFAULT_MAX_SPEED_SAMPLES = 64
_DEFAULT_STALE_AFTER_SECONDS = 4.0
_MAX_TELEMETRY_ISSUES = 200


@dataclass
class _Sample:
    at: float
    bytes_downloaded: int


class _AttemptRecord:
    __slots__ = ("attempt_number", "task_id", "bytes_downloaded", "total_bytes", "samples", "started")

    def __init__(self, task_id: str, attempt_number: int) -> None:
        self.task_id = task_id
        self.attempt_number = attempt_number
        self.bytes_downloaded = 0
        self.total_bytes: int | None = None
        self.samples: Deque[_Sample] = deque()
        self.started = False


class ProgressRegistry:
    """Owns its own lock -- never acquired nested inside
    ConcurrentDownloadRuntime's state lock, and never held during network
    I/O, file writes, hashing, or GUI callbacks (§18-20)."""

    def __init__(
        self,
        *,
        monotonic: Clock = time.monotonic,
        speed_window_seconds: float = _DEFAULT_SPEED_WINDOW_SECONDS,
        max_speed_samples: int = _DEFAULT_MAX_SPEED_SAMPLES,
        speed_stale_after_seconds: float = _DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self._monotonic = monotonic
        self._speed_window_seconds = speed_window_seconds
        self._max_speed_samples = max_speed_samples
        self._speed_stale_after_seconds = speed_stale_after_seconds

        self._lock = threading.Lock()
        self._records: dict[str, _AttemptRecord] = {}  # keyed by queue_entry_id

        self._issues_lock = threading.Lock()
        self._issues: Deque[ProgressTelemetryIssue] = deque(maxlen=_MAX_TELEMETRY_ISSUES)

    # --- attempt lifecycle -------------------------------------------------

    def begin_attempt(
        self, task_id: str, queue_entry_id: str, attempt_number: int, *, initial_bytes: int = 0
    ) -> "ProgressReporter":
        """Resets telemetry for a fresh attempt (§14): a brand-new record
        replaces whatever was there before, discarding prior sample
        history/byte count entirely -- no carryover across a retry.

        `initial_bytes` is a Prompt A9 extension (§78/§79/§80): when a
        transfer resumes from a validated durable byte offset, the new
        attempt's telemetry starts already reporting that many bytes, but
        speed history starts genuinely fresh -- a single seed sample at
        (now, initial_bytes) means the first real speed estimate can only
        come from bytes received AFTER resume, never from
        `initial_bytes / (tiny elapsed time)`. `initial_bytes=0` (the
        default) is byte-for-byte identical to every pre-A9 caller."""
        with self._lock:
            record = _AttemptRecord(task_id, attempt_number)
            if initial_bytes > 0:
                record.bytes_downloaded = initial_bytes
                record.started = True
                record.samples.append(_Sample(at=self._monotonic(), bytes_downloaded=initial_bytes))
            self._records[queue_entry_id] = record
        return ProgressReporter(self, TransferAttemptKey(task_id, queue_entry_id, attempt_number))

    def report(
        self, task_id: str, queue_entry_id: str, attempt_number: int, bytes_downloaded: int, total_bytes: int | None
    ) -> None:
        now = self._monotonic()
        with self._lock:
            record = self._records.get(queue_entry_id)
            if record is None or record.task_id != task_id or record.attempt_number != attempt_number:
                self._record_issue(
                    task_id, queue_entry_id, attempt_number, ProgressTelemetryIssueKind.STALE_ATTEMPT,
                    "report() for an attempt that is no longer current",
                )
                return  # stale callback (§56): ignored, never mutates the current attempt

            if bytes_downloaded < 0 or (total_bytes is not None and total_bytes < 0):
                self._record_issue(
                    task_id, queue_entry_id, attempt_number, ProgressTelemetryIssueKind.NEGATIVE_BYTES,
                    f"bytes_downloaded={bytes_downloaded} total_bytes={total_bytes}",
                )
                return

            if record.started and bytes_downloaded < record.bytes_downloaded:
                self._record_issue(
                    task_id, queue_entry_id, attempt_number, ProgressTelemetryIssueKind.BYTES_REGRESSION,
                    f"{bytes_downloaded} < previous {record.bytes_downloaded}",
                )
                return  # preserve previous valid progress (§28)

            if (
                record.total_bytes is not None
                and total_bytes is not None
                and total_bytes != record.total_bytes
            ):
                self._record_issue(
                    task_id, queue_entry_id, attempt_number, ProgressTelemetryIssueKind.TOTAL_CHANGED,
                    f"{record.total_bytes} -> {total_bytes}",
                )
                # Non-fatal (§32): keep the newly reported total, still update below.

            record.started = True
            record.bytes_downloaded = bytes_downloaded
            if total_bytes is not None:
                record.total_bytes = total_bytes

            record.samples.append(_Sample(at=now, bytes_downloaded=bytes_downloaded))
            self._prune_samples_locked(record, now)

    def _prune_samples_locked(self, record: _AttemptRecord, now: float) -> None:
        cutoff = now - self._speed_window_seconds
        while record.samples and record.samples[0].at < cutoff:
            record.samples.popleft()
        while len(record.samples) > self._max_speed_samples:
            record.samples.popleft()

    # --- snapshot ------------------------------------------------------------

    def snapshot(self, queue_entry_id: str) -> TransferProgressSnapshot | None:
        now = self._monotonic()
        with self._lock:
            record = self._records.get(queue_entry_id)
            if record is None:
                return None
            bytes_downloaded = record.bytes_downloaded
            total_bytes = record.total_bytes
            has_started = record.started
            sample_count = len(record.samples)
            speed_bps = self._estimate_speed_locked(record, now)
            task_id = record.task_id
            attempt_number = record.attempt_number
            last_sample_at = record.samples[-1].at if record.samples else None

        if last_sample_at is None or now - last_sample_at > self._speed_stale_after_seconds:
            speed_bps = None  # §40: stale rather than falsely positive forever

        progress_fraction = _compute_fraction(bytes_downloaded, total_bytes)
        eta_seconds = _compute_eta(bytes_downloaded, total_bytes, speed_bps)

        return TransferProgressSnapshot(
            task_id=task_id,
            queue_entry_id=queue_entry_id,
            attempt_number=attempt_number,
            bytes_downloaded=bytes_downloaded,
            total_bytes=total_bytes,
            progress_fraction=progress_fraction,
            speed_bps=speed_bps,
            eta_seconds=eta_seconds,
            sample_count=sample_count,
            has_started=has_started,
        )

    def _estimate_speed_locked(self, record: _AttemptRecord, now: float) -> float | None:
        if len(record.samples) < 2:
            return None
        oldest = record.samples[0]
        newest = record.samples[-1]
        dt = newest.at - oldest.at
        if dt <= 0:
            return None
        db = newest.bytes_downloaded - oldest.bytes_downloaded
        if db < 0:
            return None
        return db / dt

    # --- telemetry issues (§63) -----------------------------------------------

    def _record_issue(
        self, task_id: str, queue_entry_id: str, attempt_number: int, kind: ProgressTelemetryIssueKind, detail: str
    ) -> None:
        with self._issues_lock:
            self._issues.append(
                ProgressTelemetryIssue(task_id, queue_entry_id, attempt_number, kind, detail)
            )

    def drain_telemetry_issues(self) -> list[ProgressTelemetryIssue]:
        with self._issues_lock:
            drained = list(self._issues)
            self._issues.clear()
        return drained


class ProgressReporter:
    """Bound to exactly one TransferAttemptKey. Callable with the exact
    `(bytes_downloaded, total_bytes=None) -> None` signature acquisition
    backends already use (Prompt 04.5) -- no new callback API needed. Never
    reads DownloadTask/DownloadQueue/scheduler state per chunk (§55)."""

    def __init__(self, registry: ProgressRegistry, key: TransferAttemptKey) -> None:
        self._registry = registry
        self._key = key

    def __call__(self, bytes_downloaded: int, total_bytes: int | None = None) -> None:
        self._registry.report(
            self._key.task_id, self._key.queue_entry_id, self._key.attempt_number, bytes_downloaded, total_bytes
        )


def _compute_fraction(bytes_downloaded: int, total_bytes: int | None) -> float | None:
    if total_bytes is None or total_bytes <= 0:
        return None
    fraction = bytes_downloaded / total_bytes
    return min(1.0, fraction)  # clamp only the fraction (§33), never rewrite actual bytes


def _compute_eta(bytes_downloaded: int, total_bytes: int | None, speed_bps: float | None) -> float | None:
    if total_bytes is None or speed_bps is None or speed_bps <= 0:
        return None
    if bytes_downloaded >= total_bytes:
        return None
    remaining = max(0, total_bytes - bytes_downloaded)
    return remaining / speed_bps
