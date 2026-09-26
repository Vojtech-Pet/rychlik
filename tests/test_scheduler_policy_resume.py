"""Prompt A9: SchedulerPolicy's RESUME candidate extension."""

from datetime import datetime, timezone

from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import DownloadTask, DownloadTaskState, create_task
from rychlik.core.scheduler_policy import DispatchKind, SchedulerConfig, SchedulerPolicy

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _paused_task(task_id: str) -> DownloadTask:
    return (
        create_task(task_id, now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .pause_transfer(now=T0)
    )


def test_no_resume_requests_means_paused_task_never_selected():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": _paused_task("A")}
    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1))
    assert plan.selected == ()


def test_resume_requested_paused_task_is_selected_as_resume_kind():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": _paused_task("A")}
    plan = SchedulerPolicy().plan(
        queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1),
        resume_requested_queue_entry_ids=frozenset({entry.queue_entry_id}),
    )
    assert len(plan.selected) == 1
    assert plan.selected[0].kind == DispatchKind.RESUME
    assert plan.selected[0].queue_entry_id == entry.queue_entry_id


def test_queue_paused_blocks_resume_even_with_pending_request():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    queue.pause(entry.queue_entry_id)
    tasks = {"A": _paused_task("A")}
    plan = SchedulerPolicy().plan(
        queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1),
        resume_requested_queue_entry_ids=frozenset({entry.queue_entry_id}),
    )
    assert plan.selected == ()


def test_resume_consumes_normal_capacity_slot():
    queue = DownloadQueue(clock=lambda: T0)
    ready_entry = queue.enqueue("A")
    paused_entry = queue.enqueue("B")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0), "B": _paused_task("B")}
    plan = SchedulerPolicy().plan(
        queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1),
        resume_requested_queue_entry_ids=frozenset({paused_entry.queue_entry_id}),
    )
    # Only one slot: canonical order puts A (enqueued first, same priority)
    # ahead of B -- resume does not jump the queue (§22/§23).
    assert len(plan.selected) == 1
    assert plan.selected[0].task_id == "A"


def test_resume_does_not_bypass_priority():
    queue = DownloadQueue(clock=lambda: T0)
    high_ready = queue.enqueue("H", QueuePriority.HIGH)
    normal_paused = queue.enqueue("N", QueuePriority.NORMAL)
    tasks = {"H": create_task("H", now=T0).mark_ready(now=T0), "N": _paused_task("N")}
    plan = SchedulerPolicy().plan(
        queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1),
        resume_requested_queue_entry_ids=frozenset({normal_paused.queue_entry_id}),
    )
    assert len(plan.selected) == 1
    assert plan.selected[0].task_id == "H"


def test_reservation_protects_resume_candidate_too():
    queue = DownloadQueue(clock=lambda: T0)
    entry = queue.enqueue("A")
    tasks = {"A": _paused_task("A")}
    plan = SchedulerPolicy().plan(
        queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1),
        resume_requested_queue_entry_ids=frozenset({entry.queue_entry_id}),
        reserved_queue_entry_ids=frozenset({entry.queue_entry_id}),
    )
    assert plan.selected == ()


def test_default_start_candidates_have_start_kind():
    queue = DownloadQueue(clock=lambda: T0)
    queue.enqueue("A")
    tasks = {"A": create_task("A", now=T0).mark_ready(now=T0)}
    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=SchedulerConfig(max_active_transfers=1))
    assert plan.selected[0].kind == DispatchKind.START
