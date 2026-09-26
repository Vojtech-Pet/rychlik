"""Durable download-manager state store (Prompt A8).

Answers exactly one question, deterministically:

    Which facts about DownloadTask/QueueEntry/DownloadRequest/retry
    schedules must survive an application restart, and how are they
    written/read as plain SQLite rows -- never as a pickled Python object
    graph, never guessing an unknown persisted value?

It does NOT decide what a recovered state actually means for a live
runtime (that is rychlik.core.restart_recovery.RestartRecovery), does not
run the scheduler/dispatch loop, and never imports PySide6/requests/Qt.

Durable vs. runtime-only (see docs/PERSISTENT_DOWNLOAD_STATE.md for the
full rationale): DownloadTask identity/lifecycle/attempt_count/timestamps/
last_failure, DownloadRequest, QueueEntry identity/state/priority/
position, and A6 retry-schedule UTC deadlines are durable. A5 reservations/
Futures/threads and A7 progress telemetry (bytes/speed/ETA/sample history)
are never written here -- they belong to exactly one process lifetime.

Uses only the stdlib `sqlite3` module. No ORM, no pickle, no external
database.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_queue import QueueEntry, QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTask, DownloadTaskFailure, DownloadTaskState, restore_task

SCHEMA_VERSION = 1

_SCHEMA_VERSION_KEY = "schema_version"
_CLEAN_SHUTDOWN_KEY = "clean_shutdown"

_DIR_MODE = 0o700
_DB_MODE = 0o600


class PersistentStateError(Exception):
    """Base type for all durable-state-store errors."""


class UnsupportedStateSchemaError(PersistentStateError):
    """Raised when the on-disk schema_version is newer than this runtime
    supports (§10). The database is never overwritten or downgraded when
    this is raised."""


class PersistentStateCorruptionError(PersistentStateError):
    """Raised for structurally inconsistent persisted rows: an unknown enum
    value, a queue_entry/retry_schedule referencing a nonexistent task, or
    any other fact that must fail loudly rather than be silently discarded
    or guessed (§62/§109/§110)."""


@dataclass(frozen=True)
class PersistedRetrySchedule:
    """Durable cross-process approximation of A6's in-memory _RetrySchedule
    (§15/§17). `due_monotonic` is deliberately NOT part of this shape --
    monotonic timestamps have meaning only inside one process lifetime."""

    queue_entry_id: str
    task_id: str
    attempt_count_snapshot: int
    delay_seconds: float
    scheduled_at_utc: datetime
    not_before_utc: datetime

    def __post_init__(self) -> None:
        if self.scheduled_at_utc.tzinfo is None:
            raise ValueError("scheduled_at_utc must be timezone-aware")
        if self.not_before_utc.tzinfo is None:
            raise ValueError("not_before_utc must be timezone-aware")


@dataclass(frozen=True)
class PersistentDownloadState:
    """The full durable snapshot -- not the A7 GUI snapshot
    (DownloadManagerSnapshot) and not a live runtime object graph."""

    tasks: dict[str, DownloadTask]
    requests: dict[str, DownloadRequest]
    queue_entries: dict[str, QueueEntry]
    retry_schedules: dict[str, PersistedRetrySchedule]


def default_state_db_path() -> Path:
    """$XDG_DATA_HOME/rychlik/state.db, falling back to
    ~/.local/share/rychlik/state.db (§4). Tests must always pass an
    explicit temporary path instead of calling this."""
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "rychlik" / "state.db"


def _to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _from_utc_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise PersistentStateCorruptionError(f"persisted timestamp is not timezone-aware: {value!r}")
    return parsed


def _enum_name(value) -> str:
    return value.name


def _parse_enum(enum_cls, name: str, *, context: str):
    try:
        return enum_cls[name]
    except KeyError as exc:
        raise PersistentStateCorruptionError(
            f"unknown {enum_cls.__name__} value {name!r} in {context}"
        ) from exc


class SqliteDownloadStateStore:
    """One persistence owner (§26). Never becomes a scheduler/runtime/
    download executor -- it only knows how to read and write durable rows."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        # A5 workers (checkpoint_task_state) and the controller/main thread
        # (load/mark_*) may call into this store from different threads
        # (§70/§71): one small dedicated lock serializes all connection use.
        # It participates in no other lock's ordering -- it is never held
        # while the A5 state lock or A7 progress lock is held by this module
        # (callers hold their own locks around their own call, not this one).
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # --- lifecycle ----------------------------------------------------

    def initialize(self) -> None:
        """Idempotent: safe to call on a fresh path or an existing database.
        Creates the state directory/file with user-private permissions
        (§5), applies durability PRAGMAs (§24/§25), and validates the
        schema version BEFORE touching any row (§9/§10)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, _DIR_MODE)
        except OSError:
            pass  # best-effort on filesystems that don't support POSIX perms

        is_new = not self._path.exists()
        conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
        self._conn = conn

        try:
            os.chmod(self._path, _DB_MODE)
        except OSError:
            pass

        with self._transaction():
            conn.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS download_tasks (
                    task_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_failure_code TEXT,
                    last_failure_message TEXT,
                    last_failure_retryable INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS download_requests (
                    task_id TEXT PRIMARY KEY REFERENCES download_tasks(task_id),
                    url TEXT NOT NULL,
                    destination_dir TEXT NOT NULL,
                    filename_hint TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_entries (
                    queue_entry_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    enqueued_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    paused_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retry_schedules (
                    queue_entry_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_count_snapshot INTEGER NOT NULL,
                    delay_seconds REAL NOT NULL,
                    scheduled_at_utc TEXT NOT NULL,
                    not_before_utc TEXT NOT NULL
                )
                """
            )

            if is_new:
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES (?, ?)",
                    (_SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
                )
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES (?, ?)", (_CLEAN_SHUTDOWN_KEY, "0")
                )

        self._validate_schema_version()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _validate_schema_version(self) -> None:
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (_SCHEMA_VERSION_KEY,)
        ).fetchone()
        if row is None:
            raise PersistentStateCorruptionError("state database is missing schema_version metadata")
        on_disk_version = int(row["value"])
        if on_disk_version > SCHEMA_VERSION:
            raise UnsupportedStateSchemaError(
                f"state database schema_version={on_disk_version} is newer than this runtime "
                f"supports (schema_version={SCHEMA_VERSION}); refusing to touch it"
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # --- shutdown marker (§20-22) ---------------------------------------

    def get_previous_shutdown_clean(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (_CLEAN_SHUTDOWN_KEY,)
            ).fetchone()
        return row is not None and row["value"] == "1"

    def mark_session_dirty(self) -> None:
        with self._transaction():
            self._conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, '0') "
                "ON CONFLICT(key) DO UPDATE SET value = '0'",
                (_CLEAN_SHUTDOWN_KEY,),
            )

    def mark_clean_shutdown(self) -> None:
        with self._transaction():
            self._conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'",
                (_CLEAN_SHUTDOWN_KEY,),
            )

    # --- incremental checkpoint (§67, §79-86) ---------------------------

    def checkpoint_task_state(
        self,
        *,
        task: DownloadTask,
        request: DownloadRequest | None = None,
        queue_entry: QueueEntry | None = None,
        delete_queue_entry_id: str | None = None,
        retry_schedule: PersistedRetrySchedule | None = None,
        delete_retry_schedule_id: str | None = None,
    ) -> None:
        """One SQLite transaction (§23/§67) writing exactly the rows a single
        durable lifecycle event touches. Never called per A7 progress chunk
        (§74) -- only on lifecycle/queue events."""
        with self._transaction():
            self._upsert_task(task)
            if request is not None:
                self._upsert_request(task.task_id, request)
            if queue_entry is not None:
                self._upsert_queue_entry(queue_entry)
            if delete_queue_entry_id is not None:
                self._conn.execute(
                    "DELETE FROM queue_entries WHERE queue_entry_id = ?", (delete_queue_entry_id,)
                )
            if retry_schedule is not None:
                self._upsert_retry_schedule(retry_schedule)
            if delete_retry_schedule_id is not None:
                self._conn.execute(
                    "DELETE FROM retry_schedules WHERE queue_entry_id = ?",
                    (delete_retry_schedule_id,),
                )

    def _upsert_task(self, task: DownloadTask) -> None:
        failure = task.last_failure
        self._conn.execute(
            """
            INSERT INTO download_tasks
                (task_id, state, attempt_count, created_at, updated_at, started_at,
                 finished_at, last_failure_code, last_failure_message, last_failure_retryable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                state = excluded.state,
                attempt_count = excluded.attempt_count,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                last_failure_code = excluded.last_failure_code,
                last_failure_message = excluded.last_failure_message,
                last_failure_retryable = excluded.last_failure_retryable
            """,
            (
                task.task_id,
                _enum_name(task.state),
                task.attempt_count,
                _to_utc_iso(task.created_at),
                _to_utc_iso(task.updated_at),
                _to_utc_iso(task.started_at) if task.started_at else None,
                _to_utc_iso(task.finished_at) if task.finished_at else None,
                failure.code if failure else None,
                failure.message if failure else None,
                (1 if failure.retryable else 0) if failure else None,
            ),
        )

    def _upsert_request(self, task_id: str, request: DownloadRequest) -> None:
        self._conn.execute(
            """
            INSERT INTO download_requests (task_id, url, destination_dir, filename_hint)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                url = excluded.url,
                destination_dir = excluded.destination_dir,
                filename_hint = excluded.filename_hint
            """,
            (task_id, request.url, str(request.destination_dir), request.filename_hint),
        )

    def _upsert_queue_entry(self, entry: QueueEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO queue_entries
                (queue_entry_id, task_id, state, priority, position, enqueued_at, updated_at, paused_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(queue_entry_id) DO UPDATE SET
                task_id = excluded.task_id,
                state = excluded.state,
                priority = excluded.priority,
                position = excluded.position,
                enqueued_at = excluded.enqueued_at,
                updated_at = excluded.updated_at,
                paused_at = excluded.paused_at
            """,
            (
                entry.queue_entry_id,
                entry.task_id,
                _enum_name(entry.state),
                _enum_name(entry.priority),
                entry.position,
                _to_utc_iso(entry.enqueued_at),
                _to_utc_iso(entry.updated_at),
                _to_utc_iso(entry.paused_at) if entry.paused_at else None,
            ),
        )

    def _upsert_retry_schedule(self, schedule: PersistedRetrySchedule) -> None:
        self._conn.execute(
            """
            INSERT INTO retry_schedules
                (queue_entry_id, task_id, attempt_count_snapshot, delay_seconds,
                 scheduled_at_utc, not_before_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(queue_entry_id) DO UPDATE SET
                task_id = excluded.task_id,
                attempt_count_snapshot = excluded.attempt_count_snapshot,
                delay_seconds = excluded.delay_seconds,
                scheduled_at_utc = excluded.scheduled_at_utc,
                not_before_utc = excluded.not_before_utc
            """,
            (
                schedule.queue_entry_id,
                schedule.task_id,
                schedule.attempt_count_snapshot,
                schedule.delay_seconds,
                _to_utc_iso(schedule.scheduled_at_utc),
                _to_utc_iso(schedule.not_before_utc),
            ),
        )

    # --- whole-snapshot checkpoint (§67/§68), used for initial save and
    # for persisting recovery's canonical post-recovery state (§88 step 8) --

    def replace_all(self, state: PersistentDownloadState) -> None:
        """Replaces the entire durable snapshot in one transaction (§68):
        rows no longer present in `state` do not survive as ghost live
        state."""
        with self._transaction():
            self._conn.execute("DELETE FROM retry_schedules")
            self._conn.execute("DELETE FROM queue_entries")
            self._conn.execute("DELETE FROM download_requests")
            self._conn.execute("DELETE FROM download_tasks")
            for task in state.tasks.values():
                self._upsert_task(task)
            for task_id, request in state.requests.items():
                self._upsert_request(task_id, request)
            for entry in state.queue_entries.values():
                self._upsert_queue_entry(entry)
            for schedule in state.retry_schedules.values():
                self._upsert_retry_schedule(schedule)

    # --- load (§88 step 5, §109/§110) -----------------------------------

    def load(self) -> PersistentDownloadState:
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> PersistentDownloadState:
        tasks: dict[str, DownloadTask] = {}
        for row in self._conn.execute("SELECT * FROM download_tasks"):
            state = _parse_enum(DownloadTaskState, row["state"], context=f"download_tasks.task_id={row['task_id']!r}")
            failure = None
            if row["last_failure_code"] is not None:
                failure = DownloadTaskFailure(
                    code=row["last_failure_code"],
                    message=row["last_failure_message"] or "",
                    retryable=bool(row["last_failure_retryable"]),
                )
            tasks[row["task_id"]] = restore_task(
                row["task_id"],
                state=state,
                created_at=_from_utc_iso(row["created_at"]),
                updated_at=_from_utc_iso(row["updated_at"]),
                started_at=_from_utc_iso(row["started_at"]),
                finished_at=_from_utc_iso(row["finished_at"]),
                attempt_count=row["attempt_count"],
                last_failure=failure,
            )

        requests: dict[str, DownloadRequest] = {}
        for row in self._conn.execute("SELECT * FROM download_requests"):
            requests[row["task_id"]] = DownloadRequest(
                url=row["url"],
                destination_dir=Path(row["destination_dir"]),
                filename_hint=row["filename_hint"],
            )

        queue_entries: dict[str, QueueEntry] = {}
        for row in self._conn.execute("SELECT * FROM queue_entries"):
            if row["task_id"] not in tasks:
                raise PersistentStateCorruptionError(
                    f"queue_entry {row['queue_entry_id']!r} references unknown task_id "
                    f"{row['task_id']!r}"
                )
            queue_entries[row["queue_entry_id"]] = QueueEntry(
                queue_entry_id=row["queue_entry_id"],
                task_id=row["task_id"],
                state=_parse_enum(
                    QueueEntryState, row["state"], context=f"queue_entries.queue_entry_id={row['queue_entry_id']!r}"
                ),
                priority=_parse_enum(
                    QueuePriority,
                    row["priority"],
                    context=f"queue_entries.queue_entry_id={row['queue_entry_id']!r}",
                ),
                position=row["position"],
                enqueued_at=_from_utc_iso(row["enqueued_at"]),
                updated_at=_from_utc_iso(row["updated_at"]),
                paused_at=_from_utc_iso(row["paused_at"]),
            )

        retry_schedules: dict[str, PersistedRetrySchedule] = {}
        for row in self._conn.execute("SELECT * FROM retry_schedules"):
            if row["task_id"] not in tasks:
                raise PersistentStateCorruptionError(
                    f"retry_schedule {row['queue_entry_id']!r} references unknown task_id "
                    f"{row['task_id']!r}"
                )
            retry_schedules[row["queue_entry_id"]] = PersistedRetrySchedule(
                queue_entry_id=row["queue_entry_id"],
                task_id=row["task_id"],
                attempt_count_snapshot=row["attempt_count_snapshot"],
                delay_seconds=row["delay_seconds"],
                scheduled_at_utc=_from_utc_iso(row["scheduled_at_utc"]),
                not_before_utc=_from_utc_iso(row["not_before_utc"]),
            )

        return PersistentDownloadState(
            tasks=tasks, requests=requests, queue_entries=queue_entries, retry_schedules=retry_schedules
        )
