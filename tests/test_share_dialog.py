from PySide6.QtWidgets import QDialog

from rychlik.core.artifact import Artifact
from rychlik.gui.share_dialog import ShareDialog


def _artifact(tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x")
    return Artifact.from_completed_download(file_path)


def test_completed_item_enables_both_choices(qapp, tmp_path):
    dialog = ShareDialog(_artifact(tmp_path))

    assert dialog.device_button.isEnabled()
    assert dialog.link_button.isEnabled()
    assert "video.mp4" in dialog.title_label.text()


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
