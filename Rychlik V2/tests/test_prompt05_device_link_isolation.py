"""Prompt 05 regression coverage.

Prompt 05 ("Keep Device Mode Independent") is structurally satisfied by the
Prompt 03 ShareService split (see src/rychlik/share/share_service.py). This
file adds the explicit regression test the prompt asks for, rather than
duplicating architecture: disabling one mode must never break the other.
"""

from rychlik.core.artifact import Artifact
from rychlik.share.contracts import DeviceShareRequest, LinkShareRequest, ShareStatus
from rychlik.share.share_service import ShareService


def _artifact(tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x")
    return Artifact.from_completed_download(file_path)


def test_device_mode_remains_usable_when_link_mode_disabled(tmp_path):
    service = ShareService(device_enabled=True, link_enabled=False)
    artifact = _artifact(tmp_path)

    result = service.send_to_device(DeviceShareRequest(artifact=artifact))
    stop_result = service._device_service.stop_share(result.share_id)

    assert result.status == ShareStatus.CREATING
    assert stop_result.status == ShareStatus.REVOKED


def test_link_mode_remains_usable_when_device_mode_disabled(tmp_path):
    service = ShareService(device_enabled=False, link_enabled=True)
    artifact = _artifact(tmp_path)

    result = service.share_by_link(LinkShareRequest(artifact=artifact))
    # Prompt 06 state machine: CREATING has no direct path to REVOKED, only
    # ACTIVE/FAILED. Activate first to exercise a realistic lifecycle.
    service._link_service.activate(result.share_id)
    revoke_result = service._link_service.revoke_link(result.share_id)

    assert result.status == ShareStatus.CREATING
    assert revoke_result.status == ShareStatus.REVOKED
