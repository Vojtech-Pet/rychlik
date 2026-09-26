from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_queue import QueueEntry, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskFailure, DownloadTaskState, create_task
from rychlik.core.partial_transfer import PartialTransferState, ValidatorKind
from rychlik.core.state_store import (
    PersistedRetrySchedule,
    PersistentStateCorruptionError,
    SqliteDownloadStateStore,
    UnsupportedStateSchemaError,
    default_state_db_path,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _store(tmp_path) -> SqliteDownloadStateStore:
    store = SqliteDownloadStateStore(tmp_path / "state.db")
    store.initialize()
    return store


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


# --- schema / permissions / defaults -------------------------------------


def test_default_db_path_uses_xdg_data_home(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/data")
    assert default_state_db_path() == Path("/custom/data/rychlik/state.db")


def test_default_db_path_falls_back_to_local_share(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert default_state_db_path() == Path.home() / ".local" / "share" / "rychlik" / "state.db"


def test_initialize_creates_private_permissions(tmp_path):
    store = _store(tmp_path)
    db_path = tmp_path / "state.db"
    assert db_path.exists()
    assert (db_path.stat().st_mode & 0o777) == 0o600
    assert (tmp_path.stat().st_mode & 0o777) == 0o700


def test_initialize_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.close()
    store2 = SqliteDownloadStateStore(tmp_path / "state.db")
    store2.initialize()  # must not raise / must not wipe existing data
    store2.close()


def test_future_schema_version_refused_without_modifying_db(tmp_path):
    db_path = tmp_path / "state.db"
    store = SqliteDownloadStateStore(db_path)
    store.initialize()
    store.close()

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE metadata SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    before = db_path.read_bytes()
    store2 = SqliteDownloadStateStore(db_path)
    with pytest.raises(UnsupportedStateSchemaError):
        store2.initialize()
    after = db_path.read_bytes()
    assert before == after


# --- round trips -----------------------------------------------------------


def test_task_round_trip(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    store.checkpoint_task_state(task=task)

    loaded = store.load()
    assert loaded.tasks["A"] == task


def test_request_round_trip(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0)
    request = DownloadRequest(url="http://x/y.mp4", destination_dir=Path("/tmp/x"), filename_hint="y.mp4")
    store.checkpoint_task_state(task=task, request=request)

    loaded = store.load()
    assert loaded.requests["A"] == request


def test_queue_round_trip(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0)
    entry = _entry()
    store.checkpoint_task_state(task=task, queue_entry=entry)

    loaded = store.load()
    assert loaded.queue_entries["qe-A"] == entry


def test_failure_round_trip(tmp_path):
    store = _store(tmp_path)
    failure = DownloadTaskFailure(code="NET", message="boom", retryable=True)
    task = (
        create_task("A", now=T0)
        .mark_ready(now=T0)
        .start_transfer(now=T0)
        .wait_for_retry(failure, now=T0)
    )
    store.checkpoint_task_state(task=task)

    loaded = store.load()
    assert loaded.tasks["A"].last_failure == failure


def test_timestamp_round_trip_is_timezone_aware(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0).complete(now=T0)
    store.checkpoint_task_state(task=task)

    loaded = store.load()
    restored = loaded.tasks["A"]
    for field in ("created_at", "updated_at", "started_at", "finished_at"):
        value = getattr(restored, field)
        assert value is not None
        assert value.tzinfo is not None


def test_order_round_trip_complex_queue(tmp_path):
    store = _store(tmp_path)
    entries = [
        _entry(queue_entry_id="h2", task_id="H2", priority=QueuePriority.HIGH, position=1),
        _entry(queue_entry_id="h1", task_id="H1", priority=QueuePriority.HIGH, position=0),
        _entry(queue_entry_id="n3", task_id="N3", priority=QueuePriority.NORMAL, position=0),
        _entry(queue_entry_id="n1", task_id="N1", priority=QueuePriority.NORMAL, position=1),
        _entry(
            queue_entry_id="n2",
            task_id="N2",
            priority=QueuePriority.NORMAL,
            position=2,
            state=QueueEntryState.PAUSED,
            paused_at=T0,
        ),
        _entry(queue_entry_id="l1", task_id="L1", priority=QueuePriority.LOW, position=0),
    ]
    for entry in entries:
        store.checkpoint_task_state(task=create_task(entry.task_id, now=T0), queue_entry=entry)

    loaded = store.load()
    from rychlik.core.download_queue import DownloadQueue

    queue = DownloadQueue.restore(list(loaded.queue_entries.values()))
    assert [e.task_id for e in queue.active_entries()] == ["H1", "H2", "N3", "N1", "N2", "L1"]
    assert queue.get("n2").state == QueueEntryState.PAUSED


def test_retry_schedule_round_trip(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0)
    schedule = PersistedRetrySchedule(
        queue_entry_id="qe-A",
        task_id="A",
        attempt_count_snapshot=1,
        delay_seconds=2.0,
        scheduled_at_utc=T0,
        not_before_utc=T0 + timedelta(seconds=2),
    )
    store.checkpoint_task_state(task=task, queue_entry=_entry(), retry_schedule=schedule)

    loaded = store.load()
    assert loaded.retry_schedules["qe-A"] == schedule


def test_retry_schedule_deletion(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0)
    schedule = PersistedRetrySchedule("qe-A", "A", 1, 2.0, T0, T0 + timedelta(seconds=2))
    store.checkpoint_task_state(task=task, retry_schedule=schedule)
    store.checkpoint_task_state(task=task, delete_retry_schedule_id="qe-A")

    loaded = store.load()
    assert loaded.retry_schedules == {}


# --- corruption / integrity -------------------------------------------------


def test_corrupt_queue_reference_fails_loudly(tmp_path):
    db_path = tmp_path / "state.db"
    store = _store(tmp_path)
    store.close()

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO queue_entries VALUES ('qe-X', 'ghost-task', 'QUEUED', 'NORMAL', 0, ?, ?, NULL)",
        (T0.isoformat(), T0.isoformat()),
    )
    conn.commit()
    conn.close()

    store2 = SqliteDownloadStateStore(db_path)
    store2.initialize()
    with pytest.raises(PersistentStateCorruptionError):
        store2.load()


def test_unknown_enum_value_fails_loudly(tmp_path):
    db_path = tmp_path / "state.db"
    store = _store(tmp_path)
    store.close()

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO download_tasks VALUES ('A', 'FLYING', 0, ?, ?, NULL, NULL, NULL, NULL, NULL)",
        (T0.isoformat(), T0.isoformat()),
    )
    conn.commit()
    conn.close()

    store2 = SqliteDownloadStateStore(db_path)
    store2.initialize()
    with pytest.raises(PersistentStateCorruptionError):
        store2.load()


# --- transaction rollback ----------------------------------------------------


def test_transaction_rollback_preserves_previous_snapshot(tmp_path):
    db_path = tmp_path / "state.db"
    store = _store(tmp_path)
    task_a = create_task("A", now=T0)
    store.checkpoint_task_state(task=task_a)

    class BoomRequest:
        url = ["not", "a", "string"]  # sqlite3 cannot bind a list -> InterfaceError mid-transaction
        destination_dir = Path("/tmp")
        filename_hint = None

    task_b = create_task("B", now=T0)
    with pytest.raises(Exception):
        store.checkpoint_task_state(task=task_b, request=BoomRequest())

    loaded = store.load()
    assert "A" in loaded.tasks
    assert loaded.tasks["A"] == task_a


def test_replace_all_removes_ghost_rows(tmp_path):
    from rychlik.core.state_store import PersistentDownloadState

    store = _store(tmp_path)
    store.checkpoint_task_state(task=create_task("A", now=T0), queue_entry=_entry())

    store.replace_all(
        PersistentDownloadState(tasks={}, requests={}, queue_entries={}, retry_schedules={})
    )
    loaded = store.load()
    assert loaded.tasks == {}
    assert loaded.queue_entries == {}


# --- shutdown marker ---------------------------------------------------------


def test_clean_shutdown_marker_round_trip(tmp_path):
    store = _store(tmp_path)
    assert store.get_previous_shutdown_clean() is False  # fresh DB defaults dirty
    store.mark_clean_shutdown()
    store.close()

    store2 = SqliteDownloadStateStore(tmp_path / "state.db")
    store2.initialize()
    assert store2.get_previous_shutdown_clean() is True


def test_dirty_marker_after_mark_session_dirty(tmp_path):
    store = _store(tmp_path)
    store.mark_clean_shutdown()
    store.mark_session_dirty()
    store.close()

    store2 = SqliteDownloadStateStore(tmp_path / "state.db")
    store2.initialize()
    assert store2.get_previous_shutdown_clean() is False


# --- no progress / no reservation persistence -------------------------------


def test_no_progress_or_reservation_columns_exist(tmp_path):
    import sqlite3

    store = _store(tmp_path)
    conn = sqlite3.connect(store.path)
    for table in ("download_tasks", "queue_entries"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        forbidden = {"bytes_downloaded", "speed_bps", "eta_seconds", "future", "reservation", "worker"}
        assert columns & forbidden == set()
    conn.close()


# --- Prompt A9: partial-transfer round trip + v1->v2 migration ------------


def _partial(**kwargs):
    defaults = dict(
        queue_entry_id="qe-A",
        task_id="A",
        attempt_count_snapshot=1,
        temp_path=Path("/tmp/dest/video.mp4.part"),
        final_path=Path("/tmp/dest/video.mp4"),
        durable_bytes=1024,
        expected_total_bytes=4096,
        validator_kind=ValidatorKind.STRONG_ETAG,
        validator_value='"abc123"',
        prefix_sha256="a" * 64,
        created_at=T0,
        updated_at=T0,
    )
    defaults.update(kwargs)
    return PartialTransferState(**defaults)


def test_partial_transfer_round_trip(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0).mark_ready(now=T0).start_transfer(now=T0)
    partial = _partial()
    store.checkpoint_task_state(task=task, partial_transfer=partial)

    loaded = store.load()
    assert loaded.partial_transfers["qe-A"] == partial


def test_partial_transfer_deletion(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0)
    store.checkpoint_task_state(task=task, partial_transfer=_partial())
    store.checkpoint_task_state(task=task, delete_partial_transfer_id="qe-A")

    loaded = store.load()
    assert loaded.partial_transfers == {}


def test_partial_transfer_with_no_validator(tmp_path):
    store = _store(tmp_path)
    task = create_task("A", now=T0)
    partial = _partial(validator_kind=ValidatorKind.NONE, validator_value=None)
    store.checkpoint_task_state(task=task, partial_transfer=partial)

    loaded = store.load()
    assert loaded.partial_transfers["qe-A"].validator_kind == ValidatorKind.NONE
    assert loaded.partial_transfers["qe-A"].validator_value is None


def test_schema_v1_to_v2_real_migration_preserves_data(tmp_path):
    db_path = tmp_path / "state.db"

    # Build an authentic schema-v1 database using ONLY the four original
    # A8 tables, exactly as A8's SqliteDownloadStateStore would have left
    # it (no partial_transfers table, schema_version=1).
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE download_tasks (
            task_id TEXT PRIMARY KEY, state TEXT NOT NULL, attempt_count INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            last_failure_code TEXT, last_failure_message TEXT, last_failure_retryable INTEGER
        )
        """
    )
    conn.execute(
        "CREATE TABLE download_requests (task_id TEXT PRIMARY KEY, url TEXT NOT NULL, "
        "destination_dir TEXT NOT NULL, filename_hint TEXT)"
    )
    conn.execute(
        "CREATE TABLE queue_entries (queue_entry_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
        "state TEXT NOT NULL, priority TEXT NOT NULL, position INTEGER NOT NULL, "
        "enqueued_at TEXT NOT NULL, updated_at TEXT NOT NULL, paused_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE retry_schedules (queue_entry_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
        "attempt_count_snapshot INTEGER NOT NULL, delay_seconds REAL NOT NULL, "
        "scheduled_at_utc TEXT NOT NULL, not_before_utc TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO metadata VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO metadata VALUES ('clean_shutdown', '1')")
    conn.execute(
        "INSERT INTO download_tasks VALUES ('A', 'READY', 0, ?, ?, NULL, NULL, NULL, NULL, NULL)",
        (T0.isoformat(), T0.isoformat()),
    )
    conn.execute(
        "INSERT INTO queue_entries VALUES ('qe-A', 'A', 'QUEUED', 'NORMAL', 0, ?, ?, NULL)",
        (T0.isoformat(), T0.isoformat()),
    )
    conn.commit()
    conn.close()

    store = SqliteDownloadStateStore(db_path)
    store.initialize()  # real transactional v1 -> v2 migration

    loaded = store.load()
    assert loaded.tasks["A"].state == DownloadTaskState.READY
    assert loaded.queue_entries["qe-A"].task_id == "A"
    assert loaded.partial_transfers == {}  # new table, empty after migration
    assert store.get_previous_shutdown_clean() is True  # A8 metadata preserved

    conn = sqlite3.connect(db_path)
    version = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()[0]
    assert version == "2"
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "partial_transfers" in tables
    conn.close()


# --- structural import test --------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.state_store as module

    forbidden = {"PySide6", "pickle", "shelve"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    top_level = {name.split(".")[0] for name in imported}
    assert top_level & forbidden == set()
