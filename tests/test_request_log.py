from datetime import datetime, timezone

import requests

from rychlik.core.artifact import Artifact
from rychlik.core.artifact_repository import InMemoryArtifactRepository
from rychlik.share.contracts import ShareStatus
from rychlik.share.local_share_origin import LocalShareOrigin
from rychlik.share.share_link import ShareLink, generate_share_id
from rychlik.share.share_link_repository import InMemoryShareLinkRepository
from rychlik.share.share_preview_service import SharePreviewService

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _origin(tmp_path):
    artifact_repository = InMemoryArtifactRepository()
    share_link_repository = InMemoryShareLinkRepository()
    preview_service = SharePreviewService(
        artifact_repository=artifact_repository, cache_dir=tmp_path / "cache"
    )
    origin = LocalShareOrigin(
        share_link_repository=share_link_repository,
        artifact_repository=artifact_repository,
        share_preview_service=preview_service,
    )
    return origin, artifact_repository, share_link_repository


def test_request_log_records_route_method_status_and_user_agent(tmp_path):
    origin, artifact_repository, share_link_repository = _origin(tmp_path)
    file_path = tmp_path / "clip.mp4"
    file_path.write_bytes(b"x" * 50)
    artifact = Artifact.from_completed_download(file_path)
    artifact_repository.save(artifact)
    link = ShareLink(
        share_id=generate_share_id(), artifact_id=artifact.artifact_id, created_at=UTC_NOW,
        status=ShareStatus.ACTIVE,
    )
    share_link_repository.save(link)

    origin.start()
    try:
        requests.get(origin.share_page_url(link.share_id), headers={"User-Agent": "TestAgent/1.0"})
        requests.get(origin.media_url(link.share_id), headers={"Range": "bytes=0-9", "User-Agent": "TestAgent/1.0"})

        log = origin.request_log
        assert len(log) == 2
        page_entry, media_entry = log

        assert page_entry.route == "page"
        assert page_entry.method == "GET"
        assert page_entry.share_id == link.share_id
        assert page_entry.http_status == 200
        assert page_entry.user_agent == "TestAgent/1.0"

        assert media_entry.route == "media"
        assert media_entry.http_status == 206
        assert media_entry.bytes_served == 10
        assert media_entry.range_header == "bytes=0-9"
    finally:
        origin.stop()


def test_request_log_never_exposes_secret_or_path_fields(tmp_path):
    # Structural guarantee: RequestLogEntry has no field that could carry
    # secret/source_url/local_path even by accident.
    from rychlik.share.local_share_origin import RequestLogEntry

    field_names = set(RequestLogEntry.__dataclass_fields__)
    assert "secret" not in field_names
    assert "source_url" not in field_names
    assert "local_path" not in field_names
    assert "cookies" not in field_names


def test_request_log_records_denied_and_unknown_requests(tmp_path):
    origin, artifact_repository, share_link_repository = _origin(tmp_path)
    origin.start()
    try:
        requests.get(origin.media_url("does-not-exist"))
        requests.get(f"http://{origin.address[0]}:{origin.address[1]}/nonsense")

        log = origin.request_log
        assert log[0].route == "media"
        assert log[0].http_status == 404
        assert log[0].result == "unknown_share"

        assert log[1].route == "unknown"
        assert log[1].http_status == 404
    finally:
        origin.stop()


def test_request_log_is_bounded(tmp_path):
    from rychlik.share.local_share_origin import RequestLogEntry, _REQUEST_LOG_MAXLEN

    origin, _, _ = _origin(tmp_path)
    for _ in range(_REQUEST_LOG_MAXLEN + 10):
        origin._append_request_log(
            RequestLogEntry(
                timestamp=UTC_NOW, route="media", method="GET", share_id="x", http_status=200,
                user_agent=None, range_header=None, bytes_served=0, result="served",
            )
        )
    assert len(origin.request_log) == _REQUEST_LOG_MAXLEN
