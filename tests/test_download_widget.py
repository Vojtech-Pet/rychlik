from rychlik.gui.download_widget import DownloadWidget


def test_share_unavailable_until_download_completes(qapp, tmp_path):
    widget = DownloadWidget(destination_dir=tmp_path)

    assert not widget.share_button.isEnabled()


def test_share_becomes_available_after_real_download(qapp, http_fixture_server, tmp_path):
    widget = DownloadWidget(destination_dir=tmp_path)
    widget.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")

    widget.download_button.click()

    assert widget._worker is not None
    finished = widget._worker.wait(5000)
    qapp.processEvents()

    assert finished
    assert widget.share_button.isEnabled()
    assert widget.artifact is not None
    assert widget.artifact.filename == "normal.mp4"


def test_failed_download_keeps_share_disabled(qapp, http_fixture_server, tmp_path):
    widget = DownloadWidget(destination_dir=tmp_path)
    widget.url_input.setText(f"{http_fixture_server.base_url}/notfound")

    widget.download_button.click()
    widget._worker.wait(5000)
    qapp.processEvents()

    assert not widget.share_button.isEnabled()
    assert widget.artifact is None
    assert "Failed" in widget.status_label.text()
