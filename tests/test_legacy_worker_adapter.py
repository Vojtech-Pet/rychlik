"""M3: legacy `download(worker)` modules become typed DownloadRequests; nothing is downloaded by the module."""

from __future__ import annotations

import threading
import types
from pathlib import Path

import pytest

from rychlik.acquisition.contracts import DownloadRequest, MediaOptions
from rychlik.modules.legacy_adapter import (
    MultipleAcquisitions, NoRequestProduced, RequiresManagedProcessBridge, ResolutionCancelled, ResolutionError,
    requires_process_bridge, resolve_with_legacy_module,
)

LEGACY_DIR = Path("/home/vojtech/Stiahnuté/rychlik-downloader/download_modules")
IN_SCOPE_RESOLVERS = ["xvideos", "xnxx", "eporner", "tgtube", "shemalez", "pornhub", "ashemaletube", "shemaletubevideos", "ladyboygold", "trannyvideosx"]


def _module(download):
    return types.SimpleNamespace(can_handle=lambda url: True, download=download)


def test_run_media_becomes_a_typed_request_with_referer_format_and_rate_limit(tmp_path):
    def download(worker):
        worker.item.url = "https://cdn.example/v.m3u8"
        worker.item.referrer = "https://site.example/page"
        worker.item.video_format = "best[height<=720]"
        worker.item.speed_limit = 250_000
        worker.item.display_name = "My Video"
        worker.update(worker.item)
        worker._run_media()

    result = resolve_with_legacy_module(_module(download), "https://site.example/page", tmp_path)
    assert result.request == DownloadRequest(url="https://cdn.example/v.m3u8", destination_dir=tmp_path, filename_hint="My Video",
                                             media=MediaOptions(video_format="best[height<=720]", referer="https://site.example/page", rate_limit_bytes_per_second=250_000))


def test_run_media_does_not_start_any_acquisition(tmp_path, monkeypatch):
    import rychlik.acquisition.media_ytdlp as media

    monkeypatch.setattr(media.MediaAcquisition, "acquire", lambda *a, **k: pytest.fail("the adapter must never download"))

    def download(worker):
        worker._run_media()

    resolve_with_legacy_module(_module(download), "https://site.example/v", tmp_path)


def test_exactly_one_run_media_is_required(tmp_path):
    def none(worker):
        worker.update(worker.item)

    def twice(worker):
        worker._run_media()
        worker._run_media()

    with pytest.raises(NoRequestProduced):
        resolve_with_legacy_module(_module(none), "https://site.example/v", tmp_path)
    with pytest.raises(MultipleAcquisitions):
        resolve_with_legacy_module(_module(twice), "https://site.example/v", tmp_path)


@pytest.mark.parametrize("bad_url", ["", "javascript:alert(1)", "file:///etc/passwd", "ftp://x/y", "notaurl"])
def test_invalid_resolved_urls_are_rejected(tmp_path, bad_url):
    def download(worker):
        worker.item.url = bad_url
        worker._run_media()

    with pytest.raises(ResolutionError):
        resolve_with_legacy_module(_module(download), "https://site.example/v", tmp_path)


def test_invalid_referer_and_empty_format_are_rejected(tmp_path):
    def bad_referer(worker):
        worker.item.referrer = "javascript:1"
        worker._run_media()

    def empty_format(worker):
        worker.item.video_format = "  "
        worker._run_media()

    for fn in (bad_referer, empty_format):
        with pytest.raises(ResolutionError):
            resolve_with_legacy_module(_module(fn), "https://site.example/v", tmp_path)


@pytest.mark.parametrize("name,expected", [("../../etc/passwd", "passwd"), ("a/b\\c.mp4", "c.mp4"), ("x:y?.mp4", "x_y_.mp4"), ("..", None), ("Video zo stránky", None), ("", None)])
def test_filenames_are_sanitised_or_dropped(tmp_path, name, expected):
    def download(worker):
        worker.item.display_name = name
        worker._run_media()

    assert resolve_with_legacy_module(_module(download), "https://site.example/v", tmp_path).request.filename_hint == expected


def test_cancel_before_or_during_resolve_produces_no_request(tmp_path):
    cancel = threading.Event()

    def cancels_midway(worker):
        cancel.set()
        worker._run_media()

    with pytest.raises(ResolutionCancelled):
        resolve_with_legacy_module(_module(cancels_midway), "https://site.example/v", tmp_path, cancel_event=cancel)

    cancel2 = threading.Event()
    cancel2.set()  # set after the module returned normally, before the result is accepted
    def quiet(worker):
        worker._run_media()

    with pytest.raises(ResolutionCancelled):
        resolve_with_legacy_module(_module(quiet), "https://site.example/v", tmp_path, cancel_event=cancel2)


def test_update_becomes_normalised_events_and_odd_fields_become_diagnostics(tmp_path):
    def download(worker):
        worker.item.status = "Analyzuje stránku"
        worker.update(worker.item)
        worker.item.auth_browser = "firefox"
        worker.item.destination = "/somewhere/else"
        worker.item.mystery = 1
        worker._run_media()

    result = resolve_with_legacy_module(_module(download), "https://site.example/v", tmp_path)
    assert [e.status for e in result.events] == ["Analyzuje stránku"]
    assert any("auth_browser" in d for d in result.diagnostics) and any("destination" in d for d in result.diagnostics)
    assert any("mystery" in d for d in result.diagnostics)
    assert result.request.destination_dir == tmp_path  # the module cannot redirect the download


def test_mutating_the_legacy_item_after_run_media_does_not_change_the_request(tmp_path):
    holder = {}

    def download(worker):
        worker.item.url = "https://cdn.example/a.mp4"
        worker._run_media()
        holder["worker"] = worker
        worker.item.url = "https://evil.example/b.mp4"
        worker.item.referrer = "https://evil.example/"

    result = resolve_with_legacy_module(_module(download), "https://site.example/v", tmp_path)
    assert result.request.url == "https://cdn.example/a.mp4" and result.request.media.referer is None


def test_a_module_that_raises_is_a_resolution_error_not_a_crash(tmp_path):
    def download(worker):
        raise RuntimeError("site changed")

    with pytest.raises(ResolutionError, match="site changed"):
        resolve_with_legacy_module(_module(download), "https://site.example/v", tmp_path)


def test_a_module_that_starts_a_process_is_refused_and_its_process_killed(tmp_path):
    killed = []

    class FakeProcess:
        pid = 2**30  # no such process group

        def kill(self):
            killed.append(True)

    adapter_module = types.SimpleNamespace(can_handle=lambda u: True, download=lambda w: w._set_process(FakeProcess()))
    with pytest.raises(RequiresManagedProcessBridge):
        resolve_with_legacy_module(adapter_module, "https://site.example/v", tmp_path)
    assert killed


@pytest.mark.skipif(not LEGACY_DIR.is_dir(), reason="legacy modules folder not present")
def test_shez_tube_is_recognised_as_needing_the_process_bridge_and_is_not_run(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("legacy_shez_tube", LEGACY_DIR / "shez_tube.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert requires_process_bridge(module)
    with pytest.raises(RequiresManagedProcessBridge):
        resolve_with_legacy_module(module, "https://shemalez.tube/videos/123", tmp_path)


@pytest.mark.skipif(not LEGACY_DIR.is_dir(), reason="legacy modules folder not present")
@pytest.mark.parametrize("name", IN_SCOPE_RESOLVERS)
def test_the_ten_real_resolver_modules_load_and_do_not_need_the_process_bridge(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"legacy_{name}", LEGACY_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.can_handle) and callable(module.download) and not requires_process_bridge(module)


@pytest.mark.skipif(not LEGACY_DIR.is_dir(), reason="legacy modules folder not present")
@pytest.mark.parametrize("name,url", [("xvideos", "https://www.xvideos.com/video.abc123/some-title"), ("xnxx", "https://www.xnxx.com/video-abc123/some_title"),
                                       ("eporner", "https://www.eporner.com/video-abc/some-title/")])
def test_real_normalise_only_modules_yield_a_media_request_without_network(name, url, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"legacy_{name}", LEGACY_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.can_handle(url):
        pytest.skip(f"{name} does not recognise the sample URL shape")
    result = resolve_with_legacy_module(module, url, tmp_path)
    assert result.request.media is not None and result.request.url.startswith("https://") and result.request.destination_dir == tmp_path


def test_fake_legacy_module_through_the_real_queue_and_media_backend_to_an_artifact(http_fixture_server, tmp_path):
    import time

    from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
    from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed
    from tests.http_fixture_server import NORMAL_BODY

    def download(worker):  # a legacy-shaped resolver: find the media address, then hand over to "media"
        worker.item.status = "Analyzuje"
        worker.update(worker.item)
        worker.item.url = f"{http_fixture_server.base_url}/normal.mp4"
        worker.item.referrer = "https://site.example/"
        worker.item.display_name = "resolved.mp4"
        worker._run_media()

    resolved = resolve_with_legacy_module(_module(download), "https://site.example/page", tmp_path / "dl")
    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "s.db", max_active_transfers=1))
    manager.start()
    try:
        added = manager.add_download(resolved.request)
        end = time.monotonic() + 20
        while time.monotonic() < end and any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items):
            time.sleep(0.05)
        artifact, _ = build_artifact_for_completed(manager, added.queue_entry_id)
        assert artifact is not None and artifact.local_path.read_bytes() == NORMAL_BODY and artifact.filename == "resolved.mp4" and artifact.sha256
    finally:
        manager.stop()
