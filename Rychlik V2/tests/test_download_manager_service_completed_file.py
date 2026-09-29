"""Prompt A12: DownloadManagerService.completed_file() -- the privileged
completed-file accessor and its safety properties."""

import time

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import (
    CompletedFileStatus,
    DownloadManagerConfig,
    DownloadManagerService,
)
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.state_store import SqliteDownloadStateStore


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _manager(tmp_path, **overrides) -> DownloadManagerService:
    config = DownloadManagerConfig(database_path=tmp_path / "state.db", **overrides)
    return DownloadManagerService(config=config)


def test_completed_file_unknown_occurrence(tmp_path):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        result = manager.completed_file("ghost")
        assert result.status == CompletedFileStatus.UNKNOWN_OCCURRENCE
        assert result.info is None
    finally:
        manager.stop()


def test_completed_file_rejected_for_non_completed_states(tmp_path):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        added = manager.add_download(DownloadRequest(url="http://x/y", destination_dir=tmp_path, filename_hint="y"))
        result = manager.completed_file(added.queue_entry_id)  # READY
        assert result.status == CompletedFileStatus.NOT_COMPLETED
    finally:
        manager.stop()


def test_completed_file_real_http_download(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        added = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items), timeout=10
        )
        result = manager.completed_file(added.queue_entry_id)
        assert result.status == CompletedFileStatus.AVAILABLE
        assert result.info.local_path == (tmp_path / "video").resolve()
        assert result.info.display_name == "video"
        assert result.info.size_bytes is not None
        assert result.info.queue_entry_id == added.queue_entry_id
        assert result.info.task_id == added.task_id
    finally:
        manager.stop()


def test_completed_file_survives_restart(http_fixture_server, tmp_path):
    manager1 = _manager(tmp_path, max_active_transfers=1)
    manager1.start()
    added = manager1.add_download(
        DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
    )
    assert _wait_until(
        lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager1.snapshot().items), timeout=10
    )
    manager1.stop()

    manager2 = _manager(tmp_path, max_active_transfers=0)
    manager2.start()
    try:
        result = manager2.completed_file(added.queue_entry_id)
        assert result.status == CompletedFileStatus.AVAILABLE
        assert result.info.local_path.exists()
    finally:
        manager2.stop()


def test_completed_file_missing_after_external_deletion(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        added = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items), timeout=10
        )
        (tmp_path / "video").unlink()

        result = manager.completed_file(added.queue_entry_id)
        assert result.status == CompletedFileStatus.FILE_MISSING
        # Task must remain COMPLETED -- no automatic reconciliation (§45).
        item = manager.item_snapshot(added.queue_entry_id)
        assert item.task_state == DownloadTaskState.COMPLETED
    finally:
        manager.stop()


def test_completed_file_wrong_occurrence_never_returns_other_output(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        a = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path / "a", filename_hint="video")
        )
        b = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/with-content-disposition", destination_dir=tmp_path / "b")
        )
        assert _wait_until(
            lambda: not any(i.queue_entry_id in (a.queue_entry_id, b.queue_entry_id) for i in manager.snapshot().items),
            timeout=10,
        )
        result_a = manager.completed_file(a.queue_entry_id)
        result_b = manager.completed_file(b.queue_entry_id)
        assert result_a.info.local_path != result_b.info.local_path
        assert result_a.info.queue_entry_id == a.queue_entry_id
        assert result_b.info.queue_entry_id == b.queue_entry_id
    finally:
        manager.stop()


def test_completed_file_path_tamper_rejected_and_unrelated_file_untouched(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    added = manager.add_download(
        DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
    )
    assert _wait_until(
        lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items), timeout=10
    )
    manager.stop()

    victim = tmp_path.parent / "victim.txt"
    victim.write_bytes(b"do not touch me")

    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()
    persisted = store.load()
    tampered = persisted.completed_files[added.queue_entry_id]
    from dataclasses import replace

    persisted_tampered_record = replace(tampered, local_path=victim)
    new_completed_files = dict(persisted.completed_files)
    new_completed_files[added.queue_entry_id] = persisted_tampered_record
    tampered_state = replace(persisted, completed_files=new_completed_files)
    store.replace_all(tampered_state)
    store.close()

    manager2 = _manager(tmp_path, max_active_transfers=0)
    manager2.start()
    try:
        result = manager2.completed_file(added.queue_entry_id)
        assert result.status == CompletedFileStatus.INVALID_RECORD
    finally:
        manager2.stop()

    assert victim.read_bytes() == b"do not touch me"


def test_completion_checkpoint_failure_does_not_claim_availability_or_delete_file(http_fixture_server, tmp_path, monkeypatch):
    """§113: inject a persistence failure specifically for the secondary
    completed-file checkpoint (never the primary Task COMPLETED/QueueEntry
    REMOVED checkpoint dispatch() already performed). The real file must
    never be deleted just to fake atomicity (§114), and completed_file()
    must honestly report NO_RECORD rather than a false AVAILABLE."""
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        original = manager._state_store.checkpoint_task_state

        def _boom_only_for_completed_file(**kwargs):
            if kwargs.get("completed_file") is not None:
                raise RuntimeError("disk full")
            return original(**kwargs)

        monkeypatch.setattr(manager._state_store, "checkpoint_task_state", _boom_only_for_completed_file)

        added = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items), timeout=10
        )
        # The real download still physically succeeded and was never deleted.
        assert (tmp_path / "video").exists()
        # But the durable completion record was never written -- honest gap.
        result = manager.completed_file(added.queue_entry_id)
        assert result.status == CompletedFileStatus.NO_RECORD
        item = manager.item_snapshot(added.queue_entry_id)
        assert item.task_state == DownloadTaskState.COMPLETED  # primary checkpoint still succeeded
    finally:
        manager.stop()


def test_no_local_path_in_ordinary_snapshot_regression(http_fixture_server, tmp_path):
    """Even after adding the completed-file record, the normal
    DownloadViewSnapshot must still never carry a local path (§33/§108)."""
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        added = manager.add_download(
            DownloadRequest(url="http://x/private?token=SECRET", destination_dir=tmp_path / "private")
        )
        item = manager.item_snapshot(added.queue_entry_id)
        field_names = set(item.__dataclass_fields__)
        assert "local_path" not in field_names
        assert "SECRET" not in repr(item)
        assert str(tmp_path) not in repr(item)
    finally:
        manager.stop()
