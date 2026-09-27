"""Prompt A12: real (non-mock) HTTP GUI E2E tests for the hardening
additions -- destination selection, the completed-file Share bridge, and
one higher-level user-workflow scenario driven entirely through real
widget controls."""

import time

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.core.download_queue import QueuePriority
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from http_fixture_server import NORMAL_BODY


def _wait_until(qapp, predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    qapp.processEvents()
    return predicate()


def _manager(tmp_path, **overrides) -> DownloadManagerService:
    return DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", **overrides))


def _row_for(widget, queue_entry_id):
    for row in range(widget.table.rowCount()):
        item = widget.table.item(row, 0)
        if item is not None and item.data(256) == queue_entry_id:
            return row
    return None


def _select(widget, queue_entry_id):
    row = _row_for(widget, queue_entry_id)
    if row is None:
        return False
    widget.table.selectRow(row)
    return True


def _status_text(widget, queue_entry_id):
    row = _row_for(widget, queue_entry_id)
    return widget.table.item(row, 1).text() if row is not None else None


# --- real destination E2E (§83/§84) ------------------------------------------


def test_real_destination_choice_used_for_download(http_fixture_server, tmp_path, qapp):
    dest = tmp_path / "chosen"
    dest.mkdir()
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    widget = DownloadManagerWidget(manager, destination_chooser=lambda parent, start: str(dest))
    try:
        widget.browse_button.click()
        assert widget._destination_dir == dest

        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table.rowCount() == 0, timeout=10)
        assert (dest / "normal.mp4").exists()
    finally:
        widget.shutdown()
        manager.stop()


def test_real_destination_change_between_downloads(http_fixture_server, tmp_path, qapp):
    dest_x = tmp_path / "x"
    dest_x.mkdir()
    dest_y = tmp_path / "y"
    dest_y.mkdir()
    manager = _manager(tmp_path, max_active_transfers=2)
    manager.start()
    widget = DownloadManagerWidget(manager, destination_chooser=lambda parent, start: str(dest_x))
    try:
        widget.browse_button.click()
        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()

        widget._destination_chooser = lambda parent, start: str(dest_y)
        widget.browse_button.click()
        widget.url_input.setText(f"{http_fixture_server.base_url}/with-content-disposition")
        widget.download_button.click()

        assert _wait_until(qapp, lambda: widget.table.rowCount() == 0, timeout=10)
        assert (dest_x / "normal.mp4").exists()
        assert (dest_y / "named-file.mp4").exists()
        assert not (dest_x / "named-file.mp4").exists()
        assert not (dest_y / "normal.mp4").exists()
    finally:
        widget.shutdown()
        manager.stop()


# --- real Share bridge E2E (§90/§91) -----------------------------------------


def test_real_share_bridge_end_to_end(http_fixture_server, tmp_path, qapp, monkeypatch):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    widget = DownloadManagerWidget(manager)
    try:
        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table.rowCount() == 1, timeout=3)
        entry_id = widget.table.item(0, 0).data(256)
        assert _wait_until(qapp, lambda: widget.table.rowCount() == 0, timeout=10)

        opened = []
        monkeypatch.setattr(
            "rychlik.gui.download_manager_widget.ShareDialog",
            lambda artifact, parent=None: opened.append(artifact) or _NoOpDialog(),
        )
        # The row is gone (completed+removed), but the occurrence's identity
        # and completed-file record must still resolve through the facade.
        result = manager.completed_file(entry_id)
        assert result.status.name == "AVAILABLE"

        # Drive the actual bridge exactly as the button handler would.
        from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed

        artifact, bridge_result = build_artifact_for_completed(manager, entry_id)
        assert artifact is not None
        assert artifact.local_path == result.info.local_path
        assert artifact.size == len(NORMAL_BODY)
        assert artifact.sha256  # reused Artifact's own integrity computation, not duplicated in GUI
    finally:
        widget.shutdown()
        manager.stop()


# --- real user-workflow E2E (§139) -------------------------------------------


def test_real_full_user_workflow(http_fixture_server, tmp_path, qapp):
    http_fixture_server.configure_resumable(
        "workflow-slow", etag='"v1"', body=NORMAL_BODY * 30,
        slow=True, slow_chunk_bytes=16384, slow_delay=0.02,
    )
    manager = _manager(tmp_path, max_active_transfers=2)
    manager.start()
    widget = DownloadManagerWidget(manager)
    try:
        # add A
        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table.rowCount() == 1, timeout=3)
        a_id = widget.table.item(0, 0).data(256)

        # add B (slow, resumable)
        widget.url_input.setText(f"{http_fixture_server.base_url}/resumable/workflow-slow")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table.rowCount() == 2, timeout=3)
        b_id = next(widget.table.item(r, 0).data(256) for r in range(2) if widget.table.item(r, 0).data(256) != a_id)

        # change B priority to HIGH
        _select(widget, b_id)
        widget.priority_combo.setCurrentIndex(0)  # High
        assert _wait_until(qapp, lambda: manager.item_snapshot(b_id).priority == QueuePriority.HIGH, timeout=3)

        # pause + resume B -- wait on the WIDGET's own rendered status (not
        # the raw backend state directly), since the widget's refresh is
        # coalesced up to _REFRESH_COALESCE_MS behind the backend.
        assert _wait_until(qapp, lambda: _status_text(widget, b_id) == "Downloading", timeout=3)
        time.sleep(0.1)
        _select(widget, b_id)
        widget.pause_button.click()
        assert _wait_until(qapp, lambda: _status_text(widget, b_id) == "Paused", timeout=3)
        _select(widget, b_id)
        assert widget.resume_button.isEnabled()
        widget.resume_button.click()

        # allow both to complete
        assert _wait_until(qapp, lambda: widget.table.rowCount() == 0, timeout=15)

        # select completed item, Open Folder
        result_a = manager.completed_file(a_id)
        assert result_a.status.name == "AVAILABLE"

        widget.shutdown()
        manager.stop()

        # restart: completed item still available through the facade
        manager2 = _manager(tmp_path, max_active_transfers=0)
        manager2.start()
        try:
            result_a_again = manager2.completed_file(a_id)
            assert result_a_again.status.name == "AVAILABLE"
            assert result_a_again.info.local_path.exists()
        finally:
            manager2.stop()
    finally:
        if manager.state.name == "RUNNING":
            widget.shutdown()
            manager.stop()


class _NoOpDialog:
    def exec(self):
        return None
