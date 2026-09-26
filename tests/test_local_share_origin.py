from datetime import datetime, timezone

import pytest
import requests

from rychlik.core.artifact import Artifact
from rychlik.core.artifact_repository import InMemoryArtifactRepository
from rychlik.share.contracts import ShareStatus
from rychlik.share.local_share_origin import LocalShareOrigin
from rychlik.share.share_link import ShareLink, generate_share_id
from rychlik.share.share_link_repository import InMemoryShareLinkRepository

KNOWN_BYTES = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # 36 bytes
UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Fixture:
    """One origin server + repos + one active share, for a whole test."""

    def __init__(self, tmp_path, body: bytes = KNOWN_BYTES, filename: str = "clip.mp4"):
        file_path = tmp_path / filename
        file_path.write_bytes(body)

        self.artifact = Artifact.from_completed_download(file_path)
        self.artifact_repository = InMemoryArtifactRepository()
        self.artifact_repository.save(self.artifact)

        self.share_link_repository = InMemoryShareLinkRepository()
        self.link = ShareLink(
            share_id=generate_share_id(),
            artifact_id=self.artifact.artifact_id,
            created_at=UTC_NOW,
            status=ShareStatus.ACTIVE,
        )
        self.share_link_repository.save(self.link)

        self.origin = LocalShareOrigin(
            share_link_repository=self.share_link_repository,
            artifact_repository=self.artifact_repository,
        )
        self.origin.start()

    def url(self, share_id: str | None = None) -> str:
        return self.origin.media_url(share_id or self.link.share_id)

    def set_status(self, status: ShareStatus) -> None:
        from dataclasses import replace

        self.share_link_repository.save(replace(self.link, status=status))

    def close(self) -> None:
        self.origin.stop()


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path)
    yield fixture
    fixture.close()


# --- basic GET/HEAD -----------------------------------------------------


def test_head_returns_metadata_no_body(fx):
    response = requests.head(fx.url())

    assert response.status_code == 200
    assert response.headers["Content-Length"] == str(len(KNOWN_BYTES))
    assert response.headers["Content-Type"] == "video/mp4"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert "ETag" in response.headers
    assert response.content == b""


def test_get_full_file(fx):
    response = requests.get(fx.url())

    assert response.status_code == 200
    assert response.content == KNOWN_BYTES
    assert response.headers["Content-Length"] == str(len(KNOWN_BYTES))


def test_etag_stable_across_requests(fx):
    first = requests.head(fx.url()).headers["ETag"]
    second = requests.head(fx.url()).headers["ETag"]
    assert first == second


# --- Range ----------------------------------------------------------------


def test_get_single_byte_range(fx):
    response = requests.get(fx.url(), headers={"Range": "bytes=0-0"})

    assert response.status_code == 206
    assert response.content == b"0"
    assert response.headers["Content-Range"] == f"bytes 0-0/{len(KNOWN_BYTES)}"


def test_get_middle_range(fx):
    response = requests.get(fx.url(), headers={"Range": "bytes=10-19"})

    assert response.status_code == 206
    assert response.content == KNOWN_BYTES[10:20]
    assert response.headers["Content-Length"] == "10"


def test_get_open_ended_range(fx):
    response = requests.get(fx.url(), headers={"Range": "bytes=30-"})

    assert response.status_code == 206
    assert response.content == KNOWN_BYTES[30:]


def test_get_suffix_range(fx):
    response = requests.get(fx.url(), headers={"Range": "bytes=-5"})

    assert response.status_code == 206
    assert response.content == KNOWN_BYTES[-5:]


def test_416_beyond_eof(fx):
    size = len(KNOWN_BYTES)
    response = requests.get(fx.url(), headers={"Range": f"bytes={size}-{size + 10}"})

    assert response.status_code == 416
    assert response.headers["Content-Range"] == f"bytes */{size}"


def test_multiple_sequential_range_requests(fx):
    first = requests.get(fx.url(), headers={"Range": "bytes=0-4"})
    second = requests.get(fx.url(), headers={"Range": "bytes=5-9"})

    assert first.content == KNOWN_BYTES[0:5]
    assert second.content == KNOWN_BYTES[5:10]


# --- share status access control -----------------------------------------


@pytest.mark.parametrize(
    "status,expected_code",
    [
        (ShareStatus.CREATING, 404),
        (ShareStatus.OFFLINE, 503),
        (ShareStatus.EXPIRED, 410),
        (ShareStatus.REVOKED, 410),
        (ShareStatus.FAILED, 404),
    ],
)
def test_denied_statuses(fx, status, expected_code):
    fx.set_status(status)
    response = requests.get(fx.url())
    assert response.status_code == expected_code


def test_active_is_allowed(fx):
    fx.set_status(ShareStatus.ACTIVE)
    response = requests.get(fx.url())
    assert response.status_code == 200


# --- unknown / malformed share ids, no directory listing, traversal -------


def test_unknown_share_id_404(fx):
    response = requests.get(fx.origin.media_url("does-not-exist"))
    assert response.status_code == 404


def test_root_path_404(fx):
    host, port = fx.origin.address
    response = requests.get(f"http://{host}:{port}/")
    assert response.status_code == 404


def test_media_root_no_listing(fx):
    host, port = fx.origin.address
    response = requests.get(f"http://{host}:{port}/media/")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "traversal_id",
    [
        "..%2Fetc%2Fpasswd",
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ],
)
def test_path_traversal_attempts_rejected(fx, traversal_id):
    host, port = fx.origin.address
    response = requests.get(f"http://{host}:{port}/media/{traversal_id}")
    assert response.status_code == 404


def test_path_traversal_literal_segments_rejected(fx):
    host, port = fx.origin.address
    response = requests.get(f"http://{host}:{port}/media/../etc/passwd")
    assert response.status_code == 404


def test_absolute_path_style_id_rejected(fx):
    host, port = fx.origin.address
    response = requests.get(f"http://{host}:{port}/media//etc/passwd")
    assert response.status_code == 404


# --- artifact mutation -----------------------------------------------------


def test_artifact_file_deleted_after_activation(fx):
    fx.artifact.local_path.unlink()
    response = requests.get(fx.url())
    assert response.status_code == 410


def test_artifact_file_truncated(fx):
    fx.artifact.local_path.write_bytes(KNOWN_BYTES[:5])
    response = requests.get(fx.url())
    assert response.status_code == 410


def test_artifact_file_replaced_with_different_size(fx):
    fx.artifact.local_path.write_bytes(KNOWN_BYTES + b"extra-bytes-appended")
    response = requests.get(fx.url())
    assert response.status_code == 410


def test_artifact_missing_from_repository(fx):
    fx.artifact_repository._artifacts.clear()
    response = requests.get(fx.url())
    assert response.status_code == 410


# --- zero-length file -------------------------------------------------------


def test_zero_length_file_get(tmp_path):
    fixture = Fixture(tmp_path, body=b"", filename="empty.mp4")
    try:
        response = requests.get(fixture.url())
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["Content-Length"] == "0"
    finally:
        fixture.close()


def test_zero_length_file_range_is_416(tmp_path):
    fixture = Fixture(tmp_path, body=b"", filename="empty.mp4")
    try:
        response = requests.get(fixture.url(), headers={"Range": "bytes=0-0"})
        assert response.status_code == 416
    finally:
        fixture.close()


# --- larger file, multi-chunk range correctness ------------------------------


def test_range_spanning_multiple_internal_chunks(tmp_path):
    # _CHUNK_SIZE is 64 KiB; use a range that crosses several chunk boundaries.
    body = bytes((i % 256) for i in range(300_000))
    fixture = Fixture(tmp_path, body=body, filename="large.bin")
    try:
        response = requests.get(fixture.url(), headers={"Range": "bytes=50000-200000"})
        assert response.status_code == 206
        assert response.content == body[50000:200001]
        assert len(response.content) == 150001
    finally:
        fixture.close()


def test_full_large_file_get_is_byte_exact(tmp_path):
    body = bytes((i % 256) for i in range(300_000))
    fixture = Fixture(tmp_path, body=body, filename="large.bin")
    try:
        response = requests.get(fixture.url())
        assert response.content == body
    finally:
        fixture.close()


# --- server lifecycle -------------------------------------------------------


def test_double_start_is_idempotent(fx):
    address_before = fx.origin.address
    fx.origin.start()
    assert fx.origin.address == address_before
    assert requests.get(fx.url()).status_code == 200


def test_double_stop_is_safe(fx):
    fx.origin.stop()
    fx.origin.stop()  # must not raise; fixture teardown calls stop() again too


def test_binds_to_localhost_only(fx):
    host, _port = fx.origin.address
    assert host == "127.0.0.1"


# --- real non-mock local E2E -------------------------------------------------


def test_real_end_to_end_head_and_range_get(tmp_path):
    """Acquisition -> Artifact -> ShareLink -> activate -> LocalShareOrigin
    -> real TCP/HTTP HEAD and Range GET (not an in-process fake request)."""
    from rychlik.acquisition.acquisition_service import AcquisitionService
    from rychlik.acquisition.contracts import DownloadRequest
    from rychlik.share.contracts import LinkShareRequest
    from rychlik.share.share_link_service import ShareLinkService
    from http_fixture_server import HttpFixtureServer, NORMAL_BODY

    http_fixture = HttpFixtureServer().start()
    try:
        completed = AcquisitionService().acquire(
            DownloadRequest(url=f"{http_fixture.base_url}/normal.mp4", destination_dir=tmp_path)
        )
        artifact = Artifact.from_completed_download(completed.final_path, source_url=completed.source_url)

        artifact_repository = InMemoryArtifactRepository()
        artifact_repository.save(artifact)

        share_link_repository = InMemoryShareLinkRepository()
        link_service = ShareLinkService(repository=share_link_repository)
        share_id = link_service.create_link(LinkShareRequest(artifact=artifact)).share_id
        link_service.activate(share_id)

        origin = LocalShareOrigin(
            share_link_repository=share_link_repository, artifact_repository=artifact_repository
        )
        origin.start()
        try:
            head = requests.head(origin.media_url(share_id))
            assert head.status_code == 200
            assert head.headers["Content-Length"] == str(len(NORMAL_BODY))

            ranged = requests.get(origin.media_url(share_id), headers={"Range": "bytes=0-9"})
            assert ranged.status_code == 206
            assert ranged.content == NORMAL_BODY[:10]
        finally:
            origin.stop()
    finally:
        http_fixture.stop()
