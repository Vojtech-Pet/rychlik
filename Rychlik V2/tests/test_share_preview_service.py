import json
from datetime import datetime, timezone

import pytest

from rychlik.core.artifact import Artifact
from rychlik.core.artifact_repository import InMemoryArtifactRepository
from rychlik.share.share_preview import MediaKind
from rychlik.share.share_preview_service import SharePreviewService
from conftest_ffmpeg import make_test_video, requires_ffmpeg, requires_ffprobe

FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _repo_with_artifact(tmp_path, filename="clip.mp4", body=b"x"):
    file_path = tmp_path / filename
    file_path.write_bytes(body)
    artifact = Artifact.from_completed_download(file_path)
    repo = InMemoryArtifactRepository()
    repo.save(artifact)
    return repo, artifact


def _service(tmp_path, artifact_repository, **overrides):
    kwargs = dict(
        artifact_repository=artifact_repository,
        cache_dir=tmp_path / "cache",
        clock=lambda: FIXED_NOW,
    )
    kwargs.update(overrides)
    return SharePreviewService(**kwargs)


# --- basic generation without real video tooling ---------------------------


def test_unknown_artifact_returns_none(tmp_path):
    repo = InMemoryArtifactRepository()
    service = _service(tmp_path, repo)

    assert service.get_or_create_preview("does-not-exist") is None


def test_generic_file_preview_has_no_thumbnail(tmp_path):
    repo, artifact = _repo_with_artifact(tmp_path, filename="archive.zip", body=b"pk-fake-zip-bytes")
    service = _service(tmp_path, repo)

    preview = service.get_or_create_preview(artifact.artifact_id)

    assert preview is not None
    assert preview.media_kind == MediaKind.GENERIC_FILE
    assert preview.thumbnail_path is None
    assert "File" in preview.description


def test_pdf_preview_has_no_thumbnail(tmp_path):
    repo, artifact = _repo_with_artifact(tmp_path, filename="doc.pdf", body=b"%PDF-fake")
    service = _service(tmp_path, repo)

    preview = service.get_or_create_preview(artifact.artifact_id)

    assert preview.media_kind == MediaKind.DOCUMENT
    assert preview.thumbnail_path is None
    assert preview.description == "PDF document"


# --- artifact mutation ------------------------------------------------------


def test_deleted_artifact_returns_none(tmp_path):
    repo, artifact = _repo_with_artifact(tmp_path)
    service = _service(tmp_path, repo)

    artifact.local_path.unlink()

    assert service.get_or_create_preview(artifact.artifact_id) is None


def test_changed_size_artifact_returns_none(tmp_path):
    repo, artifact = _repo_with_artifact(tmp_path, body=b"original-bytes")
    service = _service(tmp_path, repo)

    artifact.local_path.write_bytes(b"replaced-with-different-length-content")

    assert service.get_or_create_preview(artifact.artifact_id) is None


def test_new_artifact_for_changed_file_gets_independent_cache(tmp_path):
    repo, artifact_v1 = _repo_with_artifact(tmp_path, body=b"version-one")
    service = _service(tmp_path, repo)
    preview_v1 = service.get_or_create_preview(artifact_v1.artifact_id)

    file_path = artifact_v1.local_path
    file_path.write_bytes(b"version-two-different-content")
    artifact_v2 = Artifact.from_completed_download(file_path)
    repo.save(artifact_v2)

    preview_v2 = service.get_or_create_preview(artifact_v2.artifact_id)

    assert artifact_v1.sha256 != artifact_v2.sha256
    assert preview_v1.artifact_id != preview_v2.artifact_id


# --- cache behavior (using real video so thumbnail caching is exercised) ---


@requires_ffmpeg
@requires_ffprobe
def test_first_generation_creates_cache_files(tmp_path):
    repo = InMemoryArtifactRepository()
    video_path = make_test_video(tmp_path / "clip.mp4", duration=2.0, size="64x64")
    artifact = Artifact.from_completed_download(video_path)
    repo.save(artifact)
    service = _service(tmp_path, repo)

    preview = service.get_or_create_preview(artifact.artifact_id)

    meta_path = tmp_path / "cache" / artifact.sha256 / "preview-v1.json"
    thumb_path = tmp_path / "cache" / artifact.sha256 / "preview-v1.jpg"
    assert meta_path.is_file()
    assert thumb_path.is_file()
    assert preview.thumbnail_path == thumb_path
    assert preview.duration_seconds == pytest.approx(2.0, abs=0.3)
    assert preview.width == 64
    assert preview.height == 64


@requires_ffmpeg
@requires_ffprobe
def test_second_call_reuses_cache_without_reprobing(tmp_path, monkeypatch):
    repo = InMemoryArtifactRepository()
    video_path = make_test_video(tmp_path / "clip.mp4", duration=2.0, size="64x64")
    artifact = Artifact.from_completed_download(video_path)
    repo.save(artifact)
    service = _service(tmp_path, repo)

    first = service.get_or_create_preview(artifact.artifact_id)

    calls = []
    import rychlik.share.share_preview_service as module

    monkeypatch.setattr(module, "probe_media", lambda *a, **k: calls.append(1) or None)
    monkeypatch.setattr(module, "generate_thumbnail", lambda *a, **k: calls.append(1) or True)

    second = service.get_or_create_preview(artifact.artifact_id)

    assert calls == []  # cache hit: neither probe nor thumbnail generation ran
    assert second.title == first.title
    assert second.thumbnail_path == first.thumbnail_path


@requires_ffmpeg
@requires_ffprobe
def test_missing_cached_thumbnail_file_triggers_regeneration(tmp_path):
    repo = InMemoryArtifactRepository()
    video_path = make_test_video(tmp_path / "clip.mp4", duration=2.0, size="64x64")
    artifact = Artifact.from_completed_download(video_path)
    repo.save(artifact)
    service = _service(tmp_path, repo)

    first = service.get_or_create_preview(artifact.artifact_id)
    first.thumbnail_path.unlink()

    second = service.get_or_create_preview(artifact.artifact_id)

    assert second.thumbnail_path.is_file()


def test_invalid_cache_json_triggers_regeneration(tmp_path):
    repo, artifact = _repo_with_artifact(tmp_path, filename="archive.zip", body=b"pk-fake-zip-bytes")
    service = _service(tmp_path, repo)
    service.get_or_create_preview(artifact.artifact_id)

    meta_path = tmp_path / "cache" / artifact.sha256 / "preview-v1.json"
    meta_path.write_text("{not valid json")

    preview = service.get_or_create_preview(artifact.artifact_id)
    assert preview is not None
    assert meta_path.read_text()  # regenerated, valid again


def test_preview_version_bump_invalidates_old_cache(tmp_path):
    repo, artifact = _repo_with_artifact(tmp_path, filename="archive.zip", body=b"pk-fake-zip-bytes")
    service = _service(tmp_path, repo)
    service.get_or_create_preview(artifact.artifact_id)

    meta_path = tmp_path / "cache" / artifact.sha256 / "preview-v1.json"
    data = json.loads(meta_path.read_text())
    data["preview_version"] = 999  # simulate an old/foreign version stamp
    meta_path.write_text(json.dumps(data))

    preview = service.get_or_create_preview(artifact.artifact_id)
    assert preview.preview_version == 1  # regenerated under the current version


# --- non-mock vertical E2E ---------------------------------------------------


@requires_ffmpeg
@requires_ffprobe
def test_non_mock_vertical_e2e(tmp_path):
    """Local acquisition fixture -> CompletedDownload -> Artifact ->
    ArtifactRepository -> SharePreviewService -> SharePreview with a real
    generated thumbnail. No HTTP public page involved."""
    from rychlik.acquisition.acquisition_service import AcquisitionService
    from rychlik.acquisition.contracts import DownloadRequest
    from http_fixture_server import HttpFixtureServer

    # Serve a real tiny video fixture over the local HTTP test server so
    # acquisition goes through the real DirectHttpAcquisition path.
    video_source = make_test_video(tmp_path / "source.mp4", duration=2.0, size="64x64")

    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = video_source.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        dest_dir = tmp_path / "downloaded"
        completed = AcquisitionService().acquire(
            DownloadRequest(url=f"http://{host}:{port}/video.mp4", destination_dir=dest_dir)
        )
        artifact = Artifact.from_completed_download(completed.final_path, source_url=completed.source_url)

        artifact_repository = InMemoryArtifactRepository()
        artifact_repository.save(artifact)

        service = SharePreviewService(
            artifact_repository=artifact_repository, cache_dir=tmp_path / "preview-cache"
        )
        preview = service.get_or_create_preview(artifact.artifact_id)

        assert preview is not None
        assert preview.media_kind == MediaKind.VIDEO
        assert preview.thumbnail_path is not None
        assert preview.thumbnail_path.is_file()
        assert preview.thumbnail_path.stat().st_size > 0
        assert preview.duration_seconds == pytest.approx(2.0, abs=0.3)

        public = preview.to_public_dict()
        assert "thumbnail_path" not in public
        assert str(tmp_path) not in json.dumps(public)
        assert "source_url" not in public
    finally:
        server.shutdown()
        server.server_close()
