from PySide6.QtWidgets import QDialog

from rychlik.core.artifact import Artifact
from rychlik.gui.share_dialog import ShareDialog


def _artifact(tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x")
    return Artifact.from_completed_download(file_path)


def test_completed_item_enables_link_choice(qapp, tmp_path):
    dialog = ShareDialog(_artifact(tmp_path))

    assert dialog.link_button.isEnabled()
    assert "video.mp4" in dialog.title_label.text()


def test_completed_item_without_device_service_shows_truthful_empty_state(qapp, tmp_path):
    # Prompt A13 (§107/§109): production wiring passes no
    # DeviceHandoffService yet (no real FriendSend app exists) -- the
    # dialog must never fake an available device.
    dialog = ShareDialog(_artifact(tmp_path))

    assert not dialog.device_button.isEnabled()
    assert dialog.device_combo.currentText() == "Device Mode not available"


def test_missing_artifact_disables_both_choices(qapp):
    dialog = ShareDialog(None)

    assert not dialog.device_button.isEnabled()
    assert not dialog.link_button.isEnabled()


def test_failed_item_is_represented_as_no_artifact(qapp):
    # A failed/incomplete download never produces an Artifact (Prompt 02 rule),
    # so "failed item" and "missing artifact" share the same dialog state.
    dialog = ShareDialog(None)

    assert dialog.status_label.text() == "Nothing to share."


def test_cancel_rejects_dialog(qapp, tmp_path):
    dialog = ShareDialog(_artifact(tmp_path))

    dialog.cancel_button.click()

    assert dialog.result() == QDialog.Rejected


def test_share_by_link_click_shows_creating_status_placeholder(qapp, tmp_path):
    dialog = ShareDialog(_artifact(tmp_path))

    dialog.link_button.click()

    assert "Share link created" in dialog.status_label.text()
    assert "CREATING" in dialog.status_label.text()


# --- Device Mode wiring (Prompt A13) -----------------------------------------


class _FakeDeviceHandoffService:
    def __init__(self, devices=()):
        self._devices = devices
        self.send_calls = []

    def devices(self):
        return self._devices

    def send(self, device_id, artifact):
        self.send_calls.append((device_id, artifact))
        return "handoff-1234-5678"


class _FakeDevice:
    def __init__(self, device_id, display_name):
        self.device_id = device_id
        self.display_name = display_name


def test_device_list_populated_from_real_service(qapp, tmp_path):
    devices = (_FakeDevice("d1", "My Phone"),)
    service = _FakeDeviceHandoffService(devices)
    dialog = ShareDialog(_artifact(tmp_path), device_handoff_service=service)

    assert dialog.device_button.isEnabled()
    assert dialog.device_combo.currentText() == "My Phone"
    assert dialog.device_combo.currentData() == "d1"


def test_send_to_device_calls_service_with_exact_artifact_and_device(qapp, tmp_path):
    devices = (_FakeDevice("d1", "My Phone"),)
    service = _FakeDeviceHandoffService(devices)
    artifact = _artifact(tmp_path)
    dialog = ShareDialog(artifact, device_handoff_service=service)

    dialog.device_button.click()

    assert len(service.send_calls) == 1
    device_id, sent_artifact = service.send_calls[0]
    assert device_id == "d1"
    assert sent_artifact is artifact
    assert "handoff" in dialog.status_label.text().lower()


def test_send_to_device_never_claims_delivery(qapp, tmp_path):
    devices = (_FakeDevice("d1", "My Phone"),)
    service = _FakeDeviceHandoffService(devices)
    dialog = ShareDialog(_artifact(tmp_path), device_handoff_service=service)

    dialog.device_button.click()

    text = dialog.status_label.text().lower()
    for forbidden in ("delivered", "received by", "viewed"):
        assert forbidden not in text
