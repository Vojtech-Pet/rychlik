"""Final GUI/UX implementation: incremental model + filter/sort proxy (stable identity, selection, scale)."""

import time
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QItemSelectionModel, QModelIndex

from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import DownloadViewSnapshot
from rychlik.gui import presentation as P
from rychlik.gui.download_model import ID_ROLE, ITEM_ROLE, DownloadFilterProxy, DownloadTableModel

T, Q = DownloadTaskState, QueueEntryState
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def mk(eid, name=None, task=T.READY, queue=Q.QUEUED, prio=QueuePriority.NORMAL, total=None, frac=None, speed=None, host="h.example", age=0, **kw):
    return DownloadViewSnapshot(
        task_id="t" + eid, queue_entry_id=eid, display_name=name if name is not None else f"{eid}.mp4", task_state=task,
        queue_state=queue, priority=prio, position=0, attempt_count=1, bytes_downloaded=0, total_bytes=total,
        progress_fraction=frac, speed_bps=speed, eta_seconds=None, source_host=host, added_at=NOW - timedelta(minutes=age), **kw,
    )


def ids(model):
    return [model.index(r, 0).data(ID_ROLE) for r in range(model.rowCount())]


def test_set_items_fills_rows_and_exposes_identity_and_display_values(qapp):
    m = DownloadTableModel()
    m.set_items([mk("a", "holiday.mp4", T.TRANSFERRING, total=198_000_000, frac=0.5, speed=12_400_000)])
    assert m.rowCount() == 1 and m.columnCount() == 11
    assert m.index(0, 0).data(ID_ROLE) == "a" and m.index(0, 0).data(ITEM_ROLE).display_name == "holiday.mp4"
    assert m.index(0, P.COL_NAME).data() == "holiday.mp4" and m.index(0, P.COL_TYPE).data() == "MP4"
    assert m.index(0, P.COL_SIZE).data() == "198.0 MB" and m.index(0, P.COL_PROGRESS).data() == "50%"
    assert m.index(0, P.COL_STATUS).data() == "Downloading" and m.index(0, P.COL_SPEED).data() == "12.4 MB/s"
    assert m.index(0, P.COL_CATEGORY).data() == "Video"
    assert "h.example" in m.index(0, P.COL_NAME).data(0x0003)  # tooltip carries the host, never a URL


def test_missing_values_render_as_a_dash_never_an_estimate(qapp):
    m = DownloadTableModel()
    m.set_items([mk("a", task=T.READY)])
    assert [m.index(0, c).data() for c in (P.COL_SIZE, P.COL_PROGRESS, P.COL_SPEED, P.COL_ETA)] == ["—"] * 4


def test_compact_layout_merges_speed_and_remaining_into_one_cell(qapp):
    m = DownloadTableModel()
    it = mk("a", task=T.TRANSFERRING, speed=31_200_000)
    it = DownloadViewSnapshot(**{**it.__dict__, "eta_seconds": 41.0})
    m.set_items([it])
    assert m.index(0, P.COL_SPEED).data() == "31.2 MB/s" and m.index(0, P.COL_ETA).data() == "41 s"
    m.set_merge_speed_eta(True)
    assert m.index(0, P.COL_SPEED).data() == "31.2 MB/s · 41 s"


def test_selection_survives_reorder_updates_and_insertions_by_identity(qapp):
    m = DownloadTableModel()
    m.set_items([mk("a"), mk("b"), mk("c")])
    sel = QItemSelectionModel(m)
    sel.select(m.index(1, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert [i.data(ID_ROLE) for i in sel.selectedRows()] == ["b"]
    m.set_items([mk("c"), mk("b", frac=0.4), mk("a"), mk("d")])  # reorder + update + insert
    assert ids(m) == ["c", "b", "a", "d"]
    assert [i.data(ID_ROLE) for i in sel.selectedRows()] == ["b"]  # still the same download, new row
    assert sel.selectedRows()[0].row() == 1


def test_a_removed_occurrence_is_dropped_from_selection_not_inherited(qapp):
    m = DownloadTableModel()
    m.set_items([mk("a"), mk("b")])
    sel = QItemSelectionModel(m)
    sel.select(m.index(0, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    m.set_items([mk("b")])  # "a" left the snapshot
    assert sel.selectedRows() == [] and ids(m) == ["b"]  # "b" did NOT inherit a's selection


def test_identical_snapshot_emits_no_structure_changes(qapp):
    m = DownloadTableModel()
    items = [mk("a"), mk("b")]
    m.set_items(items)
    events = []
    for sig in (m.rowsInserted, m.rowsRemoved, m.layoutChanged, m.dataChanged, m.modelReset):
        sig.connect(lambda *a, _s=sig: events.append(1))
    m.set_items(list(items))
    assert events == []


def test_proxy_filters_by_status_category_and_search_and_keeps_identity(qapp):
    m = DownloadTableModel()
    m.set_items([
        mk("a", "holiday.mp4", T.TRANSFERRING), mk("b", "song.flac", T.PAUSED), mk("c", "doc.pdf", T.COMPLETED, Q.REMOVED),
        mk("d", "big.iso", T.FAILED, Q.REMOVED, host="mirror.example.org"),
    ])
    p = DownloadFilterProxy()
    p.setSourceModel(m)
    assert p.rowCount() == 4
    p.set_status_filter("Paused")
    assert [p.id_for_index(p.index(r, 0)) for r in range(p.rowCount())] == ["b"]
    p.set_status_filter("All")
    p.set_category_filter("Documents")
    assert [p.id_for_index(p.index(r, 0)) for r in range(p.rowCount())] == ["c"]
    p.set_category_filter(None)
    p.set_search_text("mirror")
    assert [p.id_for_index(p.index(r, 0)) for r in range(p.rowCount())] == ["d"]
    assert p.item_for_index(p.index(0, 0)).display_name == "big.iso"


def test_proxy_sorting_never_redirects_identity(qapp):
    m = DownloadTableModel()
    m.set_items([mk("a", "b.mp4", total=10, frac=0.9), mk("b", "a.mp4", total=30, frac=0.1), mk("c", "c.mp4", total=20, frac=0.5, age=5)])
    p = DownloadFilterProxy()
    p.setSourceModel(m)
    order = lambda: [p.id_for_index(p.index(r, 0)) for r in range(p.rowCount())]
    assert order() == ["a", "b", "c"]  # queue order = backend order
    p.set_sort("Name")
    assert order() == ["b", "a", "c"]
    p.set_sort("Size", descending=True)
    assert order() == ["b", "c", "a"]
    p.set_sort("Progress")
    assert order() == ["b", "c", "a"]
    p.set_sort("Newest", descending=True)
    assert order()[-1] == "c"  # the oldest is last
    p.set_sort("Queue order")
    assert order() == ["a", "b", "c"]
    with pytest.raises(ValueError):
        p.set_sort("Colour")


@pytest.mark.parametrize("n,limit", [(100, 0.5), (1_000, 1.0), (10_000, 6.0)])
def test_model_stays_responsive_at_scale(qapp, n, limit):
    m = DownloadTableModel()
    items = [mk(f"e{i}", f"file-{i}.mp4", T.TRANSFERRING if i % 7 == 0 else T.COMPLETED, Q.QUEUED if i % 7 == 0 else Q.REMOVED,
                total=1_000_000 + i, frac=(i % 100) / 100, speed=1000.0 * (i % 9)) for i in range(n)]
    start = time.perf_counter()
    m.set_items(items)  # initial fill
    updated = [DownloadViewSnapshot(**{**i.__dict__, "bytes_downloaded": 5}) for i in items]
    m.set_items(updated)  # every row changes
    m.set_items(list(reversed(updated)))  # full reorder
    m.set_items(updated[: n // 2])  # half removed
    elapsed = time.perf_counter() - start
    assert m.rowCount() == n // 2 and elapsed < limit, elapsed
