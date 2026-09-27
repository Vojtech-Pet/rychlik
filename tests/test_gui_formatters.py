from rychlik.core.download_queue import QueueEntryState, QueuePriority
from rychlik.core.download_task import DownloadTaskState
from rychlik.gui.formatters import (
    derive_status_text,
    format_bytes,
    format_downloaded_total,
    format_eta,
    format_priority,
    format_progress,
    format_speed,
)


def test_format_bytes_none():
    assert format_bytes(None) == "—"


def test_format_bytes_small():
    assert format_bytes(42) == "42 B"


def test_format_bytes_kb_mb_gb():
    assert format_bytes(1500) == "1.5 KB"
    assert format_bytes(52_000_000) == "52.0 MB"
    assert format_bytes(3_200_000_000) == "3.2 GB"


def test_format_speed_none_and_zero():
    assert format_speed(None) == "—"
    assert format_speed(0) == "0 B/s"


def test_format_speed_scales():
    assert format_speed(850_000) == "850.0 KB/s"
    assert format_speed(4_200_000) == "4.2 MB/s"


def test_format_eta_none():
    assert format_eta(None) == "—"


def test_format_eta_zero():
    assert format_eta(0) == "0 s"


def test_format_eta_seconds():
    assert format_eta(8) == "8 s"


def test_format_eta_minutes():
    assert format_eta(84) == "1m 24s"


def test_format_eta_hours():
    assert format_eta(7500) == "2h 05m"


def test_format_progress_none_never_shows_zero():
    assert format_progress(None) == "—"


def test_format_progress_values():
    assert format_progress(0) == "0 %"
    assert format_progress(0.5) == "50 %"
    assert format_progress(1.0) == "100 %"


def test_format_downloaded_total_unknown_total():
    assert format_downloaded_total(52_000_000, None) == "52.0 MB / —"


def test_format_downloaded_total_known():
    assert format_downloaded_total(52_000_000, 100_000_000) == "52.0 MB / 100.0 MB"


def test_format_priority_labels():
    assert format_priority(QueuePriority.HIGH) == "High"
    assert format_priority(QueuePriority.NORMAL) == "Normal"
    assert format_priority(QueuePriority.LOW) == "Low"


def test_status_ready_queued_is_waiting():
    assert derive_status_text(DownloadTaskState.READY, QueueEntryState.QUEUED) == "Waiting"


def test_status_ready_paused_queue_is_on_hold():
    assert derive_status_text(DownloadTaskState.READY, QueueEntryState.PAUSED) == "On hold"


def test_status_transferring():
    assert derive_status_text(DownloadTaskState.TRANSFERRING, QueueEntryState.QUEUED) == "Downloading"


def test_status_task_paused():
    assert derive_status_text(DownloadTaskState.PAUSED, QueueEntryState.QUEUED) == "Paused"


def test_status_retry_wait():
    assert derive_status_text(DownloadTaskState.RETRY_WAIT, QueueEntryState.QUEUED) == "Waiting to retry"


def test_status_completed_failed_cancelled():
    assert derive_status_text(DownloadTaskState.COMPLETED, QueueEntryState.REMOVED) == "Completed"
    assert derive_status_text(DownloadTaskState.FAILED, QueueEntryState.REMOVED) == "Failed"
    assert derive_status_text(DownloadTaskState.CANCELLED, QueueEntryState.REMOVED) == "Cancelled"
