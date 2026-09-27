"""Prompt A11: DownloadManagerWidget unit tests against a fake
DownloadManagerService test double -- isolates GUI rendering/command
logic from real backend/network behavior. Real end-to-end HTTP GUI tests
live in test_gui_e2e.py."""

import threading
import time
from datetime import datetime, timezone

from PySide6.QtCore import QThread

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import (
    AddDownloadResult,
    CommandStatus,
    ManagerCommandResult,
    ManagerEvent,
    ManagerEventKind,
    ManagerState,
)
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadManagerSnapshot, DownloadViewSnapshot
from rychlik.gui.download_manager_widget import DownloadManagerWidget


def _item(**overrides) -> DownloadViewSnapshot:
    defaults = dict(
        task_id="t1",
        queue_entry_id="q1",
        display_name="video.mp4",
        task_state=DownloadTaskState.READY,
        queue_state=QueueEntryState.QUEUED,
        priority=QueuePriority.NORMAL,
        position=0,
        attempt_count=0,
        bytes_downloaded=0,
        total_bytes=None,
        progress_fraction=None,
        speed_bps=None,
        eta_seconds=None,
        last_failure_code=None,
    )
    defaults.update(overrides)
    return DownloadViewSnapshot(**defaults)


class _FakeManager:
    def __init__(self, items=(), state=ManagerState.RUNNING, recovery_report=None, completed_file_result=None):
        self.state = state
        self.last_recovery_report = recovery_report
        self._items = {i.queue_entry_id: i for i in items}
        self._completed_file_result = completed_file_result
        self._subscribers = {}
        self._next_token = 1
        self.calls = []

    def set_items(self, items):
        self._items = {i.queue_entry_id: i for i in items}

    def snapshot(self):
        return DownloadManagerSnapshot(items=tuple(self._items.values()), active_transfer_count=0, aggregate_speed_bps=None)

    def item_snapshot(self, queue_entry_id):
        return self._items.get(queue_entry_id)

    def subscribe(self, callback):
        token = self._next_token
        self._next_token += 1
        self._subscribers[token] = callback
        return token

    def unsubscribe(self, token):
        self._subscribers.pop(token, None)

    def emit(self, event):
        for callback in list(self._subscribers.values()):
            callback(event)

    def add_download(self, request, priority=QueuePriority.NORMAL):
        self.calls.append(("add_download", request, priority))
        return AddDownloadResult(task_id="new-task", queue_entry_id="new-qe")

    def hold(self, queue_entry_id):
        self.calls.append(("hold", queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def release_hold(self, queue_entry_id):
        self.calls.append(("release_hold", queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def pause_transfer(self, queue_entry_id):
        self.calls.append(("pause_transfer", queue_entry_id))
        return ManagerCommandResult(CommandStatus.ACCEPTED, queue_entry_id=queue_entry_id)

    def resume_transfer(self, queue_entry_id):
        self.calls.append(("resume_transfer", queue_entry_id))
        return ManagerCommandResult(CommandStatus.ACCEPTED, queue_entry_id=queue_entry_id)

    def cancel(self, queue_entry_id):
        self.calls.append(("cancel", queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def retry_now(self, queue_entry_id):
        self.calls.append(("retry_now", queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def set_priority(self, queue_entry_id, priority):
        self.calls.append(("set_priority", queue_entry_id, priority))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def move_before(self, queue_entry_id, target_queue_entry_id):
        self.calls.append(("move_before", queue_entry_id, target_queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def move_after(self, queue_entry_id, target_queue_entry_id):
        self.calls.append(("move_after", queue_entry_id, target_queue_entry_id))
        return ManagerCommandResult(CommandStatus.APPLIED, queue_entry_id=queue_entry_id)

    def completed_file(self, queue_entry_id):
        self.calls.append(("completed_file", queue_entry_id))
        if self._completed_file_result is not None:
            return self._completed_file_result
        from rychlik.core.download_manager_service import CompletedFileResult, CompletedFileStatus

        return CompletedFileResult(CompletedFileStatus.NOT_COMPLETED, reason="task is not COMPLETED")


def _select(widget, queue_entry_id):
    for row in range(widget.table.rowCount()):
        item = widget.table.item(row, 0)
        if item.data(256) == queue_entry_id:  # Qt.ItemDataRole.UserRole == 256
            widget.table.selectRow(row)
            return
    raise AssertionError(f"row for {queue_entry_id!r} not found")


# --- construction / initial snapshot -----------------------------------------


def test_window_construction_no_exception(qapp):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    assert widget.table.rowCount() == 0
    assert widget.download_button.isEnabled()


def test_initial_snapshot_renders_items_in_order(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="a", display_name="a.mp4"), _item(queue_entry_id="b", display_name="b.mp4")])
    widget = DownloadManagerWidget(manager)
    assert widget.table.rowCount() == 2
    assert widget.table.item(0, 0).text() == "a.mp4"
    assert widget.table.item(1, 0).text() == "b.mp4"


def test_recovery_notice_shown_when_actions_present(qapp):
    from rychlik.core.restart_recovery import RecoveryAction, RecoveryActionReason, RecoveryReport

    report = RecoveryReport(
        previous_shutdown_clean=False,
        actions=(RecoveryAction("t1", "q1", "TRANSFERRING", "READY", RecoveryActionReason.INTERRUPTED_TRANSFER),),
        restored_task_count=1, restored_queue_entry_count=1, restored_retry_count=0,
    )
    manager = _FakeManager(recovery_report=report)
    widget = DownloadManagerWidget(manager)
    assert "Recovered" in widget.status_label.text()


# --- stable identity / selection ---------------------------------------------


def test_selection_survives_reorder_by_identity(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="a", display_name="a.mp4"), _item(queue_entry_id="b", display_name="b.mp4")])
    widget = DownloadManagerWidget(manager)
    _select(widget, "b")
    assert widget._selected_queue_entry_id() == "b"

    # Reorder: b now comes first.
    manager.set_items([_item(queue_entry_id="b", display_name="b.mp4"), _item(queue_entry_id="a", display_name="a.mp4")])
    widget._refresh_now()
    assert widget._selected_queue_entry_id() == "b"
    assert widget.table.item(0, 0).text() == "b.mp4"


# --- button enablement ---------------------------------------------------------


def test_enablement_ready_queued(qapp):
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.READY, queue_state=QueueEntryState.QUEUED)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert widget.hold_button.isEnabled()
    assert not widget.release_button.isEnabled()
    assert not widget.pause_button.isEnabled()
    assert not widget.resume_button.isEnabled()
    assert not widget.retry_button.isEnabled()
    assert widget.cancel_button.isEnabled()
    assert not widget.share_button.isEnabled()


def test_enablement_queue_held(qapp):
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.READY, queue_state=QueueEntryState.PAUSED)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert not widget.hold_button.isEnabled()
    assert widget.release_button.isEnabled()


def test_enablement_transferring(qapp):
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.TRANSFERRING)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert widget.pause_button.isEnabled()
    assert not widget.resume_button.isEnabled()
    assert widget.hold_button.isEnabled()  # hold is purely queue-level (§54), independent of active transfer
    assert widget.cancel_button.isEnabled()


def test_enablement_task_paused(qapp):
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.PAUSED)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert widget.resume_button.isEnabled()
    assert not widget.pause_button.isEnabled()


def test_enablement_retry_wait(qapp):
    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.RETRY_WAIT)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert widget.retry_button.isEnabled()


def test_enablement_terminal_states_disable_cancel(qapp):
    for state in (DownloadTaskState.COMPLETED, DownloadTaskState.FAILED, DownloadTaskState.CANCELLED):
        manager = _FakeManager(items=[_item(task_state=state, queue_state=QueueEntryState.REMOVED)])
        widget = DownloadManagerWidget(manager)
        _select(widget, "q1")
        assert not widget.cancel_button.isEnabled(), state


def test_enablement_share_and_open_folder_only_for_completed(qapp):
    for state in (DownloadTaskState.FAILED, DownloadTaskState.CANCELLED, DownloadTaskState.READY):
        manager = _FakeManager(items=[_item(task_state=state, queue_state=QueueEntryState.REMOVED)])
        widget = DownloadManagerWidget(manager)
        _select(widget, "q1")
        assert not widget.share_button.isEnabled(), state
        assert not widget.open_folder_button.isEnabled(), state

    manager = _FakeManager(items=[_item(task_state=DownloadTaskState.COMPLETED, queue_state=QueueEntryState.REMOVED)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert widget.share_button.isEnabled()
    assert widget.open_folder_button.isEnabled()


# --- add download --------------------------------------------------------------


def test_download_button_calls_add_download_only(qapp):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    widget.url_input.setText("https://example.test/video.mp4")
    widget.download_button.click()
    assert len(manager.calls) == 1
    call = manager.calls[0]
    assert call[0] == "add_download"
    assert isinstance(call[1], DownloadRequest)
    assert call[1].url == "https://example.test/video.mp4"


def test_download_button_empty_url_does_not_call_manager(qapp, monkeypatch):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    monkeypatch.setattr("rychlik.gui.download_manager_widget.QMessageBox.warning", lambda *a, **k: None)
    widget.download_button.click()
    assert manager.calls == []


# --- commands: exact queue_entry_id, no immediate state mutation --------------


def test_hold_calls_manager_with_exact_id(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1")])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.hold_button.click()
    assert ("hold", "q1") in manager.calls


def test_pause_calls_manager_without_forcing_row_state(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1", task_state=DownloadTaskState.TRANSFERRING)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.pause_button.click()
    assert ("pause_transfer", "q1") in manager.calls
    # ACCEPTED must not immediately relabel the row as Paused -- only a
    # subsequent snapshot (still reporting TRANSFERRING here) does that.
    assert widget.table.item(0, 1).text() == "Downloading"


def test_resume_calls_manager_with_exact_id(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1", task_state=DownloadTaskState.PAUSED)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.resume_button.click()
    assert ("resume_transfer", "q1") in manager.calls


def test_cancel_calls_manager_with_exact_id(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1")])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.cancel_button.click()
    assert ("cancel", "q1") in manager.calls


def test_retry_now_calls_manager_with_exact_id(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1", task_state=DownloadTaskState.RETRY_WAIT)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.retry_button.click()
    assert ("retry_now", "q1") in manager.calls


def test_priority_combo_calls_set_priority(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1", priority=QueuePriority.NORMAL)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    widget.priority_combo.setCurrentIndex(0)  # "High"
    assert ("set_priority", "q1", QueuePriority.HIGH) in manager.calls


def test_move_up_down_use_move_before_after(qapp):
    manager = _FakeManager(items=[
        _item(queue_entry_id="a", priority=QueuePriority.NORMAL, position=0),
        _item(queue_entry_id="b", priority=QueuePriority.NORMAL, position=1),
    ])
    widget = DownloadManagerWidget(manager)
    _select(widget, "b")
    widget.up_button.click()
    assert ("move_before", "b", "a") in manager.calls

    manager.calls.clear()
    _select(widget, "a")
    widget.down_button.click()
    assert ("move_after", "a", "b") in manager.calls


def test_move_up_at_band_edge_is_noop(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="a", priority=QueuePriority.NORMAL, position=0)])
    widget = DownloadManagerWidget(manager)
    _select(widget, "a")
    widget.up_button.click()
    assert manager.calls == []


# --- Qt thread bridge (§99) ----------------------------------------------------


def test_backend_event_from_background_thread_delivered_on_gui_thread(qapp):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    received = {}

    def check(event):
        received["thread"] = QThread.currentThread()

    widget._bridge.manager_event.connect(check)

    def emit_from_background():
        manager.emit(ManagerEvent(ManagerEventKind.QUEUE_CHANGED))

    thread = threading.Thread(target=emit_from_background)
    thread.start()
    thread.join()

    deadline = time.monotonic() + 2.0
    while "thread" not in received and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert received.get("thread") is QThread.currentThread()  # this assertion runs on the GUI/main thread


# --- late event after close / bad subscriber-side error -----------------------


def test_late_event_after_shutdown_does_not_crash(qapp):
    manager = _FakeManager()
    widget = DownloadManagerWidget(manager)
    widget.shutdown()
    # Simulate a very-late delivery reaching the (already-detached) bridge directly.
    widget._on_manager_event(ManagerEvent(ManagerEventKind.QUEUE_CHANGED))  # must not raise
    qapp.processEvents()


def test_bad_backend_command_shows_error_without_crashing(qapp, monkeypatch):
    manager = _FakeManager(items=[_item(queue_entry_id="q1")])

    def boom(queue_entry_id):
        raise RuntimeError("disk full")

    manager.hold = boom
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")

    warnings = []
    monkeypatch.setattr(
        "rychlik.gui.download_manager_widget.QMessageBox.warning",
        lambda *a, **k: warnings.append(a),
    )
    widget.hold_button.click()  # must not raise
    assert len(warnings) == 1


# --- faulted service ------------------------------------------------------------


def test_faulted_service_disables_mutating_controls(qapp):
    manager = _FakeManager(items=[_item(queue_entry_id="q1")])
    widget = DownloadManagerWidget(manager)
    _select(widget, "q1")
    assert widget.hold_button.isEnabled()

    manager.state = ManagerState.FAULTED
    widget._refresh_now()

    assert not widget.download_button.isEnabled()
    assert not widget.hold_button.isEnabled()
    assert not widget.cancel_button.isEnabled()
    assert "faulted" in widget.status_label.text().lower()
