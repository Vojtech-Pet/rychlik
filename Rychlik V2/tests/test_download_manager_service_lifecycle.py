"""Prompt A10: DownloadManagerService lifecycle (start/stop idempotency,
empty/recovered start, faulted state, command-while-stopped)."""

import threading

import pytest

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import (
    DownloadManagerConfig,
    DownloadManagerService,
    ManagerFaultedError,
    ManagerNotRunningError,
    ManagerState,
)


def _service(tmp_path, **config_overrides) -> DownloadManagerService:
    config = DownloadManagerConfig(database_path=tmp_path / "state.db", **config_overrides)
    return DownloadManagerService(config=config)


def test_fresh_start_is_empty_and_running(tmp_path):
    manager = _service(tmp_path)
    report = manager.start()
    try:
        assert manager.state == ManagerState.RUNNING
        assert report.restored_task_count == 0
        assert manager.snapshot().items == ()
    finally:
        manager.stop()


def test_start_is_idempotent_on_running(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    try:
        report2 = manager.start()
        assert manager.state == ManagerState.RUNNING
        assert report2 is manager.last_recovery_report
    finally:
        manager.stop()


def test_stop_is_idempotent(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    manager.stop()
    assert manager.state == ManagerState.STOPPED
    manager.stop()  # must not raise
    assert manager.state == ManagerState.STOPPED


def test_stop_without_start_is_safe_noop(tmp_path):
    manager = _service(tmp_path)
    manager.stop()
    assert manager.state == ManagerState.STOPPED


def test_command_while_stopped_raises(tmp_path):
    manager = _service(tmp_path)
    manager.start()
    manager.stop()
    with pytest.raises(ManagerNotRunningError):
        manager.add_download(DownloadRequest(url="http://x/y", destination_dir=tmp_path))


def test_command_before_start_raises(tmp_path):
    manager = _service(tmp_path)
    with pytest.raises(ManagerNotRunningError):
        manager.snapshot()


def test_start_failure_faults_service_and_blocks_further_commands(tmp_path, monkeypatch):
    manager = _service(tmp_path)

    def boom():
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(manager._state_store, "initialize", boom)
    with pytest.raises(RuntimeError):
        manager.start()
    assert manager.state == ManagerState.FAULTED

    with pytest.raises(ManagerFaultedError):
        manager.snapshot()
    with pytest.raises(ManagerFaultedError):
        manager.start()


def test_no_thread_leak_after_stop(tmp_path):
    before = threading.active_count()
    manager = _service(tmp_path)
    manager.start()
    manager.stop()
    after = threading.active_count()
    assert after <= before


def test_recovered_start_restores_durable_state(tmp_path):
    from rychlik.core.download_queue import QueuePriority

    manager1 = _service(tmp_path)
    manager1.start()
    result = manager1.add_download(
        DownloadRequest(url="http://x/y.mp4", destination_dir=tmp_path, filename_hint="y.mp4"),
        priority=QueuePriority.HIGH,
    )
    manager1.hold(result.queue_entry_id)
    manager1.stop()

    manager2 = _service(tmp_path)
    report = manager2.start()
    try:
        assert report.previous_shutdown_clean is True
        snap = manager2.snapshot()
        assert len(snap.items) == 1
        item = snap.items[0]
        assert item.queue_entry_id == result.queue_entry_id
        assert item.priority == QueuePriority.HIGH
        assert item.queue_state.name == "PAUSED"
    finally:
        manager2.stop()
