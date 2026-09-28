"""Final acceptance: the production desktop widget driven through its visible controls (context-menu actions, bulk
bar buttons, sort/filter, selection) against a REAL DownloadManagerService and the real HTTP fixture server.
Scheduler behaviour is observed from the order in which the server actually receives requests -- never from the UI."""

from __future__ import annotations

import time

from PySide6.QtCore import QItemSelectionModel, Qt

from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.gui import presentation as P
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from gui_legacy_adapter import _find_action

T, Q = DownloadTaskState, QueueEntryState
SMALL = b"small body " * 200
BIG = b"x" * (2 * 1024 * 1024)
SLOW = dict(slow=True, slow_chunk_bytes=16384, slow_delay=0.03)
COMMANDS = {"hold", "release_hold", "pause_transfer", "resume_transfer", "cancel", "retry_now", "set_priority", "move_before", "move_after", "add_download"}


class Spy:
    """Delegates to the real service and records command calls (name, args)."""

    def __init__(self, real):
        self._real, self.calls = real, []

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if name in COMMANDS and callable(attr):
            def recorded(*args, **kwargs):
                self.calls.append((name, args))
                return attr(*args, **kwargs)
            return recorded
        return attr

    def commands(self, name):
        return [a for n, a in self.calls if n == name]


def wait(qapp, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return predicate()


class Rig:
    def __init__(self, qapp, http, tmp_path, capacity=1):
        self.qapp, self.http, self.tmp = qapp, http, tmp_path
        (tmp_path / "dl").mkdir(parents=True, exist_ok=True)
        self.real = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", max_active_transfers=capacity))
        self.real.start()
        self.manager = Spy(self.real)
        self.widget = DownloadManagerWidget(self.manager)
        self.widget.show()

    def add(self, key, body=SMALL, **kw):
        self.http.configure_resumable(key, etag='"v1"', body=body, **kw)
        ids = lambda: {i.queue_entry_id for i in self.real.snapshot(include_history=True).items}  # noqa: E731
        before = ids()
        self.widget.submit_download(f"{self.http.base_url}/resumable/{key}", str(self.tmp / "dl"))
        assert wait(self.qapp, lambda: len(ids() - before) == 1)
        return next(iter(ids() - before))

    def item(self, entry_id):
        return self.real.item_snapshot(entry_id)

    def select(self, *ids):
        self.widget._refresh_now()  # the view mirrors the service snapshot; take a fresh one before clicking
        sel = self.widget.table.selectionModel()
        sel.clearSelection()
        for row in range(self.widget.proxy.rowCount()):
            if self.widget.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) in ids:
                sel.select(self.widget.proxy.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.qapp.processEvents()
        assert set(self.widget.selected_queue_entry_ids()) == set(ids), "selection did not take"

    def menu_click(self, entry_ids, *path):
        self.select(*entry_ids)
        menu = self.widget.build_context_menu()
        assert menu is not None
        action = None
        node = menu
        for text in path:
            action = _find_action(node, text)
            assert action is not None and action.isEnabled(), f"{text!r} not available: {[a.text() for a in node.actions()]}"
            node = action.menu() if action.menu() is not None else node
        action.trigger()
        self.qapp.processEvents()

    def order_seen(self, keys):
        log = self.http.request_log()
        firsts = []
        for key in keys:
            path = f"/resumable/{key}"
            firsts.append(log.index(path) if path in log else None)
        return firsts

    def close(self):
        self.widget.shutdown()
        self.real.stop()


def blocker(rig):
    entry = rig.add("blocker", body=BIG, **SLOW)
    assert wait(rig.qapp, lambda: rig.item(entry).task_state == T.TRANSFERRING)
    return entry


def test_priority_and_reorder_change_real_dispatch_order_through_the_visible_menu(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        slot = blocker(rig)
        a, b, c, d = (rig.add(k) for k in ("A", "B", "C", "D"))
        rig.menu_click([c], "Priority", "High")
        rig.menu_click([d], "Priority", "Low")
        assert rig.item(c).priority == QueuePriority.HIGH and rig.item(d).priority == QueuePriority.LOW
        assert [n for n, _ in rig.manager.calls if n == "set_priority"] == ["set_priority", "set_priority"]  # one call per click

        # sort + filter so the row index of B differs from its queue position, then reorder B by identity
        rig.widget.set_status_filter("Waiting")
        rig.widget.proxy.set_sort("Name", descending=True)
        qapp.processEvents()
        rig.menu_click([b], "Move up")
        assert rig.manager.commands("move_before") == [(b, a)]  # exactly the intended entries

        rig.widget.set_status_filter("All")
        rig.menu_click([slot], "Cancel")  # frees the single slot -> the scheduler dispatches by priority then queue order
        assert wait(qapp, lambda: all(x is not None for x in rig.order_seen("CBAD")), timeout=20)
        seen = rig.order_seen("CBAD")
        assert seen == sorted(seen), f"dispatch order was not C(high), B, A, D(low): {seen}"
    finally:
        rig.close()


def test_hold_is_not_pause_hold_blocks_dispatch_pause_stops_a_real_transfer(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        slot = blocker(rig)
        held = rig.add("HELD")
        rig.menu_click([held], "Hold")
        assert rig.manager.commands("hold") == [(held,)] and not rig.manager.commands("pause_transfer")
        assert rig.item(held).queue_state == Q.PAUSED and rig.item(held).task_state == T.READY  # queue-level hold, task untouched
        rig.menu_click([slot], "Cancel")
        free = rig.add("FREE")
        assert wait(qapp, lambda: rig.item(free) is None or rig.item(free).task_state == T.COMPLETED, timeout=20)
        assert "/resumable/HELD" not in rig.http.request_log(), "a held entry was dispatched"
        assert rig.item(held).queue_state == Q.PAUSED
        rig.menu_click([held], "Release")
        assert wait(qapp, lambda: "/resumable/HELD" in rig.http.request_log(), timeout=20)
    finally:
        rig.close()

    rig = Rig(qapp, http_fixture_server, tmp_path / "second")
    try:
        active = blocker(rig)
        rig.menu_click([active], "Pause")
        assert rig.manager.commands("pause_transfer") == [(active,)] and not rig.manager.commands("hold")
        assert wait(qapp, lambda: rig.item(active).task_state == T.PAUSED)
        frozen = rig.item(active).bytes_downloaded
        time.sleep(0.5)
        qapp.processEvents()
        assert rig.item(active).bytes_downloaded == frozen, "a paused transfer kept downloading"
        rig.menu_click([active], "Resume")
        assert wait(qapp, lambda: rig.item(active).task_state == T.TRANSFERRING)
        assert wait(qapp, lambda: rig.item(active).bytes_downloaded > frozen)
        rig.menu_click([active], "Cancel")
    finally:
        rig.close()


def test_cancel_stops_the_transfer_keeps_a_cancelled_history_entry_and_deletes_nothing_else(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        active = blocker(rig)
        other = rig.add("OTHER")
        rig.menu_click([active], "Cancel")
        assert rig.manager.commands("cancel") == [(active,)]
        assert wait(qapp, lambda: not any(i.queue_entry_id == active for i in rig.real.snapshot().items))
        history = {i.queue_entry_id: i for i in rig.real.snapshot(include_history=True).items}
        assert history[active].task_state == T.CANCELLED  # a real terminal state, not a silent delete
        assert wait(qapp, lambda: "/resumable/OTHER" in rig.http.request_log(), timeout=15)  # the queue moved on
        assert other in history or rig.item(other) is not None
    finally:
        rig.close()


def test_bulk_bar_acts_on_every_selected_entry_once_and_selection_survives_updates(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        slot = blocker(rig)
        ids = [rig.add(k) for k in ("M1", "M2", "M3")]
        rig.select(*ids)
        assert rig.widget.bulk_bar.isVisibleTo(rig.widget)
        rig.widget.bulk_buttons[P.RowAction.HOLD].click()
        qapp.processEvents()
        assert sorted(a[0] for a in rig.manager.commands("hold")) == sorted(ids)  # one call per entry
        assert all(rig.item(i).queue_state == Q.PAUSED for i in ids)
        # queue churn while selected: a new high-priority row lands above them; identity must not shift
        extra = rig.add("EXTRA")
        rig.real.set_priority(extra, QueuePriority.HIGH)  # churn from the backend, not from the selected rows
        rig.widget._refresh_now()
        qapp.processEvents()
        assert set(rig.widget.selected_queue_entry_ids()) == set(ids)
        rig.widget.bulk_buttons[P.RowAction.RELEASE].click()
        qapp.processEvents()
        assert all(rig.item(i).queue_state == Q.QUEUED for i in ids)
        rig.select(*ids)
        rig.widget.bulk_buttons[P.RowAction.CANCEL].click()
        assert wait(qapp, lambda: all(not any(x.queue_entry_id == i for x in rig.real.snapshot().items) for i in ids))
        history = {i.queue_entry_id: i for i in rig.real.snapshot(include_history=True).items}
        assert all(history[i].task_state == T.CANCELLED for i in ids)
        rig.menu_click([slot], "Cancel")
    finally:
        rig.close()


def test_stale_selection_never_redirects_a_command(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        slot = blocker(rig)
        first, second = rig.add("S1"), rig.add("S2")
        rig.select(second)
        menu = rig.widget.build_context_menu()  # built while `second` is at visual row N
        hold = _find_action(menu, "Hold")
        # the queue reorders underneath the open menu: `second` moves above `first`, changing every row index
        rig.real.move_before(second, first)
        qapp.processEvents()
        rig.widget._refresh_now()
        hold.trigger()
        qapp.processEvents()
        assert rig.manager.commands("hold") == [(second,)], rig.manager.calls
        assert rig.item(second).queue_state == Q.PAUSED and rig.item(first).queue_state == Q.QUEUED
        rig.menu_click([slot], "Cancel")
    finally:
        rig.close()


def test_filter_and_sort_do_not_change_which_download_an_action_hits(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        slot = blocker(rig)
        ids = {k: rig.add(k) for k in ("alpha", "bravo", "charlie", "delta")}
        rig.widget.proxy.set_sort("Name", descending=True)
        rig.widget.set_search_text("bravo") if hasattr(rig.widget, "set_search_text") else rig.widget.proxy.set_search_text("bravo")
        qapp.processEvents()
        assert rig.widget.proxy.rowCount() == 1
        rig.menu_click([ids["bravo"]], "Hold")
        assert rig.manager.commands("hold") == [(ids["bravo"],)]
        assert [rig.item(i).queue_state for i in ids.values()].count(Q.PAUSED) == 1
        rig.menu_click([slot], "Cancel") if False else None
        rig.widget.proxy.set_search_text("")
        rig.menu_click([slot], "Cancel")
    finally:
        rig.close()


def test_retry_now_reaches_the_real_backend_from_the_visible_action(qapp, http_fixture_server, tmp_path):
    """Retry-wait needs a retryable failure classification; the production mapper is non-retryable by design, so the
    existing real E2E (tests/test_gui_e2e.py::test_gui_retry_now_via_button) injects one -- this test reuses that
    exact configuration through the same visible menu action."""
    import test_gui_e2e as e2e

    assert hasattr(e2e, "test_gui_retry_now_via_button")
    e2e.test_gui_retry_now_via_button(http_fixture_server, tmp_path, qapp)


def test_ordinary_table_and_model_never_expose_completed_local_paths(qapp, http_fixture_server, tmp_path):
    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        done = rig.add("PRIV")
        assert wait(qapp, lambda: any(i.queue_entry_id == done and i.task_state == T.COMPLETED for i in rig.real.snapshot(include_history=True).items), timeout=20)
        rig.widget.publish_state()
        rig.widget._refresh_now()
        qapp.processEvents()
        needle = str(tmp_path)
        model = rig.widget.proxy
        dumps = []
        for row in range(model.rowCount()):
            for col in range(model.columnCount()):
                index = model.index(row, col)
                for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.StatusTipRole, Qt.ItemDataRole.AccessibleTextRole, Qt.ItemDataRole.EditRole):
                    dumps.append(str(index.data(role)))
        item = rig.real.snapshot(include_history=True).items[0]
        dumps.append(repr(item))
        assert needle not in " ".join(dumps), "a local filesystem path leaked into the table/view-model"
        # only the explicit privileged accessor (used by Open / Open folder / Details) returns it
        result = rig.real.completed_file(done)
        assert result.info is not None and needle in str(result.info.local_path)
    finally:
        rig.close()


def test_details_show_real_priority_position_and_held_for_each_state(qapp, http_fixture_server, tmp_path):
    from rychlik.gui.dialogs.details import build_rows

    rig = Rig(qapp, http_fixture_server, tmp_path)
    try:
        active = blocker(rig)
        queued, held = rig.add("Q1"), rig.add("Q2")
        rig.menu_click([held], "Hold")
        rig.menu_click([queued], "Priority", "High")
        rows = dict(build_rows(rig.real, rig.item(active)))
        assert rows["Status"] == "Downloading" and rows["Priority"] == "Normal" and rows["Held"] == "No"
        rows = dict(build_rows(rig.real, rig.item(queued)))
        assert rows["Priority"] == "High" and rows["Queue position"].startswith("1 of 1")
        rows = dict(build_rows(rig.real, rig.item(held)))
        assert rows["Held"] == "Yes" and "Held" in rows["Status"]
        rig.menu_click([active], "Pause")
        assert wait(qapp, lambda: rig.item(active).task_state == T.PAUSED)
        assert dict(build_rows(rig.real, rig.item(active)))["Status"].startswith("Paused")
        rig.menu_click([active], "Cancel")
        # a completed and a failed download
        done = rig.add("DONE")
        assert wait(qapp, lambda: any(i.queue_entry_id == done and i.task_state == T.COMPLETED for i in rig.real.snapshot(include_history=True).items), timeout=20)
        completed = next(i for i in rig.real.snapshot(include_history=True).items if i.queue_entry_id == done)
        rows = dict(build_rows(rig.real, completed))
        assert rows["Status"] == "Completed" and str(tmp_path) in rows["Destination"]
        rig.widget.submit_download(f"{http_fixture_server.base_url}/notfound", str(tmp_path / "dl"))
        assert wait(qapp, lambda: any(i.task_state == T.FAILED for i in rig.real.snapshot(include_history=True).items), timeout=20)
        failed = next(i for i in rig.real.snapshot(include_history=True).items if i.task_state == T.FAILED)
        rows = dict(build_rows(rig.real, failed))
        assert rows["Status"] == "Failed" and rows.get("Failure")
    finally:
        rig.close()
