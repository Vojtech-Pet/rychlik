"""Final GUI/UX implementation: additive history snapshot + privacy-safe display fields."""

import time
from pathlib import Path

import pytest

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.core.download_queue import QueueEntryState
from rychlik.core.download_task import DownloadTaskState
from rychlik.core.download_view import safe_name_from_url, source_host_from_url


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _manager(tmp_path, **kw) -> DownloadManagerService:
    return DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", **kw))


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/files/holiday.mp4?token=SECRET#frag", "holiday.mp4"),
        ("https://example.com/a/b/My%20Video.mkv", "My Video.mkv"),
        ("https://example.com/", None),
        ("https://example.com/dir/", None),
        ("https://example.com/..%2F..%2Fetc%2Fpasswd", "passwd"),  # directory parts never survive
        ("https://example.com/evil%00name.bin", "evilname.bin"),  # control characters dropped
    ],
)
def test_safe_name_from_url_is_display_only_and_never_a_path(url, expected):
    assert safe_name_from_url(url) == expected


def test_source_host_never_carries_userinfo_path_or_query():
    assert source_host_from_url("https://user:pw@mirror.example.org:8443/x/y?k=v") == "mirror.example.org"
    assert source_host_from_url("http://[::1]/x") == "::1"


def test_live_snapshot_exposes_host_added_at_and_derived_name_but_not_the_url(tmp_path):
    manager = _manager(tmp_path, max_active_transfers=0)
    manager.start()
    try:
        added = manager.add_download(DownloadRequest(url="https://u:pw@cdn.example.net/a/video.mp4?sig=SECRET", destination_dir=tmp_path))
        item = manager.item_snapshot(added.queue_entry_id)
        assert item.display_name == "video.mp4" and item.source_host == "cdn.example.net"
        assert item.added_at is not None
        assert "SECRET" not in repr(item) and "pw" not in repr(item).replace("pending", "")
    finally:
        manager.stop()


def test_default_snapshot_still_excludes_finished_and_history_snapshot_includes_them(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        done = manager.add_download(DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="done.mp4"))
        assert _wait_until(lambda: not any(i.queue_entry_id == done.queue_entry_id for i in manager.snapshot().items))
        assert manager.snapshot().items == ()  # unchanged default contract

        failed = manager.add_download(DownloadRequest(url=f"{http_fixture_server.base_url}/notfound", destination_dir=tmp_path, filename_hint="nf.bin"))
        assert _wait_until(lambda: not any(i.queue_entry_id == failed.queue_entry_id for i in manager.snapshot().items), timeout=20)

        history = manager.snapshot(include_history=True)
        by_id = {i.queue_entry_id: i for i in history.items}
        assert by_id[done.queue_entry_id].task_state == DownloadTaskState.COMPLETED
        assert by_id[done.queue_entry_id].queue_state == QueueEntryState.REMOVED
        assert by_id[done.queue_entry_id].display_name == "done.mp4"
        assert by_id[done.queue_entry_id].progress_fraction == 1.0 and by_id[done.queue_entry_id].total_bytes > 0
        assert by_id[failed.queue_entry_id].task_state == DownloadTaskState.FAILED
        # active count / aggregate speed only ever describe live transfers
        assert history.active_transfer_count == 0
    finally:
        manager.stop()


def test_history_is_newest_first_bounded_and_keeps_live_items_first(http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    try:
        ids = []
        for n in range(3):
            added = manager.add_download(DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint=f"f{n}.mp4"))
            ids.append(added.queue_entry_id)
            assert _wait_until(lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items))
        waiting = manager.add_download(DownloadRequest(url="http://127.0.0.1:9/never", destination_dir=tmp_path, filename_hint="live.bin"))
        manager.hold(waiting.queue_entry_id)
        history = manager.snapshot(include_history=True)
        assert history.items[0].queue_entry_id == waiting.queue_entry_id  # live first
        assert [i.queue_entry_id for i in history.items[1:]] == list(reversed(ids))  # newest finished first
        limited = manager.snapshot(include_history=True, history_limit=2)
        assert len(limited.items) == 3 and limited.items[0].queue_entry_id == waiting.queue_entry_id
        assert manager.snapshot(include_history=True, history_limit=0).items[0].queue_entry_id == waiting.queue_entry_id
        assert len(manager.snapshot(include_history=True, history_limit=0).items) == 1
    finally:
        manager.stop()


def test_retry_wait_exposes_the_real_remaining_backoff(http_fixture_server, tmp_path):
    from rychlik.core.retry_policy import RetryPolicyConfig

    from rychlik.core.download_task import DownloadTaskFailure

    # The production mapper is conservatively non-retryable for every failure, so a real
    # RETRY_WAIT needs an injected mapper (same approach as tests/test_retry_runtime_e2e.py).
    manager = DownloadManagerService(
        config=DownloadManagerConfig(
            database_path=tmp_path / "state.db", max_active_transfers=1,
            retry_policy_config=RetryPolicyConfig(max_attempts=3, base_delay_seconds=30.0, max_delay_seconds=30.0),
        ),
        failure_mapper=lambda exc: DownloadTaskFailure(code="TRANSIENT_HTTP", message=str(exc), retryable=True),
    )
    manager.start()
    try:
        http_fixture_server.configure_flaky("gui-retry-countdown", fail_until=1)
        added = manager.add_download(DownloadRequest(url=f"{http_fixture_server.base_url}/flaky/gui-retry-countdown", destination_dir=tmp_path, filename_hint="e.bin"))
        assert _wait_until(lambda: (i := manager.item_snapshot(added.queue_entry_id)) is not None and i.task_state == DownloadTaskState.RETRY_WAIT, timeout=15)
        first = manager.item_snapshot(added.queue_entry_id).retry_in_seconds
        assert first is not None and 20.0 < first <= 30.0
        time.sleep(0.3)
        second = manager.snapshot().items[0].retry_in_seconds
        assert second < first  # counts down with real (monotonic) time
        # retry_now clears it: the item is READY again, no countdown
        manager.retry_now(added.queue_entry_id)
        assert manager.item_snapshot(added.queue_entry_id).retry_in_seconds is None
    finally:
        manager.stop()
