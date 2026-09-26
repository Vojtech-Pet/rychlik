from rychlik.core.artifact import Artifact
from rychlik.share.contracts import DeviceShareRequest, LinkShareRequest, ShareStatus
from rychlik.share.share_service import ShareService


def _artifact(tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x")
    return Artifact.from_completed_download(file_path)


def test_device_mode_works_when_link_mode_disabled(tmp_path):
    service = ShareService(device_enabled=True, link_enabled=False)

    result = service.send_to_device(DeviceShareRequest(artifact=_artifact(tmp_path)))

    assert result.status == ShareStatus.CREATING


def test_link_mode_works_when_device_mode_disabled(tmp_path):
    service = ShareService(device_enabled=False, link_enabled=True)

    result = service.share_by_link(LinkShareRequest(artifact=_artifact(tmp_path)))

    assert result.status == ShareStatus.CREATING


def test_link_mode_disabled_returns_failed(tmp_path):
    service = ShareService(device_enabled=True, link_enabled=False)

    result = service.share_by_link(LinkShareRequest(artifact=_artifact(tmp_path)))

    assert result.status == ShareStatus.FAILED


def test_device_mode_disabled_returns_failed(tmp_path):
    service = ShareService(device_enabled=False, link_enabled=True)

    result = service.send_to_device(DeviceShareRequest(artifact=_artifact(tmp_path)))

    assert result.status == ShareStatus.FAILED


def test_link_share_id_has_sufficient_entropy(tmp_path):
    service = ShareService()

    result = service.share_by_link(LinkShareRequest(artifact=_artifact(tmp_path)))

    # token_urlsafe(16) -> ~22 chars, well above a guessable length
    assert len(result.share_id) >= 20


def test_stop_and_revoke_unknown_ids_fail_cleanly(tmp_path):
    service = ShareService()

    device_result = service._device_service.stop_share("does-not-exist")
    link_result = service._link_service.revoke_link("does-not-exist")

    assert device_result.status == ShareStatus.FAILED
    assert link_result.status == ShareStatus.FAILED
