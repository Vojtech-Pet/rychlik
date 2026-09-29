"""Send video: fetches the video behind a link and copies it to the clipboard -- never the download queue.
For handing a link to another app/network (Facebook, Messenger, ...) as the actual video, not a link back to
its origin site. The file lives in a private throwaway cache and is never added to Rýchlik's download history.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QMimeData, QSettings, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout

from rychlik.gui.theme import icons
from rychlik.gui.theme.tokens import palette
from rychlik.sharing.video_fetch import FetchedVideo, VideoFetchCancelled, VideoFetchError, VideoFetchService


class _FetchThread(QThread):
    progress = Signal(int, object)  # bytes_done, total_bytes_or_None
    done = Signal(object)  # FetchedVideo
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, service: VideoFetchService, url: str, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._url = url
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            result = self._service.fetch(self._url, progress_callback=lambda done, total: self.progress.emit(done, total), cancel_event=self.cancel_event)
        except VideoFetchCancelled:
            self.cancelled.emit()
            return
        except VideoFetchError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - never let an unexpected backend error crash the GUI thread
            self.failed.emit(f"The video could not be fetched: {exc}")
            return
        self.done.emit(result)


class SendVideoDialog(QDialog):
    def __init__(self, parent=None, *, theme: str = "dark", service: VideoFetchService | None = None) -> None:
        super().__init__(parent)
        self._service = service or VideoFetchService()
        self._thread: _FetchThread | None = None
        self.result_video: FetchedVideo | None = None
        self.setWindowTitle("Send video")
        self.setModal(True)
        self.setMinimumWidth(560)
        p = palette(theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)
        title = QLabel("Send video")
        title.setProperty("role", "dialogTitle")
        layout.addWidget(title)
        note = QLabel("Fetches the video behind a link and copies it to the clipboard, ready to paste (Ctrl+V) "
                      "into Facebook, Messenger or anywhere else — the video itself, not a link back to the site.")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        layout.addSpacing(4)
        layout.addWidget(note)

        url_label = QLabel("Video link")
        url_label.setProperty("role", "caption")
        layout.addSpacing(10)
        layout.addWidget(url_label)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/watch?v=…")
        self.url_input.setAccessibleName("Video link")
        self.paste_button = QPushButton()
        self.paste_button.setIcon(icons.icon("copy", 16, p.icon))
        self.paste_button.setToolTip("Paste from clipboard")
        self.paste_button.setAccessibleName("Paste from clipboard")
        self.paste_button.setFixedWidth(32)
        row = QHBoxLayout()
        row.addWidget(self.url_input, 1)
        row.addWidget(self.paste_button)
        layout.addLayout(row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate until a total byte count is known
        self.progress_bar.setVisible(False)
        layout.addSpacing(10)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "caption")
        layout.addSpacing(6)
        layout.addWidget(self.status_label)

        layout.addSpacing(14)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setVisible(False)
        self.cancel_button = QPushButton("Cancel")
        self.send_button = QPushButton("Fetch")
        self.send_button.setProperty("variant", "primary")
        self.send_button.setDefault(True)
        self.send_button.setEnabled(False)
        buttons.addWidget(self.open_folder_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)

        self.url_input.textChanged.connect(lambda text: self.send_button.setEnabled(bool(text.strip()) and self._thread is None))
        self.url_input.returnPressed.connect(self._submit)
        self.paste_button.clicked.connect(self._paste)
        self.cancel_button.clicked.connect(self._cancel_or_close)
        self.send_button.clicked.connect(self._submit)
        self.open_folder_button.clicked.connect(self._open_folder)

    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url_input.setText(text)

    def _set_busy(self, busy: bool) -> None:
        self.url_input.setEnabled(not busy)
        self.paste_button.setEnabled(not busy)
        self.send_button.setEnabled(not busy and bool(self.url_input.text().strip()))
        self.send_button.setText("Fetching…" if busy else "Fetch")
        self.progress_bar.setVisible(busy)
        if not busy:
            self.progress_bar.setRange(0, 0)
        self.cancel_button.setText("Cancel" if busy else "Close")

    def _submit(self) -> None:
        if self._thread is not None or not self.send_button.isEnabled():
            return
        url = self.url_input.text().strip()
        self.result_video = None
        self.status_label.setText("")
        self.open_folder_button.setVisible(False)
        self._set_busy(True)
        self._thread = _FetchThread(self._service, url, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.done.connect(self._on_done)
        self._thread.cancelled.connect(self._on_cancelled)
        self._thread.failed.connect(self._on_failed)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_progress(self, done: int, total) -> None:
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    def _finish_thread(self) -> None:
        self._thread = None
        self._set_busy(False)

    def _on_done(self, video: FetchedVideo) -> None:
        self._finish_thread()
        self.result_video = video
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(video.path))])
        QGuiApplication.clipboard().setMimeData(mime)
        self.status_label.setText(f"Copied to clipboard: {video.display_name}. Paste it (Ctrl+V) where you want to send it.")
        self.open_folder_button.setVisible(True)

    def _on_cancelled(self) -> None:
        self._finish_thread()
        self.status_label.setText("Cancelled.")

    def _on_failed(self, message: str) -> None:
        self._finish_thread()
        self.status_label.setText(message)

    def _cancel_or_close(self) -> None:
        if self._thread is not None:
            self._thread.cancel_event.set()
            self.status_label.setText("Cancelling…")
        else:
            self.reject()

    def _open_folder(self) -> None:
        if self.result_video is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_video.path.parent)))

    def done(self, result: int) -> None:
        if self._thread is not None:
            self._thread.cancel_event.set()
        super().done(result)
