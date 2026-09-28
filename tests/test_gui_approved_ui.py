"""Final GUI/UX implementation: the approved UI through its real paths (menus, bulk bar, hover
actions, identity under filter/sort/refresh, keyboard, responsive tiers, dialogs, themes, scale)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from rychlik.core.download_manager_service import CompletedFileInfo, CompletedFileResult, CompletedFileStatus
from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadManagerSnapshot, DownloadViewSnapshot
from rychlik.gui import presentation as P
from rychlik.gui.dialogs import AddDownloadDialog, DetailsDialog
from rychlik.gui.dialogs.details import build_rows
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.gui.main_window import MainWindow
from test_download_manager_widget import _FakeManager

T, Q, PR = DownloadTaskState, QueueEntryState, QueuePriority
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def it(eid="q1", name="video.mp4", task=T.READY, queue=Q.QUEUED, prio=PR.NORMAL, **kw) -> DownloadViewSnapshot:
    base = dict(
        task_id="t" + eid, queue_entry_id=eid, display_name=name, task_state=task, queue_state=queue, priority=prio, position=0,
        attempt_count=1, bytes_downloaded=0, total_bytes=None, progress_fraction=None, speed_bps=None, eta_seconds=None,
        source_host="host.example", added_at=NOW,
    )
    base.update(kw)
    return DownloadViewSnapshot(**base)


def select(widget, *ids):
    sel = widget.table.selectionModel()
    sel.clearSelection()
    for row in range(widget.proxy.rowCount()):
        if widget.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) in ids:
            sel.select(widget.proxy.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def menu_texts(menu: QMenu) -> list[str]:
    out = []
    for a in menu.actions():
        out.append("---" if a.isSeparator() else a.text().strip())
    return out


def find(menu: QMenu, text: str):
    for a in menu.actions():
        if a.text().strip().lstrip("✓").strip() == text:
            return a
        if a.menu() is not None:
            sub = find(a.menu(), text)
            if sub is not None:
                return sub
    return None


def widget_with(items, **kw):
    manager = _FakeManager(items=items)
    return manager, DownloadManagerWidget(manager, **kw)


# --- state-aware context menu --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected_top",
    [
        (dict(task=T.TRANSFERRING), ["Pause", "Hold"]),
        (dict(task=T.RETRY_WAIT), ["Retry now", "Hold"]),
        (dict(task=T.PAUSED), ["Resume", "Hold"]),
        (dict(task=T.READY), ["Hold"]),
        (dict(task=T.READY, queue=Q.PAUSED), ["Release"]),
    ],
)
def test_live_item_menu_offers_exactly_the_valid_actions_in_order(qapp, kwargs, expected_top):
    _, w = widget_with([it(**kwargs)])
    select(w, "q1")
    texts = menu_texts(w.build_context_menu())
    assert texts[: len(expected_top)] == expected_top
    assert {"Priority", "Move up", "Move down", "Cancel"} <= set(texts) and "Details" in texts
    for absent in {"Pause", "Resume", "Retry now", "Hold", "Release"} - set(expected_top):
        assert absent not in texts, absent
    assert "Share…" not in texts and "Open" not in texts  # completed-only


def test_completed_menu_has_open_folder_share_and_details_but_no_queue_actions(qapp):
    _, w = widget_with([it(task=T.COMPLETED, queue=Q.REMOVED)])
    select(w, "q1")
    texts = menu_texts(w.build_context_menu())
    assert texts == ["Open", "Open folder", "Details", "---", "Share…"]  # no leading or trailing separator
    assert not {"Pause", "Hold", "Cancel", "Priority", "Retry now"} & set(texts)


@pytest.mark.parametrize("task", [T.FAILED, T.CANCELLED])
def test_failed_and_cancelled_have_no_retry_or_queue_actions(qapp, task):
    _, w = widget_with([it(task=task, queue=Q.REMOVED)])
    select(w, "q1")
    texts = menu_texts(w.build_context_menu())
    assert texts == ["Details"]  # the backend has no retry/queue/share action for these; nothing else is offered


def test_hold_and_pause_are_different_actions_with_a_hold_tooltip(qapp):
    _, w = widget_with([it(task=T.TRANSFERRING)])
    select(w, "q1")
    menu = w.build_context_menu()
    hold, pause = find(menu, "Hold"), find(menu, "Pause")
    assert hold is not pause and "not stopped" in hold.toolTip() and "Pause" in hold.toolTip()


def test_priority_submenu_sets_priority_and_marks_the_current_one(qapp):
    manager, w = widget_with([it(prio=PR.HIGH)])
    select(w, "q1")
    menu = w.build_context_menu()
    assert find(menu, "High").text().lstrip().startswith("✓")
    find(menu, "Low").trigger()
    assert ("set_priority", "q1", PR.LOW) in manager.calls


def test_move_up_down_disable_at_band_edges_and_map_to_service(qapp):
    manager, w = widget_with([it("a"), it("b"), it("c", prio=PR.HIGH)])
    select(w, "a")
    menu = w.build_context_menu()
    assert not find(menu, "Move up").isEnabled() and "first" in find(menu, "Move up").toolTip()
    find(menu, "Move down").trigger()
    assert ("move_after", "a", "b") in manager.calls  # neighbor inside the NORMAL band only, never across bands
    select(w, "c")
    menu = w.build_context_menu()
    assert not find(menu, "Move up").isEnabled() and not find(menu, "Move down").isEnabled()  # alone in HIGH


def test_service_actions_are_disabled_when_the_service_is_not_running(qapp):
    from rychlik.core.download_manager_service import ManagerState

    manager, w = widget_with([it(task=T.TRANSFERRING)])
    select(w, "q1")
    manager.state = ManagerState.FAULTED
    menu = w.build_context_menu()
    assert not find(menu, "Pause").isEnabled() and not find(menu, "Cancel").isEnabled()
    assert find(menu, "Details").isEnabled()


# --- one service call per affected occurrence; no optimistic state ---------------------------------------------------


def test_menu_action_calls_the_service_exactly_once_and_keeps_the_snapshot_state(qapp):
    manager, w = widget_with([it(task=T.TRANSFERRING)])
    select(w, "q1")
    find(w.build_context_menu(), "Pause").trigger()
    assert [c for c in manager.calls if c[0] == "pause_transfer"] == [("pause_transfer", "q1")]
    assert w.model.item_for_id("q1").task_state == T.TRANSFERRING  # never fabricated locally


# --- bulk bar ---------------------------------------------------------------------------------------------------------


def test_bulk_bar_appears_only_for_multiple_selection_and_acts_on_valid_items_only(qapp):
    manager, w = widget_with([it("a", task=T.TRANSFERRING), it("b", task=T.PAUSED), it("c", queue=Q.PAUSED), it("d", task=T.COMPLETED, queue=Q.REMOVED)])
    select(w, "a")
    w._on_selection_changed()
    assert w.bulk_bar.isHidden()  # a single selection uses row actions, not the bulk bar
    select(w, "a", "b", "c")
    w._on_selection_changed()
    assert not w.bulk_bar.isHidden() and w.bulk_count_label.text() == "3 selected"
    assert all(b.isEnabled() for b in w.bulk_buttons.values())
    w.bulk_buttons[P.RowAction.PAUSE].click()
    assert [c for c in manager.calls if c[0] == "pause_transfer"] == [("pause_transfer", "a")]  # only the transferring one
    w.bulk_buttons[P.RowAction.RESUME].click()
    assert ("resume_transfer", "b") in manager.calls and ("resume_transfer", "a") not in manager.calls
    w.bulk_buttons[P.RowAction.RELEASE].click()
    assert [c for c in manager.calls if c[0] == "release_hold"] == [("release_hold", "c")]
    w.bulk_buttons[P.RowAction.CANCEL].click()
    assert {c[1] for c in manager.calls if c[0] == "cancel"} == {"a", "b", "c"}
    select(w, "d", "a")
    w._on_selection_changed()
    assert not w.bulk_buttons[P.RowAction.RESUME].isEnabled()  # nothing selected is paused-resumable anymore


def test_bulk_selection_of_finished_downloads_disables_queue_actions(qapp):
    _, w = widget_with([it("a", task=T.COMPLETED, queue=Q.REMOVED), it("b", task=T.FAILED, queue=Q.REMOVED)])
    select(w, "a", "b")
    w._on_selection_changed()
    assert not any(b.isEnabled() for b in w.bulk_buttons.values()) and not w.bulk_priority_button.isEnabled()


def test_bulk_bar_has_no_reorder_controls(qapp):
    _, w = widget_with([it("a"), it("b")])
    labels = {b.text() for b in w.bulk_bar.findChildren(type(w.bulk_clear_button))}
    assert not {"Move up", "Move down"} & labels


# --- identity: stale, filtered, multi -----------------------------------------------------------------------------------


def test_stale_selection_never_redirects_a_command_to_another_download(qapp):
    manager, w = widget_with([it("a"), it("b")])
    select(w, "a")
    manager.set_items([it("b"), it("x")])  # "a" left; "x" now sits where "a" was
    w._refresh_now()
    assert w.selected_queue_entry_ids() == []
    w.run_action(P.RowAction.HOLD, ["a"])  # stale id from an old click
    assert [c for c in manager.calls if c[0] == "hold"] == []  # not applied to "a" (gone) nor to "x"/"b"


def test_action_after_filter_and_sort_targets_the_exact_selected_download(qapp):
    manager, w = widget_with([
        it("a", "zeta.mp4", T.TRANSFERRING), it("b", "alpha.mp4", T.PAUSED), it("c", "mid.mp4", T.TRANSFERRING), it("d", "song.flac", T.TRANSFERRING),
    ])
    w.set_status_filter("Downloading")
    w.proxy.set_sort("Name")
    order = [w.proxy.index(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(w.proxy.rowCount())]
    assert order == ["c", "d", "a"]  # alphabetical inside the filter; "b" hidden
    select(w, "d")
    find(w.build_context_menu(), "Pause").trigger()
    assert [c for c in manager.calls if c[0] == "pause_transfer"] == [("pause_transfer", "d")]


def test_multi_selection_survives_updates_sorting_and_reordering_by_identity(qapp):
    manager, w = widget_with([it("a"), it("b"), it("c"), it("d")])
    select(w, "b", "d")
    manager.set_items([it("d", task=T.TRANSFERRING), it("c"), it("b"), it("a"), it("e")])  # reorder + state change + insert
    w._refresh_now()
    assert set(w.selected_queue_entry_ids()) == {"b", "d"}
    w.proxy.set_sort("Name")
    assert set(w.selected_queue_entry_ids()) == {"b", "d"}
    manager.set_items([it("b"), it("a")])  # "d" disappeared
    w._refresh_now()
    assert w.selected_queue_entry_ids() == ["b"]  # only the still-existing selected download remains selected


# --- hover actions (painted delegate, real mouse events) ----------------------------------------------------------------------


def _click_action_button(qapp, w, row, which):
    w.resize(1280, 640)
    w.show()
    w.apply_width(1400)
    qapp.processEvents()
    view = w.table
    index = w.proxy.index(row, P.COL_ACTIONS)
    cell = view.visualRect(index)
    QTest.mouseMove(view.viewport(), cell.center())
    qapp.processEvents()
    item = w.model.item_for_id(w.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole))
    actions = P.hover_actions(item)
    rects = view.delegate.action_buttons(cell, len(actions))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rects[which].center())
    qapp.processEvents()


def test_hover_pause_and_cancel_buttons_call_the_service_for_that_row(qapp):
    manager, w = widget_with([it("a", task=T.TRANSFERRING), it("b", task=T.TRANSFERRING)])
    _click_action_button(qapp, w, 1, 0)  # pause on the second row
    assert [c for c in manager.calls if c[0] == "pause_transfer"] == [("pause_transfer", "b")]
    _click_action_button(qapp, w, 0, 1)  # cancel on the first
    assert ("cancel", "a") in manager.calls


def test_inline_retry_now_button_calls_retry_for_that_row(qapp):
    manager, w = widget_with([it("a", task=T.RETRY_WAIT, retry_in_seconds=12.0)])
    w.resize(1280, 640)
    w.show()
    w.apply_width(1400)
    qapp.processEvents()
    cell = w.table.visualRect(w.proxy.index(0, P.COL_SPEED))
    QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, w.table.delegate.retry_button_rect(cell).center())
    assert ("retry_now", "a") in manager.calls


def test_checkbox_column_toggles_selection(qapp):
    _, w = widget_with([it("a"), it("b")])
    w.resize(1280, 640)
    w.show()
    w.apply_width(1400)
    qapp.processEvents()
    for row in (0, 1):
        cell = w.table.visualRect(w.proxy.index(row, P.COL_SELECT))
        QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, w.table.delegate.checkbox_rect(cell).center())
    assert set(w.selected_queue_entry_ids()) == {"a", "b"}
    cell = w.table.visualRect(w.proxy.index(0, P.COL_SELECT))
    QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, w.table.delegate.checkbox_rect(cell).center())
    assert w.selected_queue_entry_ids() == ["b"]


# --- keyboard ---------------------------------------------------------------------------------------------------------------------


def test_keyboard_shortcuts_select_all_space_enter_escape(qapp):
    opened = []
    manager, w = widget_with([it("a", task=T.TRANSFERRING), it("b")], details_launcher=lambda item, parent: opened.append(item.queue_entry_id))
    w.show()
    w.table.setFocus()
    QTest.keyClick(w.table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert set(w.selected_queue_entry_ids()) == {"a", "b"}
    QTest.keyClick(w.table, Qt.Key.Key_Escape)
    assert w.selected_queue_entry_ids() == []
    select(w, "a")
    QTest.keyClick(w.table, Qt.Key.Key_Space)
    assert ("pause_transfer", "a") in manager.calls  # Space = pause when transferring (safe toggle)
    QTest.keyClick(w.table, Qt.Key.Key_Return)
    assert opened == ["a"]
    select(w, "b")
    manager.calls.clear()
    QTest.keyClick(w.table, Qt.Key.Key_Space)
    assert manager.calls == []  # Space never guesses on a waiting item
    QTest.keyClick(w.table, Qt.Key.Key_Delete)
    assert manager.calls == []  # no key deletes anything


def test_double_click_opens_details(qapp):
    opened = []
    _, w = widget_with([it("a")], details_launcher=lambda item, parent: opened.append(item.queue_entry_id))
    w.resize(1280, 640)
    w.show()
    w.apply_width(1400)
    qapp.processEvents()
    pos = w.table.visualRect(w.proxy.index(0, P.COL_NAME)).center()
    QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseDClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert opened == ["a"]


# --- filters, empty states, sidebar ------------------------------------------------------------------------------------------------


def test_empty_states_distinguish_no_downloads_from_no_matches(qapp):
    manager, w = widget_with([])
    assert w.empty_title.text() == "No downloads yet" and w._stack.currentWidget() is w.empty_state and not w.empty_add_button.isHidden()
    manager.set_items([it("a", "holiday.mp4")])
    w._refresh_now()
    w.search_input.setText("zzz")
    assert w._stack.currentWidget() is w.empty_state and w.empty_title.text() == "No downloads match"
    assert w.empty_add_button.isHidden()
    w.search_input.setText("")
    assert w._stack.currentWidget() is w.table


def test_search_filters_by_name_and_source_host(qapp):
    _, w = widget_with([it("a", "holiday.mp4", source_host="cdn.example.net"), it("b", "song.flac", source_host="audio.example")])
    w.search_input.setText("cdn")
    assert w.proxy.rowCount() == 1 and w.count_label.text() == "1 task"
    w.search_input.setText("")
    assert w.count_label.text() == "2 tasks"


def test_window_sidebar_filters_categories_counts_and_title(qapp):
    manager = _FakeManager(items=[it("a", "a.mp4", T.TRANSFERRING), it("b", "b.flac", T.COMPLETED, Q.REMOVED), it("c", "c.mp4", T.FAILED, Q.REMOVED)])
    w = DownloadManagerWidget(manager)
    win = MainWindow(manager, w)
    win.show()
    qapp.processEvents()
    assert win._sidebar_items["status:All"].count_label.text() == "3"
    assert win._sidebar_items["status:Downloading"].count_label.text() == "1"
    assert win._sidebar_items["category:Video"].count_label.text() == "2"
    win._sidebar_items["status:Completed"].click()
    assert w.title_label.text() == "Completed" and w.proxy.rowCount() == 1
    win._sidebar_items["category:Video"].click()
    assert w.title_label.text() == "Video" and w.proxy.rowCount() == 2
    assert win.active_sidebar_key == "category:Video"
    win._sidebar_items["status:All"].click()
    assert w.proxy.rowCount() == 3 and w.title_label.text() == "All Downloads"


# --- window shell: responsive tiers, status bar, menus ---------------------------------------------------------------------------------


def _window(items=()):
    manager = _FakeManager(items=list(items))
    w = DownloadManagerWidget(manager)
    return manager, w, MainWindow(manager, w)


@pytest.mark.parametrize(
    "width,sidebar,hidden,shown",
    [(2560, 190, [], [P.COL_CATEGORY, P.COL_TYPE, P.COL_ADDED]), (1366, 190, [P.COL_CATEGORY, P.COL_ADDED], [P.COL_TYPE, P.COL_SIZE, P.COL_ETA]),
     (1100, 190, [P.COL_TYPE, P.COL_ETA], [P.COL_SIZE]), (1000, 48, [P.COL_TYPE, P.COL_ETA, P.COL_SIZE], [P.COL_NAME, P.COL_PROGRESS, P.COL_STATUS])],
)
def test_responsive_tiers_drive_real_columns_and_sidebar(qapp, width, sidebar, hidden, shown):
    _, w, win = _window([it("a", task=T.TRANSFERRING, speed_bps=1e6, eta_seconds=3.0)])
    win.resize(width, 900)
    win.show()
    qapp.processEvents()
    assert win.sidebar.width() == sidebar
    assert all(w.table.isColumnHidden(c) for c in hidden) and not any(w.table.isColumnHidden(c) for c in shown)
    assert w.model.merge_speed_eta == (width < 1280)


def test_window_uses_approved_dimensions(qapp):
    from rychlik.gui.theme.tokens import metrics

    _, w, win = _window([it("a")])
    win.resize(1366, 768)
    win.show()
    qapp.processEvents()
    m = metrics()
    assert win.toolbar.height() == m.toolbar_height == 54 and win.status_bar.height() == 28 and win.sidebar.width() == 190
    assert w.table.verticalHeader().defaultSectionSize() == 42 and w.table.rowHeight(0) == 42
    assert w.table.horizontalHeader().height() == m.table_header_height
    assert "tool:devices" not in win._sidebar_items  # no Send/Devices page without Device Mode
    assert not any("Send" in i.label_text for i in win._sidebar_items.values())  # Send is never a navigation destination


def test_status_bar_reflects_the_summary_signal(qapp):
    _, w, win = _window([it("a", task=T.TRANSFERRING), it("b")])
    w.summary_changed.emit(2, 3, "43.6 MB/s")
    assert (win.status_active.text(), win.status_waiting.text(), win.status_speed.text()) == ("2 active", "3 waiting", "↓ 43.6 MB/s")
    w.summary_changed.emit(0, 0, "")
    assert win.status_active.text() == "Idle" and win.status_waiting.text() == "" and win.status_speed.text() == ""


def test_tasks_menu_enablement_follows_selection(qapp):
    _, w, win = _window([it("a", task=T.TRANSFERRING), it("b", task=T.COMPLETED, queue=Q.REMOVED)])
    select(w, "a")
    win._update_task_menu()
    assert win._task_actions[P.RowAction.PAUSE].isEnabled() and not win._task_actions[P.RowAction.RESUME].isEnabled()
    assert win.action_details.isEnabled() and not win.action_share.isEnabled()
    select(w, "b")
    win._update_task_menu()
    assert not win._task_actions[P.RowAction.PAUSE].isEnabled() and win.action_share.isEnabled()


def test_theme_switch_repaints_both_themes_differently(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    from rychlik.gui.theme.manager import ThemeManager

    themes = ThemeManager(qapp, QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat))
    manager = _FakeManager(items=[it("a", task=T.TRANSFERRING, progress_fraction=0.5, total_bytes=10**8, speed_bps=1e6)])
    w = DownloadManagerWidget(manager, theme=themes.theme)
    win = MainWindow(manager, w, theme_manager=themes)
    win.resize(1280, 700)
    win.show()
    themes.set_theme("dark")
    qapp.processEvents()
    dark = win.grab().toImage()
    themes.set_theme("light")
    qapp.processEvents()
    light = win.grab().toImage()
    assert dark.pixelColor(400, 300) != light.pixelColor(400, 300)
    assert w.table.delegate._p.name == "light" and win._theme == "light"  # noqa: SLF001
    themes.set_theme("dark")


# --- dialogs --------------------------------------------------------------------------------------------------------------------------------


def test_add_dialog_enables_download_only_with_a_url_and_closes_after_acceptance(qapp, tmp_path):
    manager, w = widget_with([])
    w._destination_dir = tmp_path
    dialog = AddDownloadDialog(w)
    assert not dialog.download_button.isEnabled()
    dialog.url_input.setText("https://example.test/file.mp4")
    assert dialog.download_button.isEnabled()
    dialog.download_button.click()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert [c[0] for c in manager.calls] == ["add_download"] and manager.calls[0][1].destination_dir == tmp_path


def test_add_dialog_stays_open_when_the_service_refuses(qapp, tmp_path, monkeypatch):
    manager, w = widget_with([])
    w._destination_dir = tmp_path
    manager.add_download = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    warnings = []
    monkeypatch.setattr("rychlik.gui.download_manager_widget.QMessageBox.warning", lambda *a, **k: warnings.append(a))
    dialog = AddDownloadDialog(w)
    dialog.url_input.setText("https://example.test/file.mp4")
    dialog.download_button.click()
    assert dialog.result() != dialog.DialogCode.Accepted and len(warnings) == 1


def test_details_rows_show_only_real_snapshot_fields(qapp):
    manager, w = widget_with([it("a", "video.mp4", T.TRANSFERRING, prio=PR.HIGH, total_bytes=84_200_000, bytes_downloaded=53_000_000, progress_fraction=0.63,
                                 speed_bps=12_400_000, eta_seconds=3.0, source_host="x.com"), it("b")])
    rows = dict(build_rows(manager, manager.item_snapshot("a")))
    assert rows["Name"] == "video.mp4" and rows["Status"] == "Downloading" and rows["Source"] == "x.com"
    assert rows["Size"] == "84.2 MB" and rows["Downloaded"] == "53.0 MB" and rows["Speed"] == "12.4 MB/s" and rows["Remaining"] == "3 s"
    assert rows["Priority"] == "High" and rows["Queue position"] == "1 of 1 in High" and rows["Held"] == "No"
    for invented in ("Effective URL", "Resume support", "Backend", "Resolver", "URL"):
        assert invented not in rows  # the service does not expose these, so they are never shown


def test_details_for_held_failed_and_completed(qapp, tmp_path):
    path = tmp_path / "done.mp4"
    path.write_bytes(b"x")
    result = CompletedFileResult(CompletedFileStatus.AVAILABLE, info=CompletedFileInfo(
        queue_entry_id="c", task_id="tc", local_path=path, display_name="done.mp4", size_bytes=1, completed_at_utc=NOW))
    manager = _FakeManager(items=[it("h", queue=Q.PAUSED), it("f", task=T.FAILED, queue=Q.REMOVED, last_failure_code="HTTP_404"),
                                  it("c", "done.mp4", T.COMPLETED, Q.REMOVED)], completed_file_result=result)
    held = dict(build_rows(manager, manager.item_snapshot("h")))
    assert held["Status"] == "Waiting · Held" and held["Held"] == "Yes"
    failed = dict(build_rows(manager, manager.item_snapshot("f")))
    assert failed["Failure"] == "HTTP_404" and "Priority" not in failed  # finished items have no queue attributes
    done = dict(build_rows(manager, manager.item_snapshot("c")))
    assert done["Destination"] == str(path)  # only via the privileged completed_file accessor


def test_details_dialog_renders_and_refreshes_from_the_service(qapp):
    manager, w = widget_with([it("a", task=T.TRANSFERRING, progress_fraction=0.1, total_bytes=100, speed_bps=10.0)])
    dialog = DetailsDialog(manager, manager.item_snapshot("a"), w)
    assert dialog.grid.count() >= 8
    manager.set_items([it("a", task=T.TRANSFERRING, progress_fraction=0.9, total_bytes=100, bytes_downloaded=90, speed_bps=10.0)])
    dialog.refresh()
    texts = [dialog.grid.itemAt(i).widget().text() for i in range(dialog.grid.count())]
    assert "90 B" in texts
    dialog.done(0)


def test_queue_view_shows_bands_and_moves_within_a_band_only(qapp):
    manager, w, win = _window([it("h", prio=PR.HIGH), it("n1"), it("n2"), it("l", prio=PR.LOW), it("done", task=T.COMPLETED, queue=Q.REMOVED)])
    qv = win.queue_view
    band_rows = qv.model.band_rows()
    assert len(band_rows) == 3 and qv.model.rowCount() == 3 + 4  # 3 headers + 4 live items; finished ones are not queued
    n2_row = next(r for r in range(qv.model.rowCount()) if qv.model.index(r, 0).data(Qt.ItemDataRole.UserRole) == "n2")
    qv.table.selectRow(n2_row)
    assert qv.up_button.isEnabled() and not qv.down_button.isEnabled()
    qv.up_button.click()
    assert ("move_before", "n2", "n1") in manager.calls
    h_row = next(r for r in range(qv.model.rowCount()) if qv.model.index(r, 0).data(Qt.ItemDataRole.UserRole) == "h")
    qv.table.selectRow(h_row)
    assert not qv.up_button.isEnabled() and not qv.down_button.isEnabled()  # alone in HIGH: never crosses into another band
    qv.hold_button.click()
    assert ("hold", "h") in manager.calls


# --- long text & scale ---------------------------------------------------------------------------------------------------------------------------


def test_very_long_names_hosts_and_labels_render_without_resizing_the_row(qapp):
    long_name = "a-really-long-file-name-" * 12 + ".mp4"
    _, w, win = _window([it("a", long_name, T.TRANSFERRING, source_host="very-long-hostname." * 8 + "example.org", total_bytes=10**13, progress_fraction=0.999,
                            speed_bps=9.99e11, eta_seconds=9e5)])
    win.resize(1100, 600)
    win.show()
    qapp.processEvents()
    assert w.table.rowHeight(0) == 42
    image = w.table.viewport().grab().toImage()
    assert not image.isNull()
    assert w.model.index(0, P.COL_NAME).data() == long_name  # elision is visual only, the value is untouched


def test_widget_refresh_stays_responsive_with_500_rows(qapp):
    items = [it(f"e{i}", f"file-{i}.mp4", T.TRANSFERRING if i % 5 == 0 else T.COMPLETED, Q.QUEUED if i % 5 == 0 else Q.REMOVED,
                total_bytes=10**6, progress_fraction=0.5, speed_bps=1e5) for i in range(500)]
    manager, w, win = _window(items)
    win.resize(1366, 768)
    win.show()
    start = time.perf_counter()
    for i in range(5):
        manager.set_items([it(x.queue_entry_id, x.display_name, x.task_state, x.queue_state, total_bytes=10**6, progress_fraction=0.5 + i / 20, speed_bps=1e5) for x in items])
        w._refresh_now()
        qapp.processEvents()
    assert (time.perf_counter() - start) / 5 < 0.5
