from datetime import datetime, timezone

from rychlik.core.artifact import Artifact
from rychlik.share.contracts import LinkShareRequest, ShareStatus
from rychlik.share.share_link_service import ShareLinkService

FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _artifact(tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x")
    return Artifact.from_completed_download(file_path)


def _service():
    return ShareLinkService(clock=lambda: FIXED_NOW)


def test_create_link_starts_in_creating(tmp_path):
    service = _service()
    result = service.create_link(LinkShareRequest(artifact=_artifact(tmp_path)))

    assert result.status == ShareStatus.CREATING
    link = service.get_link(result.share_id)
    assert link.artifact_id
    assert link.created_at == FIXED_NOW


def test_activate_moves_to_active_and_can_set_public_url(tmp_path):
    service = _service()
    share_id = service.create_link(LinkShareRequest(artifact=_artifact(tmp_path))).share_id

    result = service.activate(share_id, public_url="https://s.example/xyz")

    assert result.status == ShareStatus.ACTIVE
    assert service.get_link(share_id).public_url == "https://s.example/xyz"


def test_mark_offline_and_reactivate(tmp_path):
    service = _service()
    share_id = service.create_link(LinkShareRequest(artifact=_artifact(tmp_path))).share_id
    service.activate(share_id)

    offline_result = service.mark_offline(share_id)
    reactivate_result = service.activate(share_id)

    assert offline_result.status == ShareStatus.OFFLINE
    assert reactivate_result.status == ShareStatus.ACTIVE


def test_revoke_is_idempotent(tmp_path):
    service = _service()
    share_id = service.create_link(LinkShareRequest(artifact=_artifact(tmp_path))).share_id
    service.activate(share_id)

    first = service.revoke_link(share_id)
    second = service.revoke_link(share_id)

    assert first.status == ShareStatus.REVOKED
    assert second.status == ShareStatus.REVOKED
    assert second.error is None


def test_invalid_transition_leaves_status_unchanged(tmp_path):
    service = _service()
    share_id = service.create_link(LinkShareRequest(artifact=_artifact(tmp_path))).share_id
    # CREATING -> OFFLINE is not an allowed transition
    result = service.mark_offline(share_id)

    assert result.status == ShareStatus.CREATING  # rejected, reports current status
    assert result.error is not None
    assert service.get_link(share_id).status == ShareStatus.CREATING


def test_unknown_share_id_operations_fail_cleanly():
    service = _service()

    assert service.activate("nope").status == ShareStatus.FAILED
    assert service.mark_offline("nope").status == ShareStatus.FAILED
    assert service.expire("nope").status == ShareStatus.FAILED
    assert service.mark_failed("nope").status == ShareStatus.FAILED
    assert service.revoke_link("nope").status == ShareStatus.FAILED
    assert service.get_link("nope") is None


def test_create_link_rejects_empty_artifact_id(tmp_path):
    service = _service()
    artifact = _artifact(tmp_path)
    bad_request = LinkShareRequest(artifact=artifact)
    object.__setattr__(bad_request.artifact, "artifact_id", "")  # force invalid state for the test

    result = service.create_link(bad_request)

    assert result.status == ShareStatus.FAILED
