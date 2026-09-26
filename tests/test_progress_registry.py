import threading

from rychlik.core.progress import (
    ProgressRegistry,
    ProgressTelemetryIssueKind,
)


class FakeClock:
    def __init__(self, start=0.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _registry(**overrides):
    clock = FakeClock()
    defaults = dict(monotonic=clock, speed_window_seconds=5.0, max_speed_samples=64, speed_stale_after_seconds=4.0)
    defaults.update(overrides)
    return ProgressRegistry(**defaults), clock


# --- attempt start / progress update (§107 groups) --------------------------


def test_begin_attempt_initial_snapshot():
    registry, clock = _registry()
    registry.begin_attempt("t1", "q1", 1)
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 0
    assert snap.total_bytes is None
    assert snap.speed_bps is None
    assert snap.eta_seconds is None
    assert snap.has_started is False
    assert snap.attempt_number == 1


def test_report_updates_snapshot():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(1000, 5000)
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 1000
    assert snap.total_bytes == 5000
    assert snap.has_started is True


def test_no_snapshot_for_unknown_queue_entry():
    registry, clock = _registry()
    assert registry.snapshot("does-not-exist") is None


# --- unknown/known total, fraction (§30-34) ----------------------------------


def test_unknown_total_gives_none_fraction_and_eta():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    clock.advance(1.0)
    reporter(1_000_000, None)
    snap = registry.snapshot("q1")
    assert snap.total_bytes is None
    assert snap.progress_fraction is None
    assert snap.eta_seconds is None


def test_known_total_fraction():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(50, 100)
    snap = registry.snapshot("q1")
    assert snap.progress_fraction == 0.5


def test_zero_total_bytes_no_division_error():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(0, 0)
    snap = registry.snapshot("q1")
    assert snap.progress_fraction is None


def test_over_total_clamps_fraction_but_preserves_bytes():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(101, 100)
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 101
    assert snap.progress_fraction == 1.0


def test_total_becomes_known_later():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(10, None)
    reporter(20, 100)
    snap = registry.snapshot("q1")
    assert snap.total_bytes == 100


def test_total_change_recorded_as_issue_but_keeps_download_alive():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(10, 100)
    reporter(20, 200)  # different known total
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 20  # still updates, not rejected
    issues = registry.drain_telemetry_issues()
    assert any(i.kind == ProgressTelemetryIssueKind.TOTAL_CHANGED for i in issues)


# --- speed / window / ETA (§74-79) -------------------------------------------


def test_pure_speed_calculation():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(0, None)
    clock.advance(1.0)
    reporter(1_000_000, None)
    snap = registry.snapshot("q1")
    assert snap.speed_bps == 1_000_000.0


def test_speed_window_uses_only_recent_samples():
    registry, clock = _registry(speed_window_seconds=5.0)
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(0, None)  # t=0
    clock.advance(4.0)
    reporter(4_000_000, None)  # t=4
    clock.advance(4.0)
    reporter(12_000_000, None)  # t=8 -- t=0 sample now outside the 5s window (cutoff=3)
    snap = registry.snapshot("q1")
    # remaining samples: t=4 (4MB), t=8 (12MB) -> (12-4)MB / 4s = 2MB/s
    assert snap.speed_bps == 2_000_000.0


def test_bounded_sample_history():
    registry, clock = _registry(speed_window_seconds=10_000.0, max_speed_samples=10)
    reporter = registry.begin_attempt("t1", "q1", 1)
    for i in range(1000):
        clock.advance(0.001)
        reporter(i, None)
    # internal sample count is bounded; verify via a fresh registry API surface
    # (sample_count is exposed on the snapshot itself)
    snap = registry.snapshot("q1")
    assert snap.sample_count <= 10


def test_eta_calculation():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(0, 100_000_000)
    clock.advance(1.0)
    reporter(40_000_000, 100_000_000)  # 40MB in 1s -> 40MB/s
    snap = registry.snapshot("q1")
    assert snap.speed_bps == 40_000_000.0
    assert abs(snap.eta_seconds - 1.5) < 0.001  # 60MB remaining / 40MB/s


# --- stale speed (§40/§77) ---------------------------------------------------


def test_stale_speed_becomes_zero_equivalent_none():
    registry, clock = _registry(speed_stale_after_seconds=4.0)
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(0, None)
    clock.advance(1.0)
    reporter(10_000_000, None)
    snap = registry.snapshot("q1")
    assert snap.speed_bps is not None

    clock.advance(5.0)  # exceeds stale threshold, no new progress
    stale_snap = registry.snapshot("q1")
    assert stale_snap.speed_bps is None
    assert stale_snap.eta_seconds is None
    assert stale_snap.bytes_downloaded == 10_000_000  # bytes preserved


# --- duplicate / regression / negative (§27-29) -------------------------------


def test_duplicate_byte_sample_is_valid():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(1000, None)
    clock.advance(1.0)
    reporter(1000, None)  # same value again -- valid, not corruption
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 1000
    assert registry.drain_telemetry_issues() == []


def test_byte_regression_ignored_preserves_previous():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(1000, None)
    reporter(900, None)  # regression -- must be ignored
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 1000
    issues = registry.drain_telemetry_issues()
    assert any(i.kind == ProgressTelemetryIssueKind.BYTES_REGRESSION for i in issues)


def test_negative_bytes_ignored():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(-5, None)
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 0
    assert snap.has_started is False
    issues = registry.drain_telemetry_issues()
    assert any(i.kind == ProgressTelemetryIssueKind.NEGATIVE_BYTES for i in issues)


# --- retry reset (§14/§81) ----------------------------------------------------


def test_retry_reset_clears_previous_attempt_telemetry():
    registry, clock = _registry()
    reporter1 = registry.begin_attempt("t1", "q1", 1)
    reporter1(80_000_000, 100_000_000)
    clock.advance(1.0)
    reporter1(90_000_000, 100_000_000)

    reporter2 = registry.begin_attempt("t1", "q1", 2)  # new attempt -- full reset
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 0
    assert snap.speed_bps is None
    assert snap.eta_seconds is None
    assert snap.attempt_number == 2
    assert snap.sample_count == 0


# --- stale attempt / queue occurrence callback (§56-58) -----------------------


def test_stale_prior_attempt_callback_does_not_affect_current():
    registry, clock = _registry()
    reporter1 = registry.begin_attempt("t1", "q1", 1)
    reporter2 = registry.begin_attempt("t1", "q1", 2)  # attempt 2 now current
    reporter2(500, None)

    reporter1(999_999, None)  # late callback from the OLD attempt

    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 500  # untouched by the stale attempt 1 callback
    assert snap.attempt_number == 2
    issues = registry.drain_telemetry_issues()
    assert any(i.kind == ProgressTelemetryIssueKind.STALE_ATTEMPT for i in issues)


def test_stale_old_queue_occurrence_callback_does_not_affect_new_occurrence():
    registry, clock = _registry()
    reporter_x = registry.begin_attempt("t1", "qX", 1)  # old occurrence X
    # same task later re-associated with a NEW occurrence Y
    registry.begin_attempt("t1", "qY", 1)
    reporter_y_direct = registry.begin_attempt("t1", "qY", 1)  # fresh reporter for Y
    reporter_y_direct(777, None)

    reporter_x(1, None)  # late callback referencing the old queue_entry_id "qX"

    snap_y = registry.snapshot("qY")
    assert snap_y.bytes_downloaded == 777  # untouched
    # X's own record was never touched by this (X wasn't stale relative to
    # itself -- this proves X and Y are fully independent records keyed by
    # queue_entry_id, not accidentally sharing state via task_id).
    snap_x = registry.snapshot("qX")
    assert snap_x.bytes_downloaded == 1


# --- concurrent reporting (§82) -----------------------------------------------


def test_concurrent_reporting_no_lost_updates_no_cross_contamination():
    import time as real_time

    registry = ProgressRegistry(monotonic=real_time.monotonic)
    reporters = {f"q{i}": registry.begin_attempt(f"t{i}", f"q{i}", 1) for i in range(8)}

    def _worker(key, reporter):
        for step in range(1, 51):
            reporter(step * 1000, 50_000)

    threads = [threading.Thread(target=_worker, args=(k, r)) for k, r in reporters.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    for i in range(8):
        snap = registry.snapshot(f"q{i}")
        assert snap.bytes_downloaded == 50_000
        assert snap.task_id == f"t{i}"


# --- telemetry issue never fails download (structural) ------------------------


def test_malformed_update_does_not_raise():
    registry, clock = _registry()
    reporter = registry.begin_attempt("t1", "q1", 1)
    reporter(-1, None)  # must not raise
    reporter(10, None)
    reporter(5, None)  # regression, must not raise
    snap = registry.snapshot("q1")
    assert snap.bytes_downloaded == 10  # last valid value preserved


# --- structural import test --------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.progress as module

    forbidden = {"PySide6", "requests", "httpx", "yt_dlp"}
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
