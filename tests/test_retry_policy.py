import pytest

from rychlik.core.download_task import DownloadTaskFailure
from rychlik.core.retry_policy import (
    InvalidRetryPolicyConfigError,
    InvalidRetryStateError,
    RetryPolicy,
    RetryPolicyConfig,
    RetryReason,
)

RETRYABLE = DownloadTaskFailure(code="NET", message="x", retryable=True)
NON_RETRYABLE = DownloadTaskFailure(code="HTTP", message="x", retryable=False)


def _policy(**overrides):
    defaults = dict(max_attempts=3, base_delay_seconds=2.0, backoff_multiplier=2.0, max_delay_seconds=30.0)
    defaults.update(overrides)
    return RetryPolicy(RetryPolicyConfig(**defaults))


# --- first/second retry (§59/§60) -----------------------------------------


def test_first_retry_delay():
    decision = _policy().decide(attempt_count=1, failure=RETRYABLE)
    assert decision.should_retry is True
    assert decision.delay_seconds == 2.0
    assert decision.reason == RetryReason.RETRY_SCHEDULED


def test_second_retry_delay():
    decision = _policy().decide(attempt_count=2, failure=RETRYABLE)
    assert decision.delay_seconds == 4.0


def test_third_attempt_within_budget_still_retries():
    # max_attempts=3: attempt_count=2 just failed (the 2nd attempt), a 3rd
    # is still within budget.
    decision = _policy(max_attempts=4).decide(attempt_count=3, failure=RETRYABLE)
    assert decision.should_retry is True
    assert decision.delay_seconds == 8.0


# --- delay cap (§61) ---------------------------------------------------------


def test_delay_cap():
    policy = _policy(base_delay_seconds=10.0, backoff_multiplier=3.0, max_delay_seconds=30.0, max_attempts=10)
    assert policy.decide(attempt_count=1, failure=RETRYABLE).delay_seconds == 10.0
    assert policy.decide(attempt_count=2, failure=RETRYABLE).delay_seconds == 30.0  # 30 uncapped, ==cap
    assert policy.decide(attempt_count=3, failure=RETRYABLE).delay_seconds == 30.0  # 90 capped to 30


# --- exhausted (§62) ----------------------------------------------------------


def test_exhausted_at_max_attempts():
    decision = _policy(max_attempts=3).decide(attempt_count=3, failure=RETRYABLE)
    assert decision.should_retry is False
    assert decision.delay_seconds is None
    assert decision.reason == RetryReason.ATTEMPTS_EXHAUSTED


def test_max_attempts_one_means_zero_retries():
    decision = _policy(max_attempts=1).decide(attempt_count=1, failure=RETRYABLE)
    assert decision.should_retry is False
    assert decision.reason == RetryReason.ATTEMPTS_EXHAUSTED


# --- config validation (§63) --------------------------------------------------


def test_max_attempts_below_one_rejected():
    with pytest.raises(InvalidRetryPolicyConfigError):
        RetryPolicyConfig(max_attempts=0)


def test_negative_base_delay_rejected():
    with pytest.raises(InvalidRetryPolicyConfigError):
        RetryPolicyConfig(base_delay_seconds=-1)


def test_multiplier_below_one_rejected():
    with pytest.raises(InvalidRetryPolicyConfigError):
        RetryPolicyConfig(backoff_multiplier=0.5)


def test_max_delay_below_base_delay_rejected():
    with pytest.raises(InvalidRetryPolicyConfigError):
        RetryPolicyConfig(base_delay_seconds=10, max_delay_seconds=5)


def test_default_config_is_valid():
    RetryPolicyConfig()  # must not raise


# --- malformed state / non-retryable failure (§14) ----------------------------


def test_decide_rejects_non_retryable_failure():
    with pytest.raises(InvalidRetryStateError):
        _policy().decide(attempt_count=1, failure=NON_RETRYABLE)


def test_decide_rejects_non_failure_object():
    with pytest.raises(InvalidRetryStateError):
        _policy().decide(attempt_count=1, failure="not a failure")  # type: ignore[arg-type]


# --- zero base delay is allowed (§40) -----------------------------------------


def test_zero_base_delay_allowed():
    decision = _policy(base_delay_seconds=0.0).decide(attempt_count=1, failure=RETRYABLE)
    assert decision.should_retry is True
    assert decision.delay_seconds == 0.0


# --- structural import test ---------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.retry_policy as module

    forbidden = {"threading", "PySide6", "requests", "httpx", "yt_dlp"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    top_level = {name.split(".")[0] for name in imported_modules}
    assert top_level & forbidden == set()
