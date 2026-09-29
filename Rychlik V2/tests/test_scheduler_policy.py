from datetime import datetime, timezone

import pytest

from rychlik.core.download_queue import DownloadQueue, QueuePriority
from rychlik.core.download_task import DownloadTask, DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.scheduler_policy import (
    DispatchCandidate,
    InvalidSchedulerConfigError,
    SchedulerConfig,
    SchedulerPolicy,
    SchedulerSnapshotError,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _queue():
    return DownloadQueue(clock=lambda: T0)


def _ready_task(task_id: str) -> DownloadTask:
    return create_task(task_id, now=T0).mark_ready(now=T0)


def _transferring_task(task_id: str) -> DownloadTask:
    return _ready_task(task_id).start_transfer(now=T0)


def _config(max_active_transfers: int) -> SchedulerConfig:
    return SchedulerConfig(max_active_transfers=max_active_transfers)


def _selected_task_ids(plan) -> list[str]:
    return [c.task_id for c in plan.selected]


# --- basic dispatch (§47) -------------------------------------------------


def test_basic_dispatch_in_queue_order():
    queue = _queue()
    queue.enqueue("A")
    queue.enqueue("B")
    queue.enqueue("C")
    tasks = {t: _ready_task(t) for t in "ABC"}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(2))

    assert _selected_task_ids(plan) == ["A", "B"]


# --- priority (§48) --------------------------------------------------------


def test_priority_order_respected():
    queue = _queue()
    queue.enqueue("H1", QueuePriority.HIGH)
    queue.enqueue("H2", QueuePriority.HIGH)
    queue.enqueue("N1", QueuePriority.NORMAL)
    queue.enqueue("L1", QueuePriority.LOW)
    tasks = {t: _ready_task(t) for t in ["H1", "H2", "N1", "L1"]}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(2))

    assert _selected_task_ids(plan) == ["H1", "H2"]


# --- manual order (§49) -----------------------------------------------------


def test_manual_order_respected():
    queue = _queue()
    n3 = queue.enqueue("N3")
    n1 = queue.enqueue("N1")
    queue.enqueue("N2")
    queue.move_before(n3.queue_entry_id, n1.queue_entry_id)
    tasks = {t: _ready_task(t) for t in ["N1", "N2", "N3"]}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert _selected_task_ids(plan) == ["N3"]


# --- queue pause (§50) -------------------------------------------------------


def test_queue_paused_entry_excluded():
    queue = _queue()
    a = queue.enqueue("A")
    queue.pause(a.queue_entry_id)
    queue.enqueue("B")
    tasks = {"A": _ready_task("A"), "B": _ready_task("B")}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(2))

    assert _selected_task_ids(plan) == ["B"]


# --- task state filter (§51) -------------------------------------------------


def test_only_ready_tasks_are_candidates():
    queue = _queue()
    ids = list("ABCDEFGHIJ")
    for task_id in ids:
        queue.enqueue(task_id)

    tasks = {
        "A": create_task("A", now=T0),  # CREATED
        "B": create_task("B", now=T0).start_resolving(now=T0),  # RESOLVING
        "C": _ready_task("C"),  # READY
        "D": _transferring_task("D").pause_transfer(now=T0),  # PAUSED
        "E": _transferring_task("E").wait_for_retry(
            DownloadTaskFailure(code="NET", message="x", retryable=True), now=T0
        ),  # RETRY_WAIT
        "F": _transferring_task("F").start_verification(now=T0),  # VERIFYING
        "G": _transferring_task("G").start_post_processing(now=T0),  # POST_PROCESSING
        "H": _transferring_task("H").complete(now=T0),  # COMPLETED
        "I": _transferring_task("I").fail(
            DownloadTaskFailure(code="X", message="x", retryable=False), now=T0
        ),  # FAILED
        "J": create_task("J", now=T0).cancel(now=T0),  # CANCELLED
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(10))

    assert _selected_task_ids(plan) == ["C"]


# --- active capacity (§52) ---------------------------------------------------


def test_active_capacity_limits_selection():
    queue = _queue()
    for task_id in ["A", "B", "C"]:
        queue.enqueue(task_id)
    tasks = {
        "X": _transferring_task("X"),
        "Y": _transferring_task("Y"),
        "A": _ready_task("A"),
        "B": _ready_task("B"),
        "C": _ready_task("C"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(3))

    assert plan.active_transfer_count == 2
    assert plan.available_slots_before_selection == 1
    assert _selected_task_ids(plan) == ["A"]


# --- transferring task without queue entry (§53) -----------------------------


def test_transferring_task_without_queue_entry_still_consumes_slot():
    queue = _queue()
    queue.enqueue("A")
    tasks = {
        "X": _transferring_task("X"),  # no QueueEntry for X at all
        "A": _ready_task("A"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert plan.active_transfer_count == 1
    assert plan.available_slots_before_selection == 0
    assert plan.selected == ()


# --- verifying / post-processing do not consume slots (§54) ------------------


def test_verifying_does_not_consume_slot():
    queue = _queue()
    queue.enqueue("A")
    tasks = {
        "X": _transferring_task("X").start_verification(now=T0),
        "A": _ready_task("A"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert plan.active_transfer_count == 0
    assert _selected_task_ids(plan) == ["A"]


def test_post_processing_does_not_consume_slot():
    queue = _queue()
    queue.enqueue("A")
    tasks = {
        "X": _transferring_task("X").start_post_processing(now=T0),
        "A": _ready_task("A"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert plan.active_transfer_count == 0
    assert _selected_task_ids(plan) == ["A"]


# --- task-paused does not consume a slot (§55) --------------------------------


def test_task_paused_does_not_consume_slot():
    queue = _queue()
    queue.enqueue("A")
    tasks = {
        "X": _transferring_task("X").pause_transfer(now=T0),
        "A": _ready_task("A"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert plan.active_transfer_count == 0
    assert _selected_task_ids(plan) == ["A"]


# --- zero / over capacity (§56/§57) -------------------------------------------


def test_zero_capacity_selects_nothing_without_error():
    queue = _queue()
    queue.enqueue("A")
    tasks = {"A": _ready_task("A")}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(0))

    assert plan.selected == ()
    assert plan.available_slots_before_selection == 0
    assert plan.active_transfer_count == 0


def test_over_capacity_selects_nothing_and_does_not_go_negative():
    queue = _queue()
    queue.enqueue("A")
    tasks = {
        "X": _transferring_task("X"),
        "Y": _transferring_task("Y"),
        "Z": _transferring_task("Z"),
        "A": _ready_task("A"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(2))

    assert plan.active_transfer_count == 3
    assert plan.available_slots_before_selection == 0
    assert plan.selected == ()


# --- config validation (§58) --------------------------------------------------


def test_negative_max_active_transfers_rejected():
    with pytest.raises(InvalidSchedulerConfigError):
        SchedulerConfig(max_active_transfers=-1)


# --- missing task (§59/§60) ----------------------------------------------------


def test_missing_live_task_raises_snapshot_error():
    queue = _queue()
    queue.enqueue("A")
    tasks: dict[str, DownloadTask] = {}

    with pytest.raises(SchedulerSnapshotError):
        SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))


def test_removed_entry_with_missing_task_does_not_fail():
    queue = _queue()
    a = queue.enqueue("A")
    queue.remove(a.queue_entry_id)  # historical REMOVED entry, task never in snapshot
    queue.enqueue("B")
    tasks = {"B": _ready_task("B")}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert _selected_task_ids(plan) == ["B"]


def test_paused_entry_with_missing_task_raises_snapshot_error():
    queue = _queue()
    a = queue.enqueue("A")
    queue.pause(a.queue_entry_id)
    tasks: dict[str, DownloadTask] = {}

    with pytest.raises(SchedulerSnapshotError):
        SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))


# --- extra ready task without queue entry (§61) --------------------------------


def test_ready_task_without_queue_entry_is_not_selected():
    queue = _queue()  # empty queue
    tasks = {"X": _ready_task("X")}

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(5))

    assert plan.selected == ()


# --- determinism (§62) -----------------------------------------------------------


def test_planning_is_deterministic():
    queue = _queue()
    queue.enqueue("A")
    queue.enqueue("B")
    tasks = {"A": _ready_task("A"), "B": _ready_task("B")}

    plan1 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    plan2 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    assert plan1 == plan2


# --- no mutation (§63) ------------------------------------------------------------


def test_planning_does_not_mutate_queue_or_tasks():
    queue = _queue()
    a = queue.enqueue("A")
    tasks = {"A": _ready_task("A")}

    before_entry = queue.get(a.queue_entry_id)
    before_task = tasks["A"]

    SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))

    after_entry = queue.get(a.queue_entry_id)
    after_task = tasks["A"]

    assert before_entry == after_entry
    assert before_task == after_task
    assert before_task.updated_at == after_task.updated_at
    assert before_task.attempt_count == after_task.attempt_count


# --- priority change reflects in next plan (§64) --------------------------------


def test_priority_change_between_plans_changes_selection():
    queue = _queue()
    a = queue.enqueue("A", QueuePriority.NORMAL)
    queue.enqueue("B", QueuePriority.HIGH)
    tasks = {"A": _ready_task("A"), "B": _ready_task("B")}

    first_plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    assert _selected_task_ids(first_plan) == ["B"]

    queue.set_priority(a.queue_entry_id, QueuePriority.HIGH)
    second_plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    # A moved to the end of the HIGH band (A1 semantics); B was already HIGH first.
    assert _selected_task_ids(second_plan) == ["B"]

    # But pausing B now clears the way for A while B stays HIGH.
    b_entry = next(e for e in queue.active_entries() if e.task_id == "B")
    queue.pause(b_entry.queue_entry_id)
    third_plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    assert _selected_task_ids(third_plan) == ["A"]


# --- queue pause/resume reflects plan (§65) --------------------------------------


def test_queue_pause_resume_toggles_selection():
    queue = _queue()
    a = queue.enqueue("A")
    tasks = {"A": _ready_task("A")}

    plan1 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    assert _selected_task_ids(plan1) == ["A"]

    queue.pause(a.queue_entry_id)
    plan2 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    assert plan2.selected == ()

    queue.resume(a.queue_entry_id)
    plan3 = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(1))
    assert _selected_task_ids(plan3) == ["A"]

    assert tasks["A"].state == DownloadTaskState.READY  # task state never touched


# --- retry-ready re-enters candidates (§66) ---------------------------------------


def test_retry_wait_excluded_until_marked_ready_again():
    queue = _queue()
    queue.enqueue("A")
    failure = DownloadTaskFailure(code="NET", message="x", retryable=True)
    retrying_task = _transferring_task("A").wait_for_retry(failure, now=T0)

    plan1 = SchedulerPolicy().plan(queue=queue, tasks={"A": retrying_task}, config=_config(1))
    assert plan1.selected == ()

    ready_again = retrying_task.mark_retry_ready(now=T0)
    plan2 = SchedulerPolicy().plan(queue=queue, tasks={"A": ready_again}, config=_config(1))
    assert _selected_task_ids(plan2) == ["A"]


# --- complex scenario (§67) --------------------------------------------------------


def test_complex_scenario():
    queue = _queue()
    h1 = queue.enqueue("H1", QueuePriority.HIGH)
    h2 = queue.enqueue("H2", QueuePriority.HIGH)
    n2 = queue.enqueue("N2", QueuePriority.NORMAL)
    queue.enqueue("N1", QueuePriority.NORMAL)
    queue.enqueue("N3", QueuePriority.NORMAL)
    queue.enqueue("L1", QueuePriority.LOW)
    queue.pause(h2.queue_entry_id)

    failure = DownloadTaskFailure(code="NET", message="x", retryable=True)
    tasks = {
        "X": _transferring_task("X"),
        "Y": _transferring_task("Y").start_verification(now=T0),
        "H1": _ready_task("H1"),
        "H2": _ready_task("H2"),
        "N1": _ready_task("N1"),
        "N2": _ready_task("N2"),
        "N3": _transferring_task("N3").wait_for_retry(failure, now=T0),
        "L1": _ready_task("L1"),
    }

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(3))

    assert plan.active_transfer_count == 1  # only X; Y is VERIFYING
    assert plan.available_slots_before_selection == 2
    assert _selected_task_ids(plan) == ["H1", "N2"]
    assert plan.remaining_slots_after_selection == 0

    selected_ids = {c.task_id for c in plan.selected}
    assert "H2" not in selected_ids  # excluded by queue pause
    assert "N3" not in selected_ids  # excluded by task RETRY_WAIT


# --- Prompt A5 reservation extension (§77) ----------------------------------------------


def test_empty_reservation_set_preserves_old_behavior():
    queue = _queue()
    queue.enqueue("A")
    queue.enqueue("B")
    tasks = {"A": _ready_task("A"), "B": _ready_task("B")}

    plan = SchedulerPolicy().plan(
        queue=queue, tasks=tasks, config=_config(2), reserved_queue_entry_ids=frozenset()
    )

    assert _selected_task_ids(plan) == ["A", "B"]


def test_reserved_ready_entry_excluded_from_selection():
    queue = _queue()
    a = queue.enqueue("A")
    queue.enqueue("B")
    tasks = {"A": _ready_task("A"), "B": _ready_task("B")}

    plan = SchedulerPolicy().plan(
        queue=queue,
        tasks=tasks,
        config=_config(2),
        reserved_queue_entry_ids=frozenset({a.queue_entry_id}),
    )

    assert _selected_task_ids(plan) == ["B"]


def test_reserved_pending_entry_reduces_available_capacity():
    queue = _queue()
    a = queue.enqueue("A")
    queue.enqueue("B")
    tasks = {"A": _ready_task("A"), "B": _ready_task("B")}

    plan = SchedulerPolicy().plan(
        queue=queue,
        tasks=tasks,
        config=_config(1),
        reserved_queue_entry_ids=frozenset({a.queue_entry_id}),
    )

    assert plan.available_slots_before_selection == 0
    assert plan.selected == ()


def test_reserved_transferring_entry_not_double_counted():
    queue = _queue()
    a = queue.enqueue("A")
    queue.enqueue("B")
    tasks = {"A": _transferring_task("A"), "B": _ready_task("B")}

    # A is reserved AND already TRANSFERRING -- must count once, not twice.
    plan = SchedulerPolicy().plan(
        queue=queue,
        tasks=tasks,
        config=_config(2),
        reserved_queue_entry_ids=frozenset({a.queue_entry_id}),
    )

    assert plan.active_transfer_count == 1  # unchanged semantics: TRANSFERRING count only
    assert plan.available_slots_before_selection == 1
    assert _selected_task_ids(plan) == ["B"]


# --- structural dependencies (§45) ----------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.scheduler_policy as module

    forbidden = {"PySide6", "requests", "httpx", "yt_dlp", "asyncio"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    top_level = {name.split(".")[0] for name in imported_modules}
    assert top_level & forbidden == set()
    assert not any(m.startswith("rychlik.share") for m in imported_modules)  # no ShareLink/LocalShareOrigin
    assert not any(m.startswith("rychlik.acquisition") for m in imported_modules)  # no AcquisitionService


# --- bounded stress test (§68) -------------------------------------------------------


def test_bounded_stress_scenario():
    queue = _queue()
    priorities = [QueuePriority.LOW, QueuePriority.NORMAL, QueuePriority.HIGH]
    tasks: dict[str, DownloadTask] = {}

    for i in range(100):
        task_id = f"task-{i}"
        queue.enqueue(task_id, priorities[i % 3])
        if i % 5 == 0:
            tasks[task_id] = _transferring_task(task_id)
        elif i % 5 == 1:
            entries = [e for e in queue.active_entries() if e.task_id == task_id]
            queue.pause(entries[0].queue_entry_id)
            tasks[task_id] = _ready_task(task_id)
        else:
            tasks[task_id] = _ready_task(task_id)

    plan = SchedulerPolicy().plan(queue=queue, tasks=tasks, config=_config(10))

    assert len(plan.selected) <= plan.available_slots_before_selection
    for candidate in plan.selected:
        entry = queue.get(candidate.queue_entry_id)
        assert entry.state.name == "QUEUED"
        assert tasks[candidate.task_id].state == DownloadTaskState.READY

    task_ids = [c.task_id for c in plan.selected]
    assert len(task_ids) == len(set(task_ids))

    eligible_order = [e.task_id for e in queue.eligible_entries() if tasks[e.task_id].state == DownloadTaskState.READY]
    assert task_ids == eligible_order[: len(task_ids)]
