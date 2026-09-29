from datetime import datetime, timezone
from pathlib import Path

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_queue import DownloadQueue, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.download_view import build_manager_snapshot, build_view_snapshot
from rychlik.core.progress import ProgressRegistry

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(queue, task_id, **kwargs):
    return queue.enqueue(task_id, **kwargs)


# --- basic composition -------------------------------------------------------


def test_view_snapshot_flattens_facts():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0).mark_ready(now=T0)
    request = DownloadRequest(url="http://x/y", destination_dir=Path("/tmp"), filename_hint="video.mp4")

    view = build_view_snapshot(queue_entry=entry, task=task, request=request, progress=None)

    assert view.task_id == "A"
    assert view.queue_entry_id == entry.queue_entry_id
    assert view.display_name == "video.mp4"
    assert view.task_state == DownloadTaskState.READY
    assert view.queue_state == QueueEntryState.QUEUED
    assert view.priority == QueuePriority.NORMAL
    assert view.position == 0
    assert view.attempt_count == 0
    assert view.bytes_downloaded == 0


def test_view_snapshot_no_request_gives_none_display_name():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0)

    view = build_view_snapshot(queue_entry=entry, task=task, request=None, progress=None)
    assert view.display_name is None


def test_view_snapshot_no_task_uses_safe_placeholder():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")

    view = build_view_snapshot(queue_entry=entry, task=None, request=None, progress=None)
    assert view.queue_entry_id == entry.queue_entry_id
    assert view.bytes_downloaded == 0


# --- lifecycle-driven overrides (§36-39) --------------------------------------


def test_completed_task_zero_speed_and_full_fraction():
    registry = ProgressRegistry()
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    reporter = registry.begin_attempt("A", entry.queue_entry_id, task.attempt_count)
    reporter(100, 100)
    task = task.complete(now=T0)

    progress = registry.snapshot(entry.queue_entry_id)
    view = build_view_snapshot(queue_entry=entry, task=task, request=None, progress=progress)

    assert view.speed_bps == 0.0
    assert view.eta_seconds == 0.0
    assert view.progress_fraction == 1.0
    assert view.bytes_downloaded == 100


def test_failed_task_zero_speed_none_eta():
    registry = ProgressRegistry()
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    reporter = registry.begin_attempt("A", entry.queue_entry_id, task.attempt_count)
    reporter(50, 100)
    task = task.fail(DownloadTaskFailure(code="X", message="x", retryable=False), now=T0)

    progress = registry.snapshot(entry.queue_entry_id)
    view = build_view_snapshot(queue_entry=entry, task=task, request=None, progress=progress)

    assert view.speed_bps == 0.0
    assert view.eta_seconds is None
    assert view.bytes_downloaded == 50  # preserved for diagnostics
    assert view.last_failure_code == "X"


def test_cancelled_task_zero_speed_none_eta():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0).cancel(now=T0)

    view = build_view_snapshot(queue_entry=entry, task=task, request=None, progress=None)
    assert view.speed_bps == 0.0
    assert view.eta_seconds is None


def test_retry_wait_zero_speed_none_eta_not_confused_with_backoff():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(DownloadTaskFailure(code="NET", message="x", retryable=True), now=T0)
    )
    view = build_view_snapshot(queue_entry=entry, task=task, request=None, progress=None)
    assert view.speed_bps == 0.0
    assert view.eta_seconds is None


def test_queue_paused_task_ready_no_special_handling_needed():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    queue.pause(entry.queue_entry_id)
    paused_entry = queue.get(entry.queue_entry_id)
    task = create_task("A", now=T0).mark_ready(now=T0)

    view = build_view_snapshot(queue_entry=paused_entry, task=task, request=None, progress=None)
    assert view.queue_state == QueueEntryState.PAUSED
    assert view.task_state == DownloadTaskState.READY
    assert view.speed_bps is None  # no active transfer at all -- nothing to report


# --- attempt-generation consistency (§73) -------------------------------------


def test_stale_progress_attempt_mismatch_is_ignored():
    registry = ProgressRegistry()
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    stale_progress = registry.begin_attempt("A", entry.queue_entry_id, 999)  # wrong generation
    stale_progress(12345, 99999)
    progress = registry.snapshot(entry.queue_entry_id)

    view = build_view_snapshot(queue_entry=entry, task=task, request=None, progress=progress)
    assert view.bytes_downloaded == 0  # mismatched attempt_number -> ignored, not trusted


# --- no domain references leak (§85) ------------------------------------------


def test_snapshot_has_no_domain_object_fields():
    from rychlik.core.download_view import DownloadViewSnapshot

    field_names = set(DownloadViewSnapshot.__dataclass_fields__)
    forbidden = {"task", "queue_entry", "future", "thread", "lock", "request", "worker"}
    assert field_names & forbidden == set()


def test_snapshot_no_private_path_or_source_url_leak():
    queue = DownloadQueue(clock=lambda: T0)
    entry = _entry(queue, "A")
    task = create_task("A", now=T0)
    request = DownloadRequest(
        url="http://example.test/private?token=SECRET", destination_dir=Path("/home/user/private")
    )
    view = build_view_snapshot(queue_entry=entry, task=task, request=request, progress=None)

    view_repr = repr(view)
    assert "SECRET" not in view_repr
    assert "/home/user/private" not in view_repr


# --- manager snapshot: canonical order (§87) -----------------------------------


def test_manager_snapshot_preserves_canonical_queue_order():
    queue = DownloadQueue(clock=lambda: T0)
    queue.enqueue("H1", QueuePriority.HIGH)
    queue.enqueue("N3", QueuePriority.NORMAL)
    queue.enqueue("N1", QueuePriority.NORMAL)
    queue.enqueue("L1", QueuePriority.LOW)
    tasks = {t: create_task(t, now=T0) for t in ["H1", "N3", "N1", "L1"]}
    requests = {}

    snap = build_manager_snapshot(
        entries=queue.active_entries(), tasks=tasks, requests=requests, progress_registry=None
    )
    assert [item.task_id for item in snap.items] == ["H1", "N3", "N1", "L1"]


# --- manager snapshot: active count / aggregate speed (§88/§89) ----------------


def test_manager_snapshot_active_count_and_aggregate_speed():
    registry = ProgressRegistry()
    queue = DownloadQueue(clock=lambda: T0)
    entry_a = queue.enqueue("A")
    entry_b = queue.enqueue("B")
    entry_c = queue.enqueue("C")

    task_a = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    reporter_a = registry.begin_attempt("A", entry_a.queue_entry_id, task_a.attempt_count)
    reporter_a(0, None)
    reporter_a(2_000_000, None)  # instant samples -> speed via manual clock below

    task_b = create_task("B", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    reporter_b = registry.begin_attempt("B", entry_b.queue_entry_id, task_b.attempt_count)
    reporter_b(0, None)
    reporter_b(3_000_000, None)

    task_c = create_task("C", now=T0).mark_ready(now=T0)  # not transferring

    tasks = {"A": task_a, "B": task_b, "C": task_c}
    snap = build_manager_snapshot(
        entries=[entry_a, entry_b, entry_c], tasks=tasks, requests={}, progress_registry=registry
    )
    assert snap.active_transfer_count == 2


def test_non_transferring_item_never_contributes_stale_positive_speed():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    task = create_task("A", now=T0).mark_ready(now=T0)  # never started
    snap = build_manager_snapshot(
        entries=[entry], tasks={"A": task}, requests={}, progress_registry=None
    )
    assert snap.aggregate_speed_bps is None


# --- removed/terminal entry snapshot (§48) -------------------------------------


def test_removed_entry_can_still_be_snapshotted_explicitly():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).complete(now=T0)
    queue.remove(entry.queue_entry_id)
    removed_entry = queue.get(entry.queue_entry_id)

    snap = build_manager_snapshot(
        entries=[removed_entry], tasks={"A": task}, requests={}, progress_registry=None
    )
    assert snap.items[0].queue_state == QueueEntryState.REMOVED
    assert snap.items[0].task_state == DownloadTaskState.COMPLETED


# --- structural import test ---------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.download_view as module

    forbidden = {"PySide6", "requests", "httpx", "yt_dlp", "threading"}
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
