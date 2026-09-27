"""Prompt A12: DownloadManagerWidget hardening -- destination workflow,
Enter-key add, double-submit guard, empty state, status summary, shutdown
confirmation, and the Share/Open-Folder completed-file bridge. Against the
same fake DownloadManagerService test double as test_download_manager_
widget.py (imported directly to avoid duplicating it)."""

from datetime import datetime, timezone
from pathlib import Path

from rychlik.core.download_manager_service import (
    CommandStatus,
    CompletedFileInfo,
    CompletedFileResult,
    CompletedFileStatus,
    ManagerCommandResult,
    ManagerEvent,
    ManagerEventKind,
    ManagerState,
)
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadManagerSnapshot
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from test_download_manager_widget import _FakeManager, _item, _select


# --- destination workflow ------------------------------------------------------


def test_destination_default_is_a_directory(qapp):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    assert Path(widget.destination_display.text()).is_dir()


def test_browse_updates_destination(qapp, tmp_path):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager, destination_chooser=lambda parent, start: str(tmp_path))
    widget.browse_button.click()
    assert widget._destination_dir == tmp_path
    assert widget.destination_display.text() == str(tmp_path)


def test_browse_cancelled_keeps_existing_destination(qapp, tmp_path):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager, destination_chooser=lambda parent, start: "")
    original = widget._destination_dir
    widget.browse_button.click()
    assert widget._destination_dir == original


def test_invalid_destination_blocks_add(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("rychlik.gui.download_manager_widget.QMessageBox.warning", lambda *a, **k: None)
    manager = _FakeManager()
    not_a_dir = tmp_path / "not-a-real-dir"
    widget = DownloadManagerWidget(manager, destination_chooser=lambda parent, start: str(not_a_dir))
    widget.browse_button.click()  # rejected: not_a_dir does not exist
    assert widget._destination_dir != not_a_dir

    widget.url_input.setText("https://example.test/x")
    # Force an invalid destination directly to exercise the add-time guard too.
    widget._destination_dir = not_a_dir
    widget.download_button.click()
    assert manager.calls == []


def test_destination_change_applies_to_next_add_only(qapp, tmp_path):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()

    widget._destination_dir = dir_a
    widget.url_input.setText("http://example.test/a")
    widget.download_button.click()

    widget._destination_dir = dir_b
    widget.url_input.setText("http://example.test/b")
    widget.download_button.click()

    assert manager.calls[0][1].destination_dir == dir_a
    assert manager.calls[1][1].destination_dir == dir_b


# --- Enter-key add / double-submit guard ---------------------------------------


def test_enter_key_adds_download(qapp, tmp_path):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    widget._destination_dir = tmp_path
    widget.url_input.setText("https://example.test/enter.mp4")
    widget.url_input.returnPressed.emit()
    assert len(manager.calls) == 1
    assert manager.calls[0][0] == "add_download"


def test_double_submit_guard_prevents_reentrant_add(qapp, tmp_path):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    widget._destination_dir = tmp_path
    widget.url_input.setText("https://example.test/x.mp4")

    original_add = manager.add_download
    reentry_calls = []

    def reentrant_add(request, priority=QueuePriority.NORMAL):
        # Simulate a handler that is somehow re-entered while already running.
        reentry_calls.append(1)
        widget._on_download_clicked()  # must be a no-op due to the guard
        return original_add(request, priority)

    manager.add_download = reentrant_add
    widget.download_button.click()
    assert len(manager.calls) == 1  # the nested call was guarded out


# --- empty state -----------------------------------------------------------------


def test_empty_state_shown_when_no_items(qapp):
    manager = _FakeManager(items=[])
    widget = DownloadManagerWidget(manager)
    assert widget._table_stack.currentWidget() is widget.empty_state_label


def test_empty_state_hides_once_item_appears(qapp):
    manager = _FakeManager(items=[])
    widget = DownloadManagerWidget(manager)
    manager.set_items([_item(queue_entry_id="a")])
    manager.emit(ManagerEvent(ManagerEventKind.DOWNLOAD_ADDED))
    widget._refresh_now()
    assert widget._table_stack.currentWidget() is widget.table


def test_empty_state_returns_when_snapshot_becomes_empty(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="a")])
    widget = DownloadManagerWidget(manager)
    assert widget._table_stack.currentWidget() is widget.table
    manager.set_items([])
    widget._refresh_now()
    assert widget._table_stack.currentWidget() is widget.empty_state_label


# --- status summary --------------------------------------------------------------


def test_status_summary_reflects_snapshot(qapp, monkeypatch):
    manager = _FakeManager(items=[_item(queue_entry_id="a"), _item(queue_entry_id="b")])
    widget = DownloadManagerWidget(manager)

    def fake_snapshot():
        return DownloadManagerSnapshot(
            items=tuple(manager._items.values()), active_transfer_count=2, aggregate_speed_bps=5_000_000
        )

    monkeypatch.setattr(manager, "snapshot", fake_snapshot)
    widget._refresh_now()
    text = widget.summary_label.text()
    assert "2 download" in text
    assert "2 active" in text
    assert "5.0 MB/s" in text


def test_status_summary_empty_when_no_items(qapp):
    manager = _FakeManager(items=[])
    widget = DownloadManagerWidget(manager)
    assert widget.summary_label.text() == ""


# --- stale selection / async rejection hardening --------------------------------


def test_selection_cleared_when_item_disappears(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="a"), _item(queue_entry_id="b")])
    widget = DownloadManagerWidget(manager)
    _select(widget, "a")
    assert widget._selected_queue_entry_id() == "a"

    manager.set_items([_item(queue_entry_id="b")])
    widget._refresh_now()
    assert widget._selected_queue_entry_id() is None
    assert not widget.hold_button.isEnabled()


def test_command_rejection_does_not_crash_or_corrupt_selection(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="a", queue_state=QueueEntryState.QUEUED)])
    manager.hold = lambda entry_id: ManagerCommandResult(
        CommandStatus.REJECTED, queue_entry_id=entry_id, reason="occurrence is REMOVED",
    )
    widget = DownloadManagerWidget(manager)
    _select(widget, "a")
    widget.hold_button.click()  # must not raise
    assert "occurrence is REMOVED" in widget.status_label.text()
    # Widget must remain usable afterward.
    assert widget._selected_queue_entry_id() == "a"


# --- shutdown confirmation -------------------------------------------------------


def test_confirm_close_true_when_no_active_transfers(qapp):
    manager = _FakeManager(items=[])
    widget = DownloadManagerWidget(manager)
    assert widget.confirm_close() is True


def test_confirm_close_asks_and_respects_cancel(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    manager = _FakeManager(items=[_item()])

    def fake_snapshot():
        return DownloadManagerSnapshot(items=(_item(),), active_transfer_count=1, aggregate_speed_bps=None)

    manager.snapshot = fake_snapshot
    widget = DownloadManagerWidget(manager)

    monkeypatch.setattr(
        "rychlik.gui.download_manager_widget.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Cancel,
    )
    assert widget.confirm_close() is False


def test_confirm_close_accept_returns_true(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    manager = _FakeManager(items=[_item()])

    def fake_snapshot():
        return DownloadManagerSnapshot(items=(_item(),), active_transfer_count=1, aggregate_speed_bps=None)

    manager.snapshot = fake_snapshot
    widget = DownloadManagerWidget(manager)

    monkeypatch.setattr(
        "rychlik.gui.download_manager_widget.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Close,
    )
    assert widget.confirm_close() is True


def test_prepare_shutdown_disables_controls(qapp):
    manager = _FakeManager(items=[_item()])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.prepare_shutdown()
    assert not widget.download_button.isEnabled()
    assert not widget.hold_button.isEnabled()
    assert "Shutting down" in widget.status_label.text()


def test_confirm_close_not_running_returns_true(qapp):
    manager = _FakeManager(items=[], state=ManagerState.STOPPED)
    widget = DownloadManagerWidget(manager)
    assert widget.confirm_close() is True


# --- Share / Open Folder bridge --------------------------------------------------


def test_open_folder_uses_privileged_accessor_parent_dir(qapp, tmp_path):
    completed_path = tmp_path / "video.mp4"
    completed_path.write_bytes(b"x")
    result = CompletedFileResult(
        CompletedFileStatus.AVAILABLE,
        info=CompletedFileInfo(
            queue_entry_id="q1", task_id="t1", local_path=completed_path,
            display_name="video.mp4", size_bytes=1, completed_at_utc=datetime.now(timezone.utc),
        ),
    )
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.COMPLETED)], completed_file_result=result)
    opened = []
    widget = DownloadManagerWidget(manager, folder_opener=lambda path: opened.append(path))
    _select(widget, "q1")
    widget.open_folder_button.click()
    assert opened == [completed_path.parent]


def test_open_folder_unavailable_shows_bounded_message(qapp):
    result = CompletedFileResult(CompletedFileStatus.FILE_MISSING, reason="the completed file no longer exists")
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.COMPLETED)], completed_file_result=result)
    opened = []
    widget = DownloadManagerWidget(manager, folder_opener=lambda path: opened.append(path))
    _select(widget, "q1")
    widget.open_folder_button.click()
    assert opened == []
    assert "no longer exists" in widget.status_label.text()


def test_share_button_uses_completed_file_bridge(qapp, tmp_path, monkeypatch):
    completed_path = tmp_path / "video.mp4"
    completed_path.write_bytes(b"hello world")
    result = CompletedFileResult(
        CompletedFileStatus.AVAILABLE,
        info=CompletedFileInfo(
            queue_entry_id="q1", task_id="t1", local_path=completed_path,
            display_name="video.mp4", size_bytes=11, completed_at_utc=datetime.now(timezone.utc),
        ),
    )
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.COMPLETED)], completed_file_result=result)
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")

    opened_dialogs = []
    monkeypatch.setattr(
        "rychlik.gui.download_manager_widget.ShareDialog",
        lambda artifact, parent=None: opened_dialogs.append(artifact) or _FakeDialog(),
    )
    widget.share_button.click()
    assert len(opened_dialogs) == 1
    assert opened_dialogs[0].local_path == completed_path


def test_share_unavailable_shows_bounded_message_no_dialog(qapp, monkeypatch):
    result = CompletedFileResult(CompletedFileStatus.FILE_MISSING, reason="the completed file no longer exists")
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.COMPLETED)], completed_file_result=result)
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")

    opened_dialogs = []
    monkeypatch.setattr(
        "rychlik.gui.download_manager_widget.ShareDialog",
        lambda artifact, parent=None: opened_dialogs.append(artifact) or _FakeDialog(),
    )
    widget.share_button.click()
    assert opened_dialogs == []
    assert "no longer exists" in widget.status_label.text()


class _FakeDialog:
    def exec(self):
        return None
