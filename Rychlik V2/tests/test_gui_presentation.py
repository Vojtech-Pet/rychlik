"""Final GUI/UX implementation: pure presentation rules (statuses, actions, bands, columns)."""

from datetime import datetime, timedelta, timezone

import pytest

from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui import presentation as P

T, Q = DownloadTaskState, QueueEntryState


def item(task=T.READY, queue=Q.QUEUED, priority=QueuePriority.NORMAL, name="a.mp4", eid="e1", **kw):
    base = dict(
        task_id="t-" + eid, queue_entry_id=eid, display_name=name, task_state=task, queue_state=queue, priority=priority,
        position=0, attempt_count=1, bytes_downloaded=0, total_bytes=None, progress_fraction=None, speed_bps=None, eta_seconds=None,
    )
    base.update(kw)
    return DownloadViewSnapshot(**base)


@pytest.mark.parametrize(
    "task,key,tone",
    [(T.TRANSFERRING, "downloading", "info"), (T.PAUSED, "paused", "warning"), (T.COMPLETED, "completed", "success"),
     (T.FAILED, "failed", "error"), (T.CANCELLED, "cancelled", "neutral"), (T.READY, "waiting", "neutral"),
     (T.CREATED, "waiting", "neutral"), (T.RETRY_WAIT, "retrying", "info"), (T.VERIFYING, "verifying", "info")],
)
def test_status_key_and_tone(task, key, tone):
    info = P.status_info(item(task=task, queue=Q.REMOVED if task in (T.COMPLETED, T.FAILED, T.CANCELLED) else Q.QUEUED))
    assert (info.key, info.tone) == (key, tone) and info.glyph and info.label


def test_hold_is_a_separate_attribute_and_never_pause():
    held = P.status_info(item(task=T.READY, queue=Q.PAUSED))
    assert held.held and held.key == "waiting"  # still "Waiting", plus the Held attribute
    transferring_held = P.status_info(item(task=T.TRANSFERRING, queue=Q.PAUSED))
    assert transferring_held.held and transferring_held.key == "downloading"  # hold never stops a running transfer
    assert not P.status_info(item(task=T.PAUSED)).held  # a paused transfer is not held
    assert not P.status_info(item(task=T.COMPLETED, queue=Q.REMOVED)).held


def test_retry_label_uses_the_real_countdown_only():
    assert P.status_info(item(task=T.RETRY_WAIT, retry_in_seconds=11.6)).label == "Retrying in 12 s"
    assert P.status_info(item(task=T.RETRY_WAIT)).label == "Retrying"  # unknown -> no invented number


def test_action_availability_matrix_matches_backend_contract():
    A = P.RowAction
    vis = lambda i, **kw: {a for a, s in P.available_actions(i, **kw).items() if s.visible}
    assert vis(item(task=T.TRANSFERRING)) >= {A.PAUSE, A.HOLD, A.CANCEL, A.MOVE_UP, A.MOVE_DOWN}
    assert A.RESUME not in vis(item(task=T.TRANSFERRING)) and A.RETRY_NOW not in vis(item(task=T.TRANSFERRING))
    assert A.RESUME in vis(item(task=T.PAUSED)) and A.PAUSE not in vis(item(task=T.PAUSED))
    assert A.RETRY_NOW in vis(item(task=T.RETRY_WAIT)) and A.RETRY_NOW not in vis(item(task=T.READY))
    assert A.RELEASE in vis(item(queue=Q.PAUSED)) and A.HOLD not in vis(item(queue=Q.PAUSED))
    assert A.HOLD in vis(item()) and A.RELEASE not in vis(item())
    done = item(task=T.COMPLETED, queue=Q.REMOVED)
    assert vis(done) == {A.DETAILS, A.OPEN, A.OPEN_FOLDER, A.SHARE}
    for terminal in (T.FAILED, T.CANCELLED):  # no retry/queue actions for finished-unsuccessfully items
        assert vis(item(task=terminal, queue=Q.REMOVED)) == {A.DETAILS}


def test_move_up_down_disable_at_band_edges_with_a_reason():
    first = P.available_actions(item(), band_position=(1, 3))
    assert not first[P.RowAction.MOVE_UP].enabled and "first" in first[P.RowAction.MOVE_UP].reason
    assert first[P.RowAction.MOVE_DOWN].enabled
    last = P.available_actions(item(), band_position=(3, 3))
    assert last[P.RowAction.MOVE_UP].enabled and not last[P.RowAction.MOVE_DOWN].enabled


def test_band_positions_and_neighbors_only_count_live_items_in_the_same_band():
    items = [
        item(eid="h1", priority=QueuePriority.HIGH), item(eid="n1"), item(eid="n2"),
        item(eid="done", task=T.COMPLETED, queue=Q.REMOVED), item(eid="l1", priority=QueuePriority.LOW),
    ]
    pos = P.band_positions(items)
    assert pos == {"h1": (1, 1), "n1": (1, 2), "n2": (2, 2), "l1": (1, 1)}
    assert P.band_neighbor(items, "n1", +1) == "n2" and P.band_neighbor(items, "n1", -1) is None
    assert P.band_neighbor(items, "h1", +1) is None  # never crosses a priority band
    assert P.band_neighbor(items, "done", +1) is None


def test_hover_actions_are_at_most_two_and_state_valid():
    assert P.hover_actions(item(task=T.TRANSFERRING)) == (P.RowAction.PAUSE, P.RowAction.CANCEL)
    assert P.hover_actions(item(task=T.RETRY_WAIT)) == (P.RowAction.RETRY_NOW, P.RowAction.CANCEL)
    assert P.hover_actions(item(task=T.PAUSED)) == (P.RowAction.RESUME, P.RowAction.CANCEL)
    assert P.hover_actions(item(queue=Q.PAUSED)) == (P.RowAction.RELEASE, P.RowAction.CANCEL)
    assert P.hover_actions(item()) == (P.RowAction.HOLD, P.RowAction.CANCEL)
    assert P.hover_actions(item(task=T.COMPLETED, queue=Q.REMOVED)) == (P.RowAction.OPEN_FOLDER, P.RowAction.SHARE)
    assert P.hover_actions(item(task=T.FAILED, queue=Q.REMOVED)) == ()


def test_bulk_actions_enable_when_any_selected_item_supports_them():
    sel = [item(task=T.TRANSFERRING, eid="a"), item(task=T.PAUSED, eid="b"), item(queue=Q.PAUSED, eid="c")]
    bulk = P.bulk_actions(sel)
    assert all(bulk.values())
    only_done = P.bulk_actions([item(task=T.COMPLETED, queue=Q.REMOVED)])
    assert not any(only_done.values())


@pytest.mark.parametrize(
    "name,category,label",
    [("holiday.mp4", "Video", "MP4"), ("song.FLAC", "Music", "FLAC"), ("cat.png", "Images", "PNG"), ("a.pdf", "Documents", "PDF"),
     ("b.tar.zst", "Archives", "ZST"), ("os.iso", "Other", "ISO"), ("noext", "Other", "—"), (".hidden", "Other", "—"), (None, "Other", "—")],
)
def test_category_and_type_are_derived_from_the_name_only(name, category, label):
    assert P.category_for(name) == category and P.type_label(name) == label


@pytest.mark.parametrize(
    "width,tier,merge,collapsed,has_type,has_added",
    [(2560, "wide", False, False, True, True), (1920, "normal", False, False, True, False), (1366, "normal", False, False, True, False),
     (1280, "normal", False, False, True, False), (1279, "compact", True, False, False, False), (1100, "compact", True, False, False, False),
     (1099, "rail", True, True, False, False)],
)
def test_responsive_column_tiers(width, tier, merge, collapsed, has_type, has_added):
    layout = P.column_layout(width)
    assert (layout.tier, layout.merge_speed_eta, layout.sidebar_collapsed) == (tier, merge, collapsed)
    assert (P.COL_TYPE in layout.visible, P.COL_ADDED in layout.visible) == (has_type, has_added)
    assert {P.COL_NAME, P.COL_PROGRESS, P.COL_STATUS} <= layout.visible  # never disappear


def test_status_filters_map_states_and_reject_unknown_names():
    assert P.matches_status_filter(item(task=T.RETRY_WAIT), "Waiting")
    assert P.matches_status_filter(item(task=T.TRANSFERRING), "Downloading")
    assert not P.matches_status_filter(item(task=T.CANCELLED, queue=Q.REMOVED), "Failed")
    assert P.matches_status_filter(item(task=T.CANCELLED, queue=Q.REMOVED), "Cancelled")
    with pytest.raises(ValueError):
        P.matches_status_filter(item(), "Nope")


def test_search_matches_name_or_host_case_insensitively():
    it = item(name="Holiday.MP4", source_host="cdn.Example.net")
    assert P.matches_search(it, "holi") and P.matches_search(it, "EXAMPLE") and P.matches_search(it, "  ")
    assert not P.matches_search(it, "zzz")


def test_format_added_is_relative_and_safe_for_none():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    assert P.format_added(None) == "—"
    assert P.format_added(now.replace(hour=9, minute=5), now).startswith("Today")
    assert P.format_added(now - timedelta(days=1), now) == "Yesterday"
    assert P.format_added(now - timedelta(days=3), now) == "Fri"  # 2026-09-25 was a Friday, in English on any locale
    assert P.format_added(datetime(2026, 3, 5, 8, 0, tzinfo=timezone.utc), now) == "05 Mar"
    assert P.format_added(datetime(2025, 12, 31, 8, 0, tzinfo=timezone.utc), now) == "31 Dec 2025"
