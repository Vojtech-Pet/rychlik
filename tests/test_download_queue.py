from datetime import datetime, timedelta, timezone

import pytest

from rychlik.core.download_queue import (
    CrossPriorityReorderError,
    DownloadQueue,
    DuplicateQueuedTaskError,
    InvalidQueueOperationError,
    InvalidQueueTransitionError,
    QueueEntryState,
    QueuePriority,
    UnknownQueueEntryError,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock_seq(*moments):
    it = iter(moments)
    return lambda: next(it)


def _queue(now=T0):
    return DownloadQueue(clock=lambda: now)


# --- state transitions (§45) ---------------------------------------------


def test_new_entry_starts_queued():
    q = _queue()
    entry = q.enqueue("task-1")
    assert entry.state == QueueEntryState.QUEUED


def test_queued_to_paused():
    q = _queue()
    entry = q.enqueue("task-1")
    updated = q.pause(entry.queue_entry_id)
    assert updated.state == QueueEntryState.PAUSED


def test_paused_to_queued():
    q = _queue()
    entry = q.enqueue("task-1")
    q.pause(entry.queue_entry_id)
    updated = q.resume(entry.queue_entry_id)
    assert updated.state == QueueEntryState.QUEUED


def test_queued_to_removed():
    q = _queue()
    entry = q.enqueue("task-1")
    updated = q.remove(entry.queue_entry_id)
    assert updated.state == QueueEntryState.REMOVED


def test_paused_to_removed():
    q = _queue()
    entry = q.enqueue("task-1")
    q.pause(entry.queue_entry_id)
    updated = q.remove(entry.queue_entry_id)
    assert updated.state == QueueEntryState.REMOVED


def test_removed_to_queued_rejected():
    q = _queue()
    entry = q.enqueue("task-1")
    q.remove(entry.queue_entry_id)
    with pytest.raises(InvalidQueueTransitionError):
        q.resume(entry.queue_entry_id)


def test_removed_to_paused_rejected():
    q = _queue()
    entry = q.enqueue("task-1")
    q.remove(entry.queue_entry_id)
    with pytest.raises(InvalidQueueTransitionError):
        q.pause(entry.queue_entry_id)


def test_pause_already_paused_idempotent():
    q = _queue()
    entry = q.enqueue("task-1")
    q.pause(entry.queue_entry_id)
    result = q.pause(entry.queue_entry_id)
    assert result.state == QueueEntryState.PAUSED


def test_resume_already_queued_idempotent():
    q = _queue()
    entry = q.enqueue("task-1")
    result = q.resume(entry.queue_entry_id)
    assert result.state == QueueEntryState.QUEUED


def test_remove_already_removed_idempotent():
    q = _queue()
    entry = q.enqueue("task-1")
    q.remove(entry.queue_entry_id)
    result = q.remove(entry.queue_entry_id)
    assert result.state == QueueEntryState.REMOVED


# --- priority (§46) --------------------------------------------------------


def test_default_priority_is_normal():
    q = _queue()
    entry = q.enqueue("task-1")
    assert entry.priority == QueuePriority.NORMAL


def test_high_before_normal():
    q = _queue()
    q.enqueue("normal-1", QueuePriority.NORMAL)
    q.enqueue("high-1", QueuePriority.HIGH)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["high-1", "normal-1"]


def test_normal_before_low():
    q = _queue()
    q.enqueue("low-1", QueuePriority.LOW)
    q.enqueue("normal-1", QueuePriority.NORMAL)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["normal-1", "low-1"]


def test_priority_change_normal_to_high():
    q = _queue()
    entry = q.enqueue("task-1", QueuePriority.NORMAL)
    updated = q.set_priority(entry.queue_entry_id, QueuePriority.HIGH)
    assert updated.priority == QueuePriority.HIGH


def test_priority_change_high_to_low():
    q = _queue()
    entry = q.enqueue("task-1", QueuePriority.HIGH)
    updated = q.set_priority(entry.queue_entry_id, QueuePriority.LOW)
    assert updated.priority == QueuePriority.LOW


def test_same_priority_update_is_noop():
    q = _queue()
    e1 = q.enqueue("task-1", QueuePriority.NORMAL)
    e2 = q.enqueue("task-2", QueuePriority.NORMAL)
    q.set_priority(e1.queue_entry_id, QueuePriority.NORMAL)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["task-1", "task-2"]  # e1 did not move to the end


def test_same_priority_update_does_not_change_updated_at():
    q = _queue(now=T0)
    entry = q.enqueue("task-1")
    result = q.set_priority(entry.queue_entry_id, QueuePriority.NORMAL, now=T0 + timedelta(hours=1))
    assert result.updated_at == T0


def test_priority_change_moves_entry_to_end_of_target_band():
    q = _queue()
    q.enqueue("h1", QueuePriority.HIGH)
    n1 = q.enqueue("n1", QueuePriority.NORMAL)
    q.enqueue("n2", QueuePriority.NORMAL)
    q.set_priority(n1.queue_entry_id, QueuePriority.HIGH)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["h1", "n1", "n2"]


def test_priority_change_preserves_paused_state():
    q = _queue()
    entry = q.enqueue("task-1", QueuePriority.NORMAL)
    q.pause(entry.queue_entry_id)
    updated = q.set_priority(entry.queue_entry_id, QueuePriority.HIGH)
    assert updated.state == QueueEntryState.PAUSED
    assert updated.priority == QueuePriority.HIGH


def test_set_priority_rejects_non_enum_value():
    q = _queue()
    entry = q.enqueue("task-1")
    with pytest.raises(InvalidQueueOperationError):
        q.set_priority(entry.queue_entry_id, 300)  # type: ignore[arg-type]


def test_set_priority_on_removed_entry_rejected():
    q = _queue()
    entry = q.enqueue("task-1")
    q.remove(entry.queue_entry_id)
    with pytest.raises(InvalidQueueOperationError):
        q.set_priority(entry.queue_entry_id, QueuePriority.HIGH)


# --- ordering (§47) ---------------------------------------------------------


def test_fifo_insertion_within_same_priority():
    q = _queue()
    q.enqueue("a")
    q.enqueue("b")
    q.enqueue("c")
    order = [e.task_id for e in q.active_entries()]
    assert order == ["a", "b", "c"]


def test_manual_move_before():
    q = _queue()
    a = q.enqueue("a")
    q.enqueue("b")
    c = q.enqueue("c")
    q.move_before(c.queue_entry_id, a.queue_entry_id)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["c", "a", "b"]


def test_manual_move_after():
    q = _queue()
    a = q.enqueue("a")
    q.enqueue("b")
    c = q.enqueue("c")
    q.move_after(a.queue_entry_id, c.queue_entry_id)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["b", "c", "a"]


def test_paused_entry_retains_position():
    q = _queue()
    q.enqueue("a")
    b = q.enqueue("b")
    q.enqueue("c")
    q.pause(b.queue_entry_id)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["a", "b", "c"]


def test_resume_retains_position():
    q = _queue()
    q.enqueue("a")
    b = q.enqueue("b")
    q.enqueue("c")
    q.pause(b.queue_entry_id)
    q.resume(b.queue_entry_id)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["a", "b", "c"]


def test_paused_entry_can_be_manually_reordered():
    q = _queue()
    a = q.enqueue("a")
    b = q.enqueue("b")
    q.enqueue("c")
    q.pause(b.queue_entry_id)
    q.move_before(b.queue_entry_id, a.queue_entry_id)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["b", "a", "c"]


def test_cross_priority_move_rejected():
    q = _queue()
    low = q.enqueue("low-1", QueuePriority.LOW)
    high = q.enqueue("high-1", QueuePriority.HIGH)
    with pytest.raises(CrossPriorityReorderError):
        q.move_before(low.queue_entry_id, high.queue_entry_id)


def test_removed_entry_absent_from_active_projection():
    q = _queue()
    a = q.enqueue("a")
    q.enqueue("b")
    q.remove(a.queue_entry_id)
    order = [e.task_id for e in q.active_entries()]
    assert order == ["b"]


def test_removed_entry_absent_from_eligible_projection():
    q = _queue()
    a = q.enqueue("a")
    q.enqueue("b")
    q.remove(a.queue_entry_id)
    order = [e.task_id for e in q.eligible_entries()]
    assert order == ["b"]


def test_deterministic_order_after_multiple_operations():
    q = _queue()
    a = q.enqueue("a")
    q.enqueue("b")
    q.pause(a.queue_entry_id)
    q.resume(a.queue_entry_id)
    order1 = [e.task_id for e in q.active_entries()]
    order2 = [e.task_id for e in q.active_entries()]
    assert order1 == order2 == ["a", "b"]


def test_move_before_target_itself_rejected():
    q = _queue()
    a = q.enqueue("a")
    with pytest.raises(InvalidQueueOperationError):
        q.move_before(a.queue_entry_id, a.queue_entry_id)


def test_move_involving_removed_entry_rejected():
    q = _queue()
    a = q.enqueue("a")
    b = q.enqueue("b")
    q.remove(a.queue_entry_id)
    with pytest.raises(InvalidQueueOperationError):
        q.move_before(b.queue_entry_id, a.queue_entry_id)


# --- duplicate task policy (§48) --------------------------------------------


def test_duplicate_enqueue_rejected_when_queued():
    q = _queue()
    q.enqueue("task-1")
    with pytest.raises(DuplicateQueuedTaskError):
        q.enqueue("task-1")


def test_duplicate_enqueue_rejected_when_paused():
    q = _queue()
    entry = q.enqueue("task-1")
    q.pause(entry.queue_entry_id)
    with pytest.raises(DuplicateQueuedTaskError):
        q.enqueue("task-1")


def test_reenqueue_after_removal_allowed():
    q = _queue()
    a = q.enqueue("task-1")
    q.remove(a.queue_entry_id)
    b = q.enqueue("task-1")
    assert b.state == QueueEntryState.QUEUED


def test_reenqueue_after_removal_generates_new_entry_id():
    q = _queue()
    a = q.enqueue("task-1")
    q.remove(a.queue_entry_id)
    b = q.enqueue("task-1")
    assert a.queue_entry_id != b.queue_entry_id


# --- eligibility (§49) ------------------------------------------------------


def test_eligibility_projection():
    q = _queue()
    h1 = q.enqueue("h1", QueuePriority.HIGH)
    h2 = q.enqueue("h2", QueuePriority.HIGH)
    q.pause(h2.queue_entry_id)
    q.enqueue("n1", QueuePriority.NORMAL)
    q.enqueue("l1", QueuePriority.LOW)

    eligible = [e.task_id for e in q.eligible_entries()]
    assert eligible == ["h1", "n1", "l1"]

    active = [e.task_id for e in q.active_entries()]
    assert active == ["h1", "h2", "n1", "l1"]


# --- timestamps (§50) -------------------------------------------------------


def test_enqueued_at_and_updated_at_are_timezone_aware():
    q = _queue()
    entry = q.enqueue("task-1")
    assert entry.enqueued_at.tzinfo is not None
    assert entry.updated_at.tzinfo is not None


def test_real_mutation_updates_updated_at():
    times = _clock_seq(T0, T0 + timedelta(minutes=5))
    q = DownloadQueue(clock=times)
    entry = q.enqueue("task-1")
    updated = q.pause(entry.queue_entry_id)
    assert updated.updated_at == T0 + timedelta(minutes=5)
    assert updated.updated_at != entry.updated_at


def test_idempotent_noop_does_not_change_updated_at():
    times = _clock_seq(T0, T0 + timedelta(minutes=5))
    q = DownloadQueue(clock=times)
    entry = q.enqueue("task-1")
    result = q.resume(entry.queue_entry_id)  # already QUEUED: no-op
    assert result.updated_at == entry.updated_at == T0


# --- encapsulation (§51) ----------------------------------------------------


def test_active_entries_returned_as_immutable_tuple():
    q = _queue()
    q.enqueue("a")
    entries = q.active_entries()
    assert isinstance(entries, tuple)
    with pytest.raises(AttributeError):
        entries.append("x")  # type: ignore[attr-defined]


def test_queue_entry_fields_are_frozen():
    q = _queue()
    entry = q.enqueue("a")
    with pytest.raises(Exception):
        entry.state = QueueEntryState.PAUSED  # type: ignore[misc]


# --- unknown entry handling --------------------------------------------------


def test_unknown_entry_operations_raise():
    q = _queue()
    with pytest.raises(UnknownQueueEntryError):
        q.pause("does-not-exist")
    with pytest.raises(UnknownQueueEntryError):
        q.resume("does-not-exist")
    with pytest.raises(UnknownQueueEntryError):
        q.remove("does-not-exist")
    with pytest.raises(UnknownQueueEntryError):
        q.set_priority("does-not-exist", QueuePriority.HIGH)


# --- complex scenario (§52) --------------------------------------------------


def test_complex_multi_step_scenario():
    q = _queue()
    a = q.enqueue("A", QueuePriority.NORMAL)
    b = q.enqueue("B", QueuePriority.NORMAL)
    c = q.enqueue("C", QueuePriority.HIGH)
    d = q.enqueue("D", QueuePriority.LOW)
    e = q.enqueue("E", QueuePriority.NORMAL)

    assert [x.task_id for x in q.active_entries()] == ["C", "A", "B", "E", "D"]

    q.pause(b.queue_entry_id)
    q.move_before(e.queue_entry_id, a.queue_entry_id)
    q.set_priority(d.queue_entry_id, QueuePriority.HIGH)

    active = [x.task_id for x in q.active_entries()]
    assert active == ["C", "D", "E", "A", "B"]

    eligible = [x.task_id for x in q.eligible_entries()]
    assert eligible == ["C", "D", "E", "A"]

    q.resume(b.queue_entry_id)
    q.remove(c.queue_entry_id)

    final_active = [x.task_id for x in q.active_entries()]
    assert final_active == ["D", "E", "A", "B"]
    final_eligible = [x.task_id for x in q.eligible_entries()]
    assert final_eligible == ["D", "E", "A", "B"]


# --- bounded stress test (§53) ------------------------------------------------


def test_bounded_stress_scenario():
    q = _queue()
    priorities = [QueuePriority.LOW, QueuePriority.NORMAL, QueuePriority.HIGH]
    entries = []
    for i in range(100):
        priority = priorities[i % 3]
        entries.append(q.enqueue(f"task-{i}", priority))

    for i in range(0, 100, 7):
        q.pause(entries[i].queue_entry_id)

    # deterministic reorders within HIGH band
    high_entries = [e for e in q.active_entries() if e.priority == QueuePriority.HIGH]
    if len(high_entries) >= 2:
        q.move_before(high_entries[-1].queue_entry_id, high_entries[0].queue_entry_id)

    active = q.active_entries()
    task_ids = [e.task_id for e in active]
    assert len(task_ids) == len(set(task_ids)) == 100  # no duplicate active task_id

    # canonical ordering: priority rank non-increasing across the sequence
    ranks = [e.priority.value for e in active]
    for band_start in range(len(ranks) - 1):
        if ranks[band_start] != ranks[band_start + 1]:
            assert ranks[band_start] > ranks[band_start + 1]

    # eligible subset correctness
    eligible_ids = {e.queue_entry_id for e in q.eligible_entries()}
    for entry in active:
        if entry.state == QueueEntryState.QUEUED:
            assert entry.queue_entry_id in eligible_ids
        else:
            assert entry.queue_entry_id not in eligible_ids

    # positions valid (contiguous 0..n-1) within each band
    for priority in priorities:
        band_entries = sorted(
            (e for e in active if e.priority == priority), key=lambda e: e.position
        )
        assert [e.position for e in band_entries] == list(range(len(band_entries)))
