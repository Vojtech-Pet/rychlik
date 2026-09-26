from dataclasses import replace
from datetime import datetime, timezone

import pytest
import requests

from rychlik.core.artifact import Artifact
from rychlik.core.artifact_repository import InMemoryArtifactRepository
from rychlik.share.contracts import ShareStatus
from rychlik.share.local_share_origin import LocalShareOrigin
from rychlik.share.share_link import ShareLink, generate_share_id
from rychlik.share.share_link_repository import InMemoryShareLinkRepository
from rychlik.share.share_preview_service import SharePreviewService
from conftest_ffmpeg import make_test_video, requires_ffmpeg, requires_ffprobe

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PageFixture:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.artifact_repository = InMemoryArtifactRepository()
        self.share_link_repository = InMemoryShareLinkRepository()
        self.preview_service = SharePreviewService(
            artifact_repository=self.artifact_repository, cache_dir=tmp_path / "preview-cache"
        )
        self.origin = LocalShareOrigin(
            share_link_repository=self.share_link_repository,
            artifact_repository=self.artifact_repository,
            share_preview_service=self.preview_service,
        )
        self.origin.start()

    def add_artifact(self, filename: str, body: bytes) -> Artifact:
        path = self.tmp_path / filename
        path.write_bytes(body)
        artifact = Artifact.from_completed_download(path)
        self.artifact_repository.save(artifact)
        return artifact

    def add_link(self, artifact: Artifact, *, status: ShareStatus = ShareStatus.ACTIVE) -> str:
        link = ShareLink(
            share_id=generate_share_id(), artifact_id=artifact.artifact_id, created_at=UTC_NOW, status=status
        )
        self.share_link_repository.save(link)
        return link.share_id

    def set_status(self, share_id: str, status: ShareStatus) -> None:
        link = self.share_link_repository.get(share_id)
        self.share_link_repository.save(replace(link, status=status))

    def close(self):
        self.origin.stop()


@pytest.fixture
def fx(tmp_path):
    fixture = PageFixture(tmp_path)
    yield fixture
    fixture.close()


# --- status mapping ----------------------------------------------------


def test_active_returns_200_html(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 100)
    share_id = fx.add_link(artifact, status=ShareStatus.ACTIVE)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 200
    assert "<html" in response.text
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"


def test_creating_returns_200_preparing(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 100)
    share_id = fx.add_link(artifact, status=ShareStatus.CREATING)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 200
    assert "Preparing" in response.text


def test_offline_returns_503(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 100)
    share_id = fx.add_link(artifact, status=ShareStatus.OFFLINE)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 503
    assert "offline" in response.text


def test_revoked_returns_410(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 100)
    share_id = fx.add_link(artifact, status=ShareStatus.REVOKED)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 410
    assert "revoked" in response.text


def test_expired_returns_410(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 100)
    share_id = fx.add_link(artifact, status=ShareStatus.EXPIRED)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 410
    assert "expired" in response.text


def test_failed_returns_404(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 100)
    share_id = fx.add_link(artifact, status=ShareStatus.FAILED)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 404


def test_unknown_share_returns_404(fx):
    response = requests.get(fx.origin.share_page_url("does-not-exist"))
    assert response.status_code == 404
    assert "does not exist" in response.text


# --- content correctness -------------------------------------------------


def test_utf8_title_renders_correctly(fx):
    artifact = fx.add_artifact("české slovenské video.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.status_code == 200
    assert "české slovenské video" in response.text


def test_html_special_chars_filename_is_escaped(fx):
    # '<' and '>' cannot be created as real filenames in this sandboxed test
    # environment (blocked below the filesystem layer, not by the OS) — the
    # actual <script> injection proof lives in test_share_page_renderer.py,
    # which exercises the exact same render_share_page() this route calls.
    # Here we prove the same escaping happens end-to-end over real HTTP with
    # the HTML-special characters this environment does allow on disk.
    artifact = fx.add_artifact("A&B \"quoted\" 'title'.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert "A&amp;B" in response.text
    assert "&quot;quoted&quot;" in response.text
    assert 'A&B "quoted"' not in response.text  # raw, unescaped form must not appear


def test_no_local_path_or_source_url_in_page(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert str(fx.tmp_path) not in response.text
    assert "source_url" not in response.text


def test_og_title_description_url_present(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert 'property="og:title"' in response.text
    assert 'property="og:description"' in response.text
    assert f'property="og:url" content="{fx.origin.share_page_url(share_id)}"' in response.text


def test_og_image_absent_without_thumbnail(fx):
    # generic file -> SharePreview never generates a thumbnail (Prompt 08)
    artifact = fx.add_artifact("archive.zip", b"pk-fake-zip")
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert "og:image" not in response.text


@requires_ffmpeg
@requires_ffprobe
def test_og_image_present_with_real_thumbnail(tmp_path, fx):
    video_path = make_test_video(tmp_path / "real.mp4", duration=2.0, size="64x64")
    artifact = Artifact.from_completed_download(video_path)
    fx.artifact_repository.save(artifact)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))
    assert f'property="og:image" content="{fx.origin.preview_url(share_id)}"' in response.text

    preview_response = requests.get(fx.origin.preview_url(share_id))
    assert preview_response.status_code == 200
    assert preview_response.headers["Content-Type"] == "image/jpeg"
    assert len(preview_response.content) > 0


def test_robots_noindex_header_and_meta(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex,nofollow">' in response.text


def test_cache_control_no_store(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))
    assert response.headers["Cache-Control"] == "no-store"


def test_security_headers_present(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "script-src 'none'" in response.headers["Content-Security-Policy"]


def test_head_share_page_matches_get_headers_no_body(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.head(fx.origin.share_page_url(share_id))
    assert response.status_code == 200
    assert response.content == b""


# --- preview route -----------------------------------------------------


def test_preview_unknown_share_404(fx):
    response = requests.get(fx.origin.preview_url("does-not-exist"))
    assert response.status_code == 404


def test_preview_denied_for_revoked_share(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact, status=ShareStatus.REVOKED)

    response = requests.get(fx.origin.preview_url(share_id))
    assert response.status_code == 410


def test_preview_404_when_no_thumbnail_exists(fx):
    artifact = fx.add_artifact("archive.zip", b"pk-fake-zip")
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.preview_url(share_id))
    assert response.status_code == 404


@requires_ffmpeg
@requires_ffprobe
def test_preview_denied_media_still_allowed_state_check(tmp_path, fx):
    # CREATING is OK-coded for /preview and /s, but /media stays denied
    # (Prompt 07 policy: media requires ACTIVE specifically).
    video_path = make_test_video(tmp_path / "real2.mp4", duration=1.0, size="32x32")
    artifact = Artifact.from_completed_download(video_path)
    fx.artifact_repository.save(artifact)
    share_id = fx.add_link(artifact, status=ShareStatus.CREATING)

    preview_response = requests.get(fx.origin.preview_url(share_id))
    media_response = requests.get(fx.origin.media_url(share_id))

    assert preview_response.status_code == 200
    assert media_response.status_code == 404


@requires_ffmpeg
@requires_ffprobe
def test_full_stack_page_preview_and_ranged_media(tmp_path, fx):
    """Prompt 09 §41: real local HTTP composition of all Link Mode layers.
    Artifact -> SharePreview -> ShareLink ACTIVE -> LocalShareOrigin ->
    GET /s -> parse HTML -> GET preview URL -> GET media URL with Range."""
    video_path = make_test_video(tmp_path / "full_stack.mp4", duration=2.0, size="64x64")
    video_bytes = video_path.read_bytes()
    artifact = Artifact.from_completed_download(video_path)
    fx.artifact_repository.save(artifact)
    share_id = fx.add_link(artifact, status=ShareStatus.ACTIVE)

    page_response = requests.get(fx.origin.share_page_url(share_id))
    assert page_response.status_code == 200
    html = page_response.text

    assert "<title>full_stack</title>" in html
    assert 'property="og:title" content="full_stack"' in html
    expected_preview_url = fx.origin.preview_url(share_id)
    expected_media_url = fx.origin.media_url(share_id)
    assert expected_preview_url in html
    assert expected_media_url in html

    preview_response = requests.get(expected_preview_url)
    assert preview_response.status_code == 200
    assert preview_response.headers["Content-Type"] == "image/jpeg"
    assert len(preview_response.content) > 0

    ranged_response = requests.get(expected_media_url, headers={"Range": "bytes=0-99"})
    assert ranged_response.status_code == 206
    assert ranged_response.content == video_bytes[:100]


def test_crawler_style_requests_have_no_side_effects(fx):
    """Repeated GET on /s and /preview must never mutate ShareLink state."""
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    for _ in range(5):
        requests.get(fx.origin.share_page_url(share_id))
        requests.get(fx.origin.preview_url(share_id))

    link = fx.share_link_repository.get(share_id)
    assert link.status == ShareStatus.ACTIVE  # unchanged by any of the crawler-style requests


# --- set_base_url (Prompt 13 bug found via live WhatsApp experiment) --------


def test_default_base_url_is_local_address(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    response = requests.get(fx.origin.share_page_url(share_id))

    host, port = fx.origin.address
    assert f'og:url" content="http://{host}:{port}/s/{share_id}"' in response.text
    assert f'src="http://{host}:{port}/media/{share_id}"' in response.text


def test_set_base_url_overrides_og_urls(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)
    host, port = fx.origin.address
    local_page_url = f"http://{host}:{port}/s/{share_id}"

    fx.origin.set_base_url("https://public-tunnel.example")
    response = requests.get(local_page_url)  # request the real local socket; only rendered URLs change

    assert f'og:url" content="https://public-tunnel.example/s/{share_id}"' in response.text
    assert f'src="https://public-tunnel.example/media/{share_id}"' in response.text
    assert "127.0.0.1" not in response.text


def test_set_base_url_also_changes_route_helper_methods(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)

    fx.origin.set_base_url("https://public-tunnel.example")

    assert fx.origin.share_page_url(share_id) == f"https://public-tunnel.example/s/{share_id}"
    assert fx.origin.media_url(share_id) == f"https://public-tunnel.example/media/{share_id}"


def test_set_base_url_none_reverts_to_local_default(fx):
    artifact = fx.add_artifact("clip.mp4", b"x" * 50)
    share_id = fx.add_link(artifact)
    host, port = fx.origin.address

    fx.origin.set_base_url("https://public-tunnel.example")
    fx.origin.set_base_url(None)

    assert fx.origin.share_page_url(share_id) == f"http://{host}:{port}/s/{share_id}"
