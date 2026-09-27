"""Prompt A10: DownloadManagerService command-level tests (add_download,
hold/release, cancel of a waiting task, retry_now, priority/reorder,
unknown/removed occurrence handling) -- no real network needed for most
of these since the tasks stay non-active."""

from datetime import datetime, timezone

import pytest

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import (
    CommandStatus,
    DownloadManagerConfig,
    DownloadManagerService,
    PersistenceCommandError,
)
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState


def _service(tmp_path, **overrides) -> DownloadManagerService:
    config = DownloadManagerConfig(database_path=tmp_path / "state.db", max_active_transfers=0, **overrides)
    return DownloadManagerService(config=config)


def _request(tmp_path, name="x") -> DownloadRequest:
    return DownloadRequest(url=f"http://example.test/{name}", destination_dir=tmp_path, filename_hint=name)


# --- add_download ---------------------------------------------------------


def test_add_download_returns_stable_ids(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        snap = manager.snapshot()
        assert snap.items[0].task_id == result.task_id
        assert snap.items[0].queue_entry_id == result.queue_entry_id
        assert snap.items[0].task_state == DownloadTaskState.READY
        assert snap.items[0].queue_state == QueueEntryState.QUEUED
    finally:
        manager.stop()


def test_add_download_persistence_failure_rolls_back(tmp_path, monkeypatch):
    manager = _service(tmp_path)
    manager.start()
    try:
        monkeypatch.setattr(
            manager._state_store, "checkpoint_task_state",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
        )
        with pytest.raises(PersistenceCommandError):
            manager.add_download(_request(tmp_path))
        assert manager.snapshot().items == ()  # never visible/schedulable
    finally:
        manager.stop()


# --- hold / release_hold ---------------------------------------------------


def test_hold_pauses_queue_entry_not_task(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        outcome = manager.hold(result.queue_entry_id)
        assert outcome.status == CommandStatus.APPLIED
        item = manager.item_snapshot(result.queue_entry_id)
        assert item.queue_state == QueueEntryState.PAUSED
        assert item.task_state == DownloadTaskState.READY  # task itself untouched
    finally:
        manager.stop()


def test_hold_is_idempotent_no_op(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        manager.hold(result.queue_entry_id)
        second = manager.hold(result.queue_entry_id)
        assert second.status == CommandStatus.NO_OP
    finally:
        manager.stop()


def test_release_hold_restores_queued(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        manager.hold(result.queue_entry_id)
        outcome = manager.release_hold(result.queue_entry_id)
        assert outcome.status == CommandStatus.APPLIED
        assert manager.item_snapshot(result.queue_entry_id).queue_state == QueueEntryState.QUEUED
    finally:
        manager.stop()


# --- unknown / removed occurrence -----------------------------------------


def test_unknown_queue_entry_id_is_rejected_consistently(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        for outcome in (
            manager.hold("ghost"),
            manager.release_hold("ghost"),
            manager.pause_transfer("ghost"),
            manager.resume_transfer("ghost"),
            manager.cancel("ghost"),
            manager.retry_now("ghost"),
            manager.set_priority("ghost", QueuePriority.HIGH),
            manager.move_before("ghost", "also-ghost"),
        ):
            assert outcome.status == CommandStatus.REJECTED
            assert "unknown" in outcome.reason
        assert manager.item_snapshot("ghost") is None
    finally:
        manager.stop()


def test_old_removed_occurrence_does_not_affect_new_one(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        manager.cancel(result.queue_entry_id)  # waiting task -> synchronous cancel, REMOVED
        assert manager.item_snapshot(result.queue_entry_id).queue_state == QueueEntryState.REMOVED

        # Old occurrence commands must not affect anything now that it's REMOVED.
        outcome = manager.hold(result.queue_entry_id)
        assert outcome.status == CommandStatus.REJECTED
        assert "REMOVED" in outcome.reason
    finally:
        manager.stop()


# --- cancel: waiting task ---------------------------------------------------


def test_cancel_waiting_task_is_synchronous_and_terminal(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        outcome = manager.cancel(result.queue_entry_id)
        assert outcome.status == CommandStatus.APPLIED
        item = manager.item_snapshot(result.queue_entry_id)
        assert item.task_state == DownloadTaskState.CANCELLED
        assert item.queue_state == QueueEntryState.REMOVED
    finally:
        manager.stop()


def test_cancel_already_cancelled_is_no_op(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        manager.cancel(result.queue_entry_id)
        second = manager.cancel(result.queue_entry_id)
        assert second.status == CommandStatus.NO_OP
    finally:
        manager.stop()


def test_cancel_completed_task_is_rejected(tmp_path):
    from rychlik.core.download_task import create_task

    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        now = datetime.now(timezone.utc)
        task = manager._tasks[result.task_id]
        completed = task.start_transfer(now=now).complete(now=now)
        manager._tasks[result.task_id] = completed
        outcome = manager.cancel(result.queue_entry_id)
        assert outcome.status == CommandStatus.REJECTED
        assert "terminal" in outcome.reason
    finally:
        manager.stop()


# --- retry_now --------------------------------------------------------------


def test_retry_now_promotes_retry_wait_to_ready(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        now = datetime.now(timezone.utc)
        task = manager._tasks[result.task_id]
        failure = DownloadTaskFailure(code="NET", message="x", retryable=True)
        retry_wait_task = task.start_transfer(now=now).wait_for_retry(failure, now=now)
        manager._tasks[result.task_id] = retry_wait_task

        outcome = manager.retry_now(result.queue_entry_id)
        assert outcome.status == CommandStatus.APPLIED
        assert manager.item_snapshot(result.queue_entry_id).task_state == DownloadTaskState.READY
    finally:
        manager.stop()


def test_retry_now_rejected_when_not_retry_wait(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))  # READY, not RETRY_WAIT
        outcome = manager.retry_now(result.queue_entry_id)
        assert outcome.status == CommandStatus.REJECTED
    finally:
        manager.stop()


def test_retry_now_respects_queue_hold(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        now = datetime.now(timezone.utc)
        task = manager._tasks[result.task_id]
        failure = DownloadTaskFailure(code="NET", message="x", retryable=True)
        manager._tasks[result.task_id] = task.start_transfer(now=now).wait_for_retry(failure, now=now)
        manager.hold(result.queue_entry_id)

        manager.retry_now(result.queue_entry_id)
        item = manager.item_snapshot(result.queue_entry_id)
        assert item.task_state == DownloadTaskState.READY
        assert item.queue_state == QueueEntryState.PAUSED
    finally:
        manager.stop()


# --- priority / reorder ------------------------------------------------------


def test_set_priority_updates_snapshot_order(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        a = manager.add_download(_request(tmp_path, "a"))
        b = manager.add_download(_request(tmp_path, "b"))
        manager.set_priority(b.queue_entry_id, QueuePriority.HIGH)
        snap = manager.snapshot()
        assert [item.task_id for item in snap.items] == [b.task_id, a.task_id]
    finally:
        manager.stop()


def test_set_priority_no_op_for_same_priority(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        result = manager.add_download(_request(tmp_path))
        outcome = manager.set_priority(result.queue_entry_id, QueuePriority.NORMAL)
        assert outcome.status == CommandStatus.NO_OP
    finally:
        manager.stop()


def test_move_before_reorders_within_band(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        a = manager.add_download(_request(tmp_path, "a"))
        b = manager.add_download(_request(tmp_path, "b"))
        manager.move_before(b.queue_entry_id, a.queue_entry_id)
        snap = manager.snapshot()
        assert [item.task_id for item in snap.items] == [b.task_id, a.task_id]
    finally:
        manager.stop()


def test_reorder_persists_across_restart(tmp_path):
    manager1 = _service(tmp_path)
    manager1.start()
    a = manager1.add_download(_request(tmp_path, "a"))
    b = manager1.add_download(_request(tmp_path, "b"))
    manager1.move_before(b.queue_entry_id, a.queue_entry_id)
    manager1.stop()

    manager2 = _service(tmp_path)
    manager2.start()
    try:
        snap = manager2.snapshot()
        assert [item.task_id for item in snap.items] == [b.task_id, a.task_id]
    finally:
        manager2.stop()


def test_cross_priority_reorder_rejected(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        a = manager.add_download(_request(tmp_path, "a"))
        b = manager.add_download(_request(tmp_path, "b"), priority=QueuePriority.HIGH)
        outcome = manager.move_before(a.queue_entry_id, b.queue_entry_id)
        assert outcome.status == CommandStatus.REJECTED
    finally:
        manager.stop()


# --- structural import test -------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.download_manager_service as module

    forbidden = {"PySide6", "ShareLink", "SharePreview", "FriendSend"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    top_level = {name.split(".")[0] for name in imported}
    assert top_level & forbidden == set()
