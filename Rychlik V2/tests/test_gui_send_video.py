"""SendVideoDialog: real fetch (yt-dlp against a local HTTP server) through the real QThread, real clipboard."""

from __future__ import annotations

import time

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from rychlik.gui.dialogs.send_video import SendVideoDialog
from rychlik.sharing.video_fetch import VideoFetchService
from tests.http_fixture_server import NORMAL_BODY


def _pump(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


def _service(monkeypatch, tmp_path, http_fixture_server):
    service = VideoFetchService(tmp_path / "cache")
    monkeypatch.setattr("rychlik.sharing.video_fetch.known_video_page", lambda url: True)
    return service


def test_fetch_copies_the_video_to_the_clipboard_as_a_file_url(qapp, http_fixture_server, tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path, http_fixture_server)
    dialog = SendVideoDialog(service=service)
    dialog.url_input.setText(f"{http_fixture_server.base_url}/normal.mp4")
    assert dialog.send_button.isEnabled()
    dialog.send_button.click()
    assert not dialog.send_button.isEnabled()  # busy while fetching
    assert _pump(lambda: dialog.result_video is not None)
    assert dialog.result_video.path.read_bytes() == NORMAL_BODY
    urls = QGuiApplication.clipboard().mimeData().urls()
    assert len(urls) == 1 and urls[0].toLocalFile() == str(dialog.result_video.path)
    assert "Copied to clipboard" in dialog.status_label.text()
    assert dialog.open_folder_button.isVisibleTo(dialog)
    dialog.close()


def test_a_link_that_is_not_a_recognised_video_shows_a_bounded_error_and_copies_nothing(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("rychlik.sharing.video_fetch.known_video_page", lambda url: False)
    QGuiApplication.clipboard().clear()
    service = VideoFetchService(tmp_path / "cache")
    dialog = SendVideoDialog(service=service)
    dialog.url_input.setText("https://example.test/not-a-video")
    dialog.send_button.click()
    assert _pump(lambda: "does not look like a link to a video" in dialog.status_label.text())
    mime = QGuiApplication.clipboard().mimeData()
    assert mime is None or mime.urls() == []
    dialog.close()


def test_cancel_during_fetch_never_copies_to_the_clipboard(qapp, http_fixture_server, tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path, http_fixture_server)
    QGuiApplication.clipboard().clear()
    dialog = SendVideoDialog(service=service)
    dialog.url_input.setText(f"{http_fixture_server.base_url}/slow")
    dialog.send_button.click()
    assert _pump(lambda: dialog.cancel_button.text() == "Cancel" and dialog.progress_bar.isVisibleTo(dialog))
    dialog.cancel_button.click()
    assert _pump(lambda: "Cancelled" in dialog.status_label.text())
    mime = QGuiApplication.clipboard().mimeData()
    assert mime is None or mime.urls() == []
    assert dialog.result_video is None
    dialog.close()


def test_paste_button_fills_the_url_field_from_the_clipboard(qapp, tmp_path):
    QGuiApplication.clipboard().setText("https://example.test/pasted-link")
    dialog = SendVideoDialog(service=VideoFetchService(tmp_path / "cache"))
    dialog.paste_button.click()
    assert dialog.url_input.text() == "https://example.test/pasted-link"
    dialog.close()


def test_closing_the_dialog_while_fetching_cancels_it(qapp, http_fixture_server, tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path, http_fixture_server)
    dialog = SendVideoDialog(service=service)
    dialog.url_input.setText(f"{http_fixture_server.base_url}/slow")
    dialog.send_button.click()
    assert _pump(lambda: dialog._thread is not None)
    thread = dialog._thread
    dialog.reject()  # the window-close path (QWidget.close() is a no-op on a never-shown dialog)
    assert _pump(lambda: thread.cancel_event.is_set())
