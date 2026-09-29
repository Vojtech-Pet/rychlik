"""VideoFetchService: real yt-dlp against a local HTTP server (no internet), private cache, purge, cancel."""

from __future__ import annotations

import threading
import time

import pytest

from rychlik.acquisition.media_ytdlp import known_video_page
from rychlik.sharing.video_fetch import FetchedVideo, VideoFetchCancelled, VideoFetchError, VideoFetchService
from tests.http_fixture_server import NORMAL_BODY


@pytest.fixture(autouse=True)
def _no_real_youtube(monkeypatch):
    # known_video_page() only inspects the URL (no network); real fetches in these tests go against the local
    # fixture server, so it must be recognised without actually being YouTube -- patch it to accept our test URLs.
    monkeypatch.setattr("rychlik.sharing.video_fetch.known_video_page", lambda url: "/normal.mp4" in url or "/slow" in url or "/notfound" in url)


def test_fetch_downloads_into_the_private_cache_dir_not_the_default_downloads_dir(http_fixture_server, tmp_path):
    service = VideoFetchService(tmp_path / "cache")
    video = service.fetch(f"{http_fixture_server.base_url}/normal.mp4")
    assert isinstance(video, FetchedVideo)
    assert video.path.parent == tmp_path / "cache"
    assert video.path.read_bytes() == NORMAL_BODY
    assert video.size == len(NORMAL_BODY)
    assert video.mime_type == "video/mp4"


def test_a_link_yt_dlp_does_not_recognise_is_rejected_before_any_fetch(tmp_path):
    service = VideoFetchService(tmp_path / "cache")
    with pytest.raises(VideoFetchError, match="does not look like a link to a video"):
        service.fetch("https://example.test/plain-page")
    assert not (tmp_path / "cache").exists()


def test_cancel_stops_the_fetch_and_raises_cancelled(http_fixture_server, tmp_path):
    service = VideoFetchService(tmp_path / "cache")
    cancel = threading.Event()
    seen = []

    def progress(done, total):
        seen.append(done)
        if done > 0:
            cancel.set()

    with pytest.raises(VideoFetchCancelled):
        service.fetch(f"{http_fixture_server.base_url}/slow", progress_callback=progress, cancel_event=cancel)
    assert seen


def test_old_cached_files_are_purged_on_the_next_fetch(http_fixture_server, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    stale = cache / "leftover-from-a-previous-send.mp4"
    stale.write_bytes(b"old")
    old_time = time.time() - 3600
    import os

    os.utime(stale, (old_time, old_time))
    service = VideoFetchService(cache, max_age_seconds=1.0)
    service.fetch(f"{http_fixture_server.base_url}/normal.mp4")
    assert not stale.exists()  # purged because it was older than max_age_seconds


def test_known_video_page_still_the_real_one_for_actual_recognised_sites():
    assert known_video_page("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert known_video_page("https://example.test/x.zip") is False
