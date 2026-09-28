"""M2: the yt-dlp MediaAcquisition backend, run for real (real yt-dlp against a local HTTP server; no internet)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("yt_dlp")

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import (
    AcquisitionError, DownloadCancelled, DownloadPaused, DownloadRequest, MediaOptions,
)
from rychlik.acquisition.media_ytdlp import MediaAcquisition, output_template, reject_non_media
from tests.http_fixture_server import NORMAL_BODY


def _req(base, path, dest, **kw):
    return DownloadRequest(url=f"{base}{path}", destination_dir=dest, media=MediaOptions(), **kw)


def test_downloads_a_direct_media_url_with_progress(http_fixture_server, tmp_path):
    seen: list[tuple[int, int | None]] = []
    done = MediaAcquisition().acquire(_req(http_fixture_server.base_url, "/normal.mp4", tmp_path, filename_hint="holiday.mp4"),
                                      progress_callback=lambda got, total: seen.append((got, total)))
    assert done.final_path == tmp_path / "holiday.mp4" and done.final_path.read_bytes() == NORMAL_BODY
    assert done.size == len(NORMAL_BODY) and done.display_name == "holiday.mp4"
    assert seen and seen[-1] == (len(NORMAL_BODY), len(NORMAL_BODY))


def test_service_routes_media_requests_to_the_media_backend_and_plain_requests_to_http(http_fixture_server, tmp_path):
    calls = []

    class Spy:
        def __init__(self, name):
            self.name = name

        def acquire(self, request, **kw):
            calls.append(self.name)
            raise AcquisitionError("stop")

    service = AcquisitionService(http_backend=Spy("http"), media_backend=Spy("media"))
    for req in (DownloadRequest(url="http://x/a.mp4", destination_dir=tmp_path), DownloadRequest(url="http://x/a", destination_dir=tmp_path, media=MediaOptions())):
        with pytest.raises(AcquisitionError):
            service.acquire(req)
    assert calls == ["http", "media"]


def test_download_with_a_referer_option_still_completes(http_fixture_server, tmp_path):
    req = DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="r.mp4",
                          media=MediaOptions(referer="https://example.test/page"))
    MediaAcquisition().acquire(req)
    assert (tmp_path / "r.mp4").exists()


def test_html_page_is_rejected_not_saved_as_video(http_fixture_server, tmp_path):
    with pytest.raises(AcquisitionError):
        MediaAcquisition().acquire(_req(http_fixture_server.base_url, "/notfound", tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_cancel_stops_the_run_and_removes_partial_files(http_fixture_server, tmp_path):
    cancel = threading.Event()
    got = []

    def progress(n, total):
        got.append(n)
        if n > 0:
            cancel.set()

    with pytest.raises(DownloadCancelled):
        MediaAcquisition().acquire(_req(http_fixture_server.base_url, "/slow", tmp_path, filename_hint="c.mp4"), progress_callback=progress, cancel_event=cancel)
    assert got and list(tmp_path.iterdir()) == []


def test_pause_keeps_the_partial_and_a_second_attempt_completes(http_fixture_server, tmp_path):
    pause = threading.Event()

    def progress(n, total):
        if n > 0:
            pause.set()

    request = _req(http_fixture_server.base_url, "/slow", tmp_path, filename_hint="p.mp4")
    with pytest.raises(DownloadPaused):
        MediaAcquisition().acquire(request, progress_callback=progress, pause_event=pause)
    assert not (tmp_path / "p.mp4").exists()  # not complete
    pause.clear()
    done = MediaAcquisition().acquire(request, pause_event=pause)
    assert done.final_path.read_bytes() == NORMAL_BODY


def test_output_template_sanitises_and_escapes():
    assert output_template("a/b:c?.mp4") == "b_c_.%(ext)s"  # directory parts are dropped, so no path traversal
    assert output_template("../../evil.mp4") == "evil.%(ext)s"
    assert output_template("100%.mp4") == "100%%.%(ext)s"
    assert output_template(None) == "%(title).180B.%(ext)s"
    assert output_template("...") == "%(title).180B.%(ext)s"


def test_non_media_filter_matches_the_legacy_rules():
    assert reject_non_media({"ext": "html"}) is not None
    assert reject_non_media({"ext": "exe", "protocol": "https"}) is not None
    assert reject_non_media({"ext": "unknown_video", "protocol": "m3u8_native"}) is None
    assert reject_non_media({"ext": "mp4"}) is None
    assert reject_non_media({"ext": "exe"}, incomplete=True) is None


def test_media_options_validation():
    with pytest.raises(ValueError):
        MediaOptions(video_format=" ")
    with pytest.raises(ValueError):
        MediaOptions(rate_limit_bytes_per_second=0)


def _wait(pred, timeout=20.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_manager_queue_runs_a_media_request_end_to_end_and_pause_resume_cancel_work(http_fixture_server, tmp_path):
    from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService

    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", max_active_transfers=1))
    manager.start()
    try:
        base = http_fixture_server.base_url
        # completes through the real queue, into the dispatcher's own filename policy
        done = manager.add_download(_req(base, "/normal.mp4", tmp_path / "dl", filename_hint="whole.mp4"))
        assert _wait(lambda: (tmp_path / "dl" / "whole.mp4").exists() and not any(i.queue_entry_id == done.queue_entry_id for i in manager.snapshot().items))
        assert (tmp_path / "dl" / "whole.mp4").read_bytes() == NORMAL_BODY

        # pause -> PAUSED (transfer slot released), resume -> completes with the same bytes
        slow = manager.add_download(_req(base, "/slow", tmp_path / "dl", filename_hint="slow.mp4"))
        assert _wait(lambda: any(i.queue_entry_id == slow.queue_entry_id and i.bytes_downloaded for i in manager.snapshot().items))
        assert manager.pause_transfer(slow.queue_entry_id).status.name in ("APPLIED", "ACCEPTED")
        assert _wait(lambda: any(i.queue_entry_id == slow.queue_entry_id and "PAUSED" in str(i.task_state).upper() for i in manager.snapshot().items))
        assert manager.resume_transfer(slow.queue_entry_id).status.name in ("APPLIED", "ACCEPTED")
        assert _wait(lambda: (tmp_path / "dl" / "slow.mp4").exists() and not any(i.queue_entry_id == slow.queue_entry_id for i in manager.snapshot().items))
        assert (tmp_path / "dl" / "slow.mp4").read_bytes() == NORMAL_BODY

        # cancel mid-transfer -> gone from the queue, no leftovers
        gone = manager.add_download(_req(base, "/slow", tmp_path / "dl", filename_hint="gone.mp4"))
        assert _wait(lambda: any(i.queue_entry_id == gone.queue_entry_id and i.bytes_downloaded for i in manager.snapshot().items))
        assert manager.cancel(gone.queue_entry_id).status.name in ("APPLIED", "ACCEPTED")
        assert _wait(lambda: not any(i.queue_entry_id == gone.queue_entry_id for i in manager.snapshot().items))
        assert _wait(lambda: not list((tmp_path / "dl").glob("gone*")))
    finally:
        manager.stop()
