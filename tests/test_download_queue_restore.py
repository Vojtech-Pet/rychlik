"""Prompt A8: DownloadQueue.restore() -- the queue-side rehydration entry
point used by restart recovery. Not a sequence of enqueue()/pause() calls;
it directly reconstructs the aggregate's bands from persisted facts."""

from datetime import datetime, timezone

import pytest

from rychlik.core.download_queue import (
    DownloadQueue,
    DuplicateQueuedTaskError,
    InvalidQueueOperationError,
    QueueEntry,
    QueueEntryState,
    QueuePriority,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(**kwargs) -> QueueEntry:
    defaults = dict(
        queue_entry_id="qe-A",
        task_id="A",
        state=QueueEntryState.QUEUED,
        priority=QueuePriority.NORMAL,
        position=0,
        enqueued_at=T0,
        updated_at=T0,
    )
    defaults.update(kwargs)
    return QueueEntry(**defaults)


def test_restore_preserves_identity_and_order():
    entries = [
        _entry(queue_entry_id="a", task_id="A", position=0),
        _entry(queue_entry_id="b", task_id="B", position=1),
    ]
    queue = DownloadQueue.restore(entries)
    assert [e.task_id for e in queue.active_entries()] == ["A", "B"]
    assert queue.get("a").queue_entry_id == "a"


def test_restore_preserves_removed_history():
    entries = [
        _entry(queue_entry_id="a", task_id="A", state=QueueEntryState.REMOVED, position=0),
    ]
    queue = DownloadQueue.restore(entries)
    assert queue.active_entries() == ()
    assert queue.get("a").state == QueueEntryState.REMOVED


def test_restore_allows_re_enqueue_history_same_task():
    entries = [
        _entry(queue_entry_id="old", task_id="A", state=QueueEntryState.REMOVED, position=0),
        _entry(queue_entry_id="new", task_id="A", state=QueueEntryState.QUEUED, position=0),
    ]
    queue = DownloadQueue.restore(entries)
    assert [e.queue_entry_id for e in queue.active_entries()] == ["new"]
    assert queue.get("old").state == QueueEntryState.REMOVED


def test_restore_rejects_duplicate_live_entries_for_same_task():
    entries = [
        _entry(queue_entry_id="x", task_id="A", state=QueueEntryState.QUEUED, position=0),
        _entry(queue_entry_id="y", task_id="A", state=QueueEntryState.PAUSED, position=0),
    ]
    with pytest.raises(DuplicateQueuedTaskError):
        DownloadQueue.restore(entries)


def test_restore_rejects_non_contiguous_positions():
    entries = [
        _entry(queue_entry_id="a", task_id="A", position=0),
        _entry(queue_entry_id="b", task_id="B", position=5),  # gap
    ]
    with pytest.raises(InvalidQueueOperationError):
        DownloadQueue.restore(entries)


def test_restore_new_enqueue_continues_after_restored_entries():
    entries = [_entry(queue_entry_id="a", task_id="A", position=0)]
    queue = DownloadQueue.restore(entries)
    new_entry = queue.enqueue("B")
    assert new_entry.position == 1
    assert [e.task_id for e in queue.active_entries()] == ["A", "B"]
