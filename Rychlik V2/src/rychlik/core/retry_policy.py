"""Pure retry/backoff decision policy (Prompt A6).

Answers exactly one question, deterministically, with no side effects:

    Given how many transfer attempts a task has already made, is another
    attempt allowed, and if so, after how long?

It does NOT decide whether a failure is retryable in the first place --
that remains A4's failure-mapping boundary (`DownloadTaskFailure.retryable`).
It does NOT start a transfer, touch a clock/timer, or know about
DownloadQueue/DownloadTask/threads/HTTP at all. Runtime integration
(monotonic deadlines, the A5 controller loop, DownloadTask.mark_retry_ready())
lives in rychlik.core.concurrent_runtime, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rychlik.core.download_task import DownloadTaskFailure


class RetryPolicyDomainError(Exception):
    """Base type for all RetryPolicy domain errors."""


class InvalidRetryPolicyConfigError(RetryPolicyDomainError):
    """Raised for a structurally invalid RetryPolicyConfig."""


class InvalidRetryStateError(RetryPolicyDomainError):
    """Raised if decide() is asked to evaluate a non-retryable failure --
    A2 already guarantees RETRY_WAIT only follows a retryable failure, so
    this indicates externally-supplied malformed state, not a normal
    outcome. Fails loudly rather than silently inventing a decision."""


class RetryReason(Enum):
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"


@dataclass(frozen=True)
class RetryPolicyConfig:
    """max_attempts counts TOTAL transfer attempts, including the first --
    max_attempts=1 means zero retries, max_attempts=3 means the initial
    attempt plus up to two retries. No jitter, no Retry-After support, no
    HTTP-status-specific policy: deterministic exponential backoff only."""

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise InvalidRetryPolicyConfigError("max_attempts must be >= 1")
        if self.base_delay_seconds < 0:
            raise InvalidRetryPolicyConfigError("base_delay_seconds must not be negative")
        if self.backoff_multiplier < 1:
            raise InvalidRetryPolicyConfigError("backoff_multiplier must be >= 1")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise InvalidRetryPolicyConfigError("max_delay_seconds must be >= base_delay_seconds")


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    delay_seconds: float | None
    reason: RetryReason


class RetryPolicy:
    def __init__(self, config: RetryPolicyConfig | None = None) -> None:
        self._config = config or RetryPolicyConfig()

    def decide(self, *, attempt_count: int, failure: DownloadTaskFailure) -> RetryDecision:
        """`attempt_count` is the attempt that just failed (DownloadTask.
        attempt_count, A2's sole attempt counter -- no second counter is
        introduced here). `failure` must be the task's last_failure; A2
        already guarantees it is retryable whenever the task is in
        RETRY_WAIT, but this is re-validated defensively (§14)."""
        if not isinstance(failure, DownloadTaskFailure) or not failure.retryable:
            raise InvalidRetryStateError(
                "RetryPolicy.decide() requires a retryable DownloadTaskFailure"
            )

        if attempt_count >= self._config.max_attempts:
            return RetryDecision(
                should_retry=False, delay_seconds=None, reason=RetryReason.ATTEMPTS_EXHAUSTED
            )

        delay = min(
            self._config.max_delay_seconds,
            self._config.base_delay_seconds * (self._config.backoff_multiplier ** (attempt_count - 1)),
        )
        return RetryDecision(should_retry=True, delay_seconds=delay, reason=RetryReason.RETRY_SCHEDULED)
