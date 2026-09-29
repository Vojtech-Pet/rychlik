"""Prompt A10: DownloadManagerService event/notification model -- subscribe/
unsubscribe, callback-safe-to-call-snapshot, bad-subscriber isolation, and
real progress/completion event delivery from the background event pump."""

import threading
import time

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import (
    DownloadManagerConfig,
    DownloadManagerService,
    ManagerEventKind,
)
from http_fixture_server import NORMAL_BODY

BIG_BODY = NORMAL_BODY * 30
STRONG_ETAG = '"v1"'
SLOW = dict(slow=True, slow_chunk_bytes=16384, slow_delay=0.02)


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _manager(tmp_path, **overrides) -> DownloadManagerService:
    config = DownloadManagerConfig(database_path=tmp_path / "state.db", progress_event_interval_seconds=0.05, **overrides)
    return DownloadManagerService(config=config)


def _request(tmp_path, server, name="video"):
    return DownloadRequest(url=f"{server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint=name)


def test_subscribe_receives_download_added_event(tmp_path, http_fixture_server):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        events = []
        manager.subscribe(events.append)
        result = manager.add_download(_request(tmp_path, http_fixture_server))
        assert any(
            e.kind == ManagerEventKind.DOWNLOAD_ADDED and e.queue_entry_id == result.queue_entry_id for e in events
        )
    finally:
        manager.stop()


def test_unsubscribe_stops_delivery(tmp_path, http_fixture_server):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        events = []
        token = manager.subscribe(events.append)
        manager.unsubscribe(token)
        manager.add_download(_request(tmp_path, http_fixture_server))
        assert events == []
    finally:
        manager.stop()


def test_callback_can_safely_call_snapshot_no_deadlock(tmp_path, http_fixture_server):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        results = []

        def on_event(event):
            results.append(manager.snapshot())

        manager.subscribe(on_event)
        manager.add_download(_request(tmp_path, http_fixture_server))
        assert len(results) >= 1  # if this returns at all, no deadlock occurred
    finally:
        manager.stop()


def test_bad_subscriber_does_not_break_others_or_runtime(tmp_path, http_fixture_server):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        good_events = []

        def bad(event):
            raise RuntimeError("boom")

        def good(event):
            good_events.append(event)

        manager.subscribe(bad)
        manager.subscribe(good)
        manager.add_download(_request(tmp_path, http_fixture_server))  # must not raise
        assert len(good_events) >= 1
        assert manager.state.name == "RUNNING"
    finally:
        manager.stop()


def test_real_completion_event_delivered(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        events = []
        manager.subscribe(events.append)
        result = manager.add_download(_request(tmp_path, http_fixture_server))
        assert _wait_until(
            lambda: any(e.kind == ManagerEventKind.COMPLETED and e.queue_entry_id == result.queue_entry_id for e in events),
            timeout=10,
        )
    finally:
        manager.stop()


def test_real_pause_event_delivered(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("evt-pause1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        events = []
        manager.subscribe(events.append)
        result = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/evt-pause1", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: manager.item_snapshot(result.queue_entry_id).task_state.name == "TRANSFERRING", timeout=3
        )
        time.sleep(0.15)
        manager.pause_transfer(result.queue_entry_id)
        assert _wait_until(
            lambda: any(e.kind == ManagerEventKind.PAUSED and e.queue_entry_id == result.queue_entry_id for e in events),
            timeout=3,
        )
    finally:
        manager.stop()


def test_progress_events_are_coalesced_not_flooded(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("evt-progress1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        events = []
        manager.subscribe(events.append)
        manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/evt-progress1", destination_dir=tmp_path, filename_hint="video")
        )
        time.sleep(0.6)  # transfer takes ~0.6s total (510KB @ 16KB/20ms)
        progress_events = [e for e in events if e.kind == ManagerEventKind.PROGRESS_CHANGED]
        # Bounded by the pump interval (~0.05s), not by the number of real
        # 64KiB network chunks received (which would be many more).
        assert 0 < len(progress_events) <= 30
    finally:
        manager.stop()


def test_no_thread_leak_with_active_subscribers(tmp_path, http_fixture_server):
    before = threading.active_count()
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    manager.subscribe(lambda e: None)
    manager.add_download(_request(tmp_path, http_fixture_server))
    time.sleep(0.2)
    manager.stop()
    assert threading.active_count() <= before
