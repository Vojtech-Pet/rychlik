"""Approved Add Download dialog: URL + Save to. Nothing else is asked of a normal user."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from rychlik.gui.theme import icons
from rychlik.gui.theme.tokens import palette


class AddDownloadDialog(QDialog):
    """Calls `widget.submit_download(url, destination)` -- the same single service call the page
    always used -- and closes only after the service accepted the download."""

    def __init__(self, widget, *, theme: str = "dark") -> None:
        super().__init__(widget)
        self._widget = widget
        self._destination: Path = widget.destination_dir
        self.setWindowTitle("Add download")
        self.setModal(True)
        self.setMinimumWidth(560)
        p = palette(theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)
        title = QLabel("Add download")
        title.setProperty("role", "dialogTitle")
        layout.addWidget(title)

        url_label = QLabel("URL")
        url_label.setProperty("role", "caption")
        layout.addSpacing(8)
        layout.addWidget(url_label)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/file.mp4")
        self.url_input.setAccessibleName("Download URL")
        self.paste_button = QPushButton()
        self.paste_button.setIcon(icons.icon("copy", 16, p.icon))
        self.paste_button.setToolTip("Paste from clipboard")
        self.paste_button.setAccessibleName("Paste from clipboard")
        self.paste_button.setFixedWidth(32)
        row = QHBoxLayout()
        row.addWidget(self.url_input, 1)
        row.addWidget(self.paste_button)
        layout.addLayout(row)

        dest_label = QLabel("Save to")
        dest_label.setProperty("role", "caption")
        layout.addSpacing(6)
        layout.addWidget(dest_label)
        self.destination_display = QLineEdit(str(self._destination))
        self.destination_display.setReadOnly(True)
        self.destination_display.setAccessibleName("Destination folder")
        self.browse_button = QPushButton("Browse…")
        drow = QHBoxLayout()
        drow.addWidget(self.destination_display, 1)
        drow.addWidget(self.browse_button)
        layout.addLayout(drow)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "caption")
        layout.addSpacing(6)
        layout.addWidget(self.status_label)
        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.download_button = QPushButton("Download")
        self.download_button.setProperty("variant", "primary")
        self.download_button.setDefault(True)
        self.download_button.setEnabled(False)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)

        self._job = None
        self.url_input.textChanged.connect(lambda text: self.download_button.setEnabled(bool(text.strip()) and self._job is None))
        self.url_input.returnPressed.connect(self._submit)
        self.paste_button.clicked.connect(self._paste)
        self.browse_button.clicked.connect(self._browse)
        self.cancel_button.clicked.connect(self.reject)
        self.download_button.clicked.connect(self._submit)

    def _paste(self) -> None:
        from PySide6.QtGui import QGuiApplication

        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url_input.setText(text)

    def _browse(self) -> None:
        chosen = self._widget.choose_destination()
        if chosen is not None:
            self._destination = chosen
            self.destination_display.setText(str(chosen))

    def _submit(self) -> None:
        if not self.download_button.isEnabled():
            return
        self.status_label.setText("")
        self._finished = False
        job = self._widget.submit_download_async(self.url_input.text(), self._destination, self._on_result)
        if job is not None and not self._finished:  # a module is resolving the address in the background
            self._job = job
            self._set_busy(True)
            self.status_label.setText("Resolving URL…")

    def _on_result(self, ok: bool, message: str) -> None:
        self._finished = True
        self._job = None
        self._set_busy(False)
        if ok:
            self.accept()
        else:
            self.status_label.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self.url_input.setEnabled(not busy)
        self.paste_button.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.download_button.setEnabled(not busy and bool(self.url_input.text().strip()))

    def done(self, result: int) -> None:
        if self._job is not None:  # closing the dialog while a module is resolving cancels it: nothing is enqueued
            self._job.cancel()
            self._job = None
        super().done(result)
