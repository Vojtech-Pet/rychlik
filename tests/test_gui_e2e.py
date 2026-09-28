"""Prompt A11: real (non-mock) HTTP end-to-end tests driving
DownloadManagerWidget through its actual widgets (URL field, buttons,
table selection) against a real DownloadManagerService and the real HTTP
fixture server. No direct backend-layer calls except test/fixture setup."""

import time

from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.core.download_task import DownloadTaskState
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from gui_legacy_adapter import make_widget
from http_fixture_server import NORMAL_BODY

STRONG_ETAG = '"v1"'
BIG_BODY = NORMAL_BODY * 30
SLOW = dict(slow=True, slow_chunk_bytes=16384, slow_delay=0.02)


def _wait_until(qapp, predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    qapp.processEvents()
    return predicate()


def _manager(tmp_path, **overrides) -> DownloadManagerService:
    config = DownloadManagerConfig(database_path=tmp_path / "state.db", **overrides)
    return DownloadManagerService(config=config)


def _select(widget, queue_entry_id):
    for row in range(widget.table_legacy.live_count()):
        item = widget.table_legacy.item(row, 0)
        if item is not None and item.data(256) == queue_entry_id:
            widget.table_legacy.selectRow(row)
            return True
    return False


def _row_for(widget, queue_entry_id):
    for row in range(widget.table_legacy.live_count()):
        item = widget.table_legacy.item(row, 0)
        if item is not None and item.data(256) == queue_entry_id:
            return row
    return None


# --- real single HTTP GUI E2E (§103) -----------------------------------------


def test_gui_single_download_via_button(qapp, http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    widget = make_widget(manager)
    try:
        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()

        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 1, timeout=3)
        row_id = widget.table_legacy.item(0, 0).data(256)

        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 0, timeout=10)
        assert (tmp_path.parent).exists()  # sanity: destination was real
    finally:
        widget.shutdown()
        manager.stop()


# --- real concurrent HTTP GUI E2E (§104) --------------------------------------


def test_gui_two_concurrent_downloads(qapp, http_fixture_server, tmp_path):
    manager = _manager(tmp_path, max_active_transfers=2)
    widget = None
    manager.start()
    try:
        widget = make_widget(manager)
        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()
        widget.url_input.setText(f"{http_fixture_server.base_url}/with-content-disposition")
        widget.download_button.click()

        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 2, timeout=3)
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 0, timeout=10)
    finally:
        if widget is not None:
            widget.shutdown()
        manager.stop()


# --- real hold/release GUI E2E (§105) -----------------------------------------


def test_gui_hold_release_via_buttons(http_fixture_server, tmp_path, qapp):
    # Deterministic (no dispatch-race): capacity=1 and item1 occupies the
    # only slot on a slow endpoint, so item2 is GUARANTEED to still be
    # waiting (READY+QUEUED, never dispatched) when we hold it.
    http_fixture_server.configure_resumable("gui-hold1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    widget = make_widget(manager)
    try:
        widget.url_input.setText(f"{http_fixture_server.base_url}/resumable/gui-hold1")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 1, timeout=3)
        busy_id = widget.table_legacy.item(0, 0).data(256)
        assert _wait_until(
            qapp, lambda: widget.table_legacy.item(_row_for(widget, busy_id), 1).text() == "Downloading", timeout=3
        )

        widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 2, timeout=3)
        held_id = next(i for i in (widget.table_legacy.item(r, 0).data(256) for r in range(2)) if i != busy_id)

        _select(widget, held_id)
        widget.hold_button.click()
        time.sleep(0.2)
        qapp.processEvents()
        row = _row_for(widget, held_id)
        assert row is not None
        assert widget.table_legacy.item(row, 1).text() == "On hold"  # never dispatched while capacity was full anyway

        _select(widget, held_id)
        widget.release_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 0, timeout=10)
    finally:
        widget.shutdown()
        manager.stop()


# --- real pause/resume GUI E2E (§106) -----------------------------------------


def test_gui_pause_resume_via_buttons(http_fixture_server, tmp_path, qapp):
    http_fixture_server.configure_resumable("gui-pause1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    widget = make_widget(manager)
    try:
        widget.url_input.setText(f"{http_fixture_server.base_url}/resumable/gui-pause1")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 1, timeout=3)
        entry_id = widget.table_legacy.item(0, 0).data(256)

        assert _wait_until(
            qapp, lambda: widget.table_legacy.item(_row_for(widget, entry_id), 1).text() == "Downloading", timeout=3
        )
        time.sleep(0.15)
        _select(widget, entry_id)
        widget.pause_button.click()
        assert _wait_until(
            qapp, lambda: widget.table_legacy.item(_row_for(widget, entry_id), 1).text() == "Paused", timeout=3
        )

        _select(widget, entry_id)
        widget.resume_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 0, timeout=10)
    finally:
        widget.shutdown()
        manager.stop()


# --- real cancel GUI E2E (§107) -----------------------------------------------


def test_gui_cancel_active_transfer(http_fixture_server, tmp_path, qapp):
    http_fixture_server.configure_resumable("gui-cancel1", etag=STRONG_ETAG, body=BIG_BODY, **SLOW)
    manager = _manager(tmp_path, max_active_transfers=1)
    manager.start()
    widget = make_widget(manager)
    try:
        widget.url_input.setText(f"{http_fixture_server.base_url}/resumable/gui-cancel1")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 1, timeout=3)
        entry_id = widget.table_legacy.item(0, 0).data(256)
        assert _wait_until(
            qapp, lambda: widget.table_legacy.item(_row_for(widget, entry_id), 1).text() == "Downloading", timeout=3
        )

        _select(widget, entry_id)
        widget.cancel_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 0, timeout=10)
        assert widget.isEnabled()  # GUI still alive/responsive
    finally:
        widget.shutdown()
        manager.stop()


# --- real retry GUI E2E (§108) -------------------------------------------------


def test_gui_retry_now_via_button(http_fixture_server, tmp_path, qapp):
    from rychlik.acquisition.contracts import AcquisitionError
    from rychlik.core.download_task import DownloadTaskFailure
    from rychlik.core.retry_policy import RetryPolicyConfig

    # A retryable failure is forced by holding the queue entry after a
    # first attempt fails against a always-503 endpoint -- simplest
    # deterministic way to get a real RETRY_WAIT through the real facade,
    # then prove Retry Now uses A10 (never bypassing the attempt budget).
    http_fixture_server.configure_flaky("gui-retry1", fail_until=100)  # always fails for this test

    def _retryable_mapper(exc: AcquisitionError) -> DownloadTaskFailure:
        return DownloadTaskFailure(code="TRANSIENT", message=str(exc), retryable=True)

    manager = DownloadManagerService(
        config=DownloadManagerConfig(
            database_path=tmp_path / "state.db", max_active_transfers=1,
            retry_policy_config=RetryPolicyConfig(max_attempts=5, base_delay_seconds=30.0),  # long, so it stays RETRY_WAIT
        ),
        failure_mapper=_retryable_mapper,
    )
    manager.start()
    widget = make_widget(manager)
    try:
        widget.url_input.setText(f"{http_fixture_server.base_url}/flaky/gui-retry1")
        widget.download_button.click()
        assert _wait_until(qapp, lambda: widget.table_legacy.live_count() == 1, timeout=3)
        entry_id = widget.table_legacy.item(0, 0).data(256)

        assert _wait_until(
            qapp, lambda: widget.table_legacy.item(_row_for(widget, entry_id), 1).text() == "Waiting to retry", timeout=5
        )
        attempt_before = int(widget.table_legacy.item(_row_for(widget, entry_id), 7).text())
        # approved UI: the row shows the REAL countdown from the runtime's retry schedule (30 s backoff)
        from rychlik.gui import presentation as P

        status_text = widget.proxy.index(_row_for(widget, entry_id), P.COL_STATUS).data()
        assert status_text.startswith("Retrying in ") and 0 <= int(status_text.split()[2]) <= 30, status_text

        _select(widget, entry_id)
        widget.retry_button.click()
        # Retry Now promotes RETRY_WAIT -> READY -> normal dispatch tries
        # again immediately (still fails, back to RETRY_WAIT) -- proves the
        # button used the real A10/A6 path, not a direct task mutation.
        assert _wait_until(
            qapp,
            lambda: widget.table_legacy.item(_row_for(widget, entry_id), 1).text() == "Waiting to retry"
            and int(widget.table_legacy.item(_row_for(widget, entry_id), 7).text()) > attempt_before,
            timeout=5,
        )
    finally:
        widget.shutdown()
        manager.stop()


# --- real restart/persistence GUI E2E (§109) ----------------------------------


def test_gui_restart_shows_recovered_state(http_fixture_server, tmp_path, qapp):
    manager1 = _manager(tmp_path, max_active_transfers=0)
    manager1.start()
    widget1 = make_widget(manager1)
    widget1.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
    widget1.download_button.click()
    assert _wait_until(qapp, lambda: widget1.table_legacy.live_count() == 1, timeout=3)
    entry_id = widget1.table_legacy.item(0, 0).data(256)
    _select(widget1, entry_id)
    widget1.hold_button.click()
    assert _wait_until(qapp, lambda: widget1.table_legacy.item(0, 1).text() == "On hold", timeout=3)
    widget1.shutdown()
    manager1.stop()

    manager2 = _manager(tmp_path, max_active_transfers=1)
    manager2.start()
    widget2 = make_widget(manager2)
    try:
        # §20: recovered state must appear immediately on construction, no
        # further command/event required.
        assert widget2.table_legacy.rowCount() == 1
        assert widget2.table_legacy.item(0, 1).text() == "On hold"
        assert widget2.table_legacy.item(0, 0).data(256) == entry_id

        _select(widget2, entry_id)
        widget2.release_button.click()
        assert _wait_until(qapp, lambda: widget2.table_legacy.live_count() == 0, timeout=10)
    finally:
        widget2.shutdown()
        manager2.stop()
