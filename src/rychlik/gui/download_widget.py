"""Functional-skeleton download UI: Add URL -> start -> progress -> completed -> Share.

No final styling. Runs acquisition on a background QThread so the UI never
blocks; the widget itself only owns UI state transitions.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import AcquisitionError, CompletedDownload, DownloadRequest
from rychlik.core.artifact import Artifact
from rychlik.gui.share_dialog import ShareDialog


class _DownloadWorker(QThread):
    progress = Signal(int, object)  # bytes_written, total_size (int or None)
    succeeded = Signal(object)  # CompletedDownload
    failed = Signal(str)

    def __init__(self, service: AcquisitionService, request: DownloadRequest, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._request = request
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            completed = self._service.acquire(
                self._request,
                progress_callback=lambda written, total: self.progress.emit(written, total),
                cancel_event=self.cancel_event,
            )
        except AcquisitionError as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(completed)


class DownloadWidget(QWidget):
    def __init__(
        self,
        *,
        acquisition_service: AcquisitionService | None = None,
        destination_dir: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = acquisition_service or AcquisitionService()
        self._destination_dir = destination_dir or Path(tempfile.mkdtemp(prefix="rychlik-"))
        self._worker: _DownloadWorker | None = None
        self.artifact: Artifact | None = None

        layout = QVBoxLayout(self)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/file.mp4")
        self.download_button = QPushButton("Download")
        url_row.addWidget(self.url_input)
        url_row.addWidget(self.download_button)
        layout.addLayout(url_row)

        self.status_label = QLabel("Idle")
        layout.addWidget(self.status_label)

        self.share_button = QPushButton("Share...")
        self.share_button.setEnabled(False)
        layout.addWidget(self.share_button)

        self.download_button.clicked.connect(self._on_download_clicked)
        self.share_button.clicked.connect(self._on_share_clicked)

    def _on_download_clicked(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Enter a URL first.")
            return

        request = DownloadRequest(url=url, destination_dir=self._destination_dir)
        self.status_label.setText("Downloading...")
        self.download_button.setEnabled(False)
        self.share_button.setEnabled(False)
        self.artifact = None

        self._worker = _DownloadWorker(self._service, request)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, written: int, total: int | None) -> None:
        if total:
            self.status_label.setText(f"Downloading... {written}/{total} bytes")
        else:
            self.status_label.setText(f"Downloading... {written} bytes")

    def _on_completed(self, completed: CompletedDownload) -> None:
        self.artifact = Artifact.from_completed_download(
            completed.final_path, source_url=completed.source_url
        )
        self.status_label.setText(f"Completed: {self.artifact.filename}")
        self.download_button.setEnabled(True)
        self.share_button.setEnabled(True)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(f"Failed: {message}")
        self.download_button.setEnabled(True)
        self.share_button.setEnabled(False)

    def _on_share_clicked(self) -> None:
        dialog = ShareDialog(self.artifact, parent=self)
        dialog.exec()
