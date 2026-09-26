"""Safe partial HTTP resume: durable value objects + the pure resume
decision function (Prompt A9).

The central safety principle (stated by the user driving this project):
a `.part` file existing is never, by itself, proof that it is safe to
resume. Resume requires ALL of:

    an owned partial file (inside the expected destination, not a
    symlink escape, not an arbitrary persisted path)
    + a durable byte checkpoint (never the raw on-disk file size)
    + local prefix integrity (SHA-256 of exactly that checkpointed prefix)
    + a remote validator (strong ETag, else Last-Modified, else none)
    + a valid HTTP Range/206 response consistent with all of the above

If any single piece of evidence is missing or inconsistent, the only safe
answer is FULL_RESTART -- never an optimistic append. This module contains
no HTTP client and no threading; it is pure decision logic plus the local
(on-disk) validation step, which is real I/O but never touches the
network (real Range validation only happens once AcquisitionService
actually attempts the request).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class ValidatorKind(Enum):
    NONE = "NONE"
    STRONG_ETAG = "STRONG_ETAG"
    LAST_MODIFIED = "LAST_MODIFIED"


@dataclass(frozen=True)
class Validator:
    kind: ValidatorKind
    value: str | None = None

    def __post_init__(self) -> None:
        if self.kind != ValidatorKind.NONE and not self.value:
            raise ValueError(f"{self.kind.name} validator requires a non-empty value")
        if self.kind == ValidatorKind.NONE and self.value is not None:
            raise ValueError("NONE validator must not carry a value")

    @property
    def if_range_header(self) -> str | None:
        """Value to send in `If-Range:` -- an HTTP-date for Last-Modified,
        the quoted ETag for a strong ETag, or None if there is nothing safe
        to send (NONE kind, meaning no Range attempt should be made at all)."""
        if self.kind == ValidatorKind.NONE:
            return None
        return self.value


def classify_validator(*, etag: str | None, last_modified: str | None) -> Validator:
    """Prefers a strong ETag, then Last-Modified, else NONE (§30/§31/§33).
    A weak ETag (`W/"..."`) is never treated as strong (§133)."""
    if etag and not etag.strip().upper().startswith("W/"):
        return Validator(ValidatorKind.STRONG_ETAG, etag.strip())
    if last_modified:
        return Validator(ValidatorKind.LAST_MODIFIED, last_modified.strip())
    return Validator(ValidatorKind.NONE)


class PartialTransferConsistencyError(Exception):
    """Raised when persisted partial-transfer metadata cannot be trusted
    structurally: a path outside the expected destination, a symlink
    escape, or an attempt_count_snapshot from an unrelated generation
    (§36/§37/§90/§91). Never silently guessed around."""


@dataclass(frozen=True)
class PartialTransferState:
    """Durable partial-resume checkpoint for exactly one queue occurrence
    (§29/§42). Primary identity is `queue_entry_id`, never `task_id` alone
    -- an old, removed occurrence's partial state must never be reused by
    a newer re-enqueued occurrence of the same task (§91/§92)."""

    queue_entry_id: str
    task_id: str
    attempt_count_snapshot: int
    temp_path: Path
    final_path: Path
    durable_bytes: int
    expected_total_bytes: int | None
    validator_kind: ValidatorKind
    validator_value: str | None
    prefix_sha256: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.durable_bytes < 0:
            raise ValueError("durable_bytes must not be negative")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")

    @property
    def validator(self) -> Validator:
        return Validator(self.validator_kind, self.validator_value)


class ResumeDecisionKind(Enum):
    NO_PARTIAL = "NO_PARTIAL"
    FULL_RESTART = "FULL_RESTART"
    ATTEMPT_RANGE = "ATTEMPT_RANGE"


@dataclass(frozen=True)
class ResumeDecision:
    kind: ResumeDecisionKind
    offset: int = 0
    validator: Validator | None = None
    reason: str = ""


def _is_path_owned(path: Path, *, expected_dir: Path, expected_suffix: str) -> bool:
    """§36/§37: the persisted temp path must resolve to a plain file
    directly inside the expected destination directory, with the expected
    acquisition temp-file suffix -- never a `../` escape, never an
    absolute path outside the destination, never a symlink pointing
    elsewhere."""
    try:
        resolved = path.resolve()
        expected_dir_resolved = expected_dir.resolve()
    except OSError:
        return False
    if resolved.parent != expected_dir_resolved:
        return False
    if not resolved.name.endswith(expected_suffix):
        return False
    if path.is_symlink():
        return False
    return True


def validate_local_partial(
    partial: PartialTransferState, *, expected_dir: Path, expected_suffix: str = ".part"
) -> tuple[bool, str]:
    """Local-only validation (no network): path ownership, then size vs.
    durable_bytes, then prefix SHA-256 (§36/§37/§44-54). Returns
    (is_valid, reason). Truncates the file back to durable_bytes in place
    when it is larger (§52) -- discarding uncheckpointed crash-window
    bytes -- but only after ownership is proven."""
    if not _is_path_owned(partial.temp_path, expected_dir=expected_dir, expected_suffix=expected_suffix):
        raise PartialTransferConsistencyError(
            f"partial temp_path {partial.temp_path!r} is not owned by destination {expected_dir!r}"
        )

    if not partial.temp_path.exists():
        return False, "partial file does not exist"

    actual_size = partial.temp_path.stat().st_size
    if actual_size < partial.durable_bytes:
        return False, f"partial file too short: {actual_size} < durable_bytes {partial.durable_bytes}"

    if actual_size > partial.durable_bytes:
        # §52: discard uncheckpointed crash-window bytes before hashing.
        with partial.temp_path.open("r+b") as handle:
            handle.truncate(partial.durable_bytes)
            handle.flush()
            os.fsync(handle.fileno())

    if partial.durable_bytes == 0:
        actual_hash = hashlib.sha256(b"").hexdigest()
    else:
        actual_hash = _hash_prefix(partial.temp_path, partial.durable_bytes)
    if actual_hash != partial.prefix_sha256:
        return False, "prefix SHA-256 mismatch"

    return True, "valid"


def _hash_prefix(path: Path, length: int) -> str:
    digest = hashlib.sha256()
    remaining = length
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def plan_resume(
    partial: PartialTransferState | None,
    *,
    expected_dir: Path,
    task_attempt_count: int,
) -> ResumeDecision:
    """Pure decision (no network) combining local validation with
    attempt-generation safety (§90): a partial from an unrelated/older
    generation is never trusted. Actual remote validator re-confirmation
    happens only when the HTTP request is made (AcquisitionService's job,
    not this function's)."""
    if partial is None:
        return ResumeDecision(ResumeDecisionKind.NO_PARTIAL, reason="no persisted partial state")

    if partial.attempt_count_snapshot > task_attempt_count:
        return ResumeDecision(
            ResumeDecisionKind.FULL_RESTART,
            reason=(
                f"partial belongs to a newer generation ({partial.attempt_count_snapshot}) "
                f"than the current task ({task_attempt_count})"
            ),
        )

    is_valid, reason = validate_local_partial(partial, expected_dir=expected_dir)
    if not is_valid:
        return ResumeDecision(ResumeDecisionKind.FULL_RESTART, reason=reason)

    if partial.validator_kind == ValidatorKind.NONE:
        return ResumeDecision(
            ResumeDecisionKind.FULL_RESTART, reason="no remote validator was captured for this partial"
        )

    return ResumeDecision(
        ResumeDecisionKind.ATTEMPT_RANGE,
        offset=partial.durable_bytes,
        validator=partial.validator,
        reason="valid local prefix + remote validator available",
    )
