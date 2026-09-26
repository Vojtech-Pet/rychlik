"""Functional-skeleton Share dialog (Prompt 04). No final styling/icons/branding."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from rychlik.core.artifact import Artifact


class ShareDialog(QDialog):
    """Two explicit choices: Send to device/app, Share by link.

    No AUTO mode. Only a completed, valid Artifact may be shared.
    """

    def __init__(self, artifact: Artifact | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share")
        self.artifact = artifact

        layout = QVBoxLayout(self)

        self.title_label = QLabel()
        layout.addWidget(self.title_label)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.device_button = QPushButton("Send to device / app")
        self.link_button = QPushButton("Share by link")
        button_row.addWidget(self.device_button)
        button_row.addWidget(self.link_button)
        layout.addLayout(button_row)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)

        self._refresh_state()

    def _refresh_state(self) -> None:
        if self.artifact is None:
            self.title_label.setText("No artifact selected")
            self.status_label.setText("Nothing to share.")
            self.device_button.setEnabled(False)
            self.link_button.setEnabled(False)
            return

        self.title_label.setText(f"Share \"{self.artifact.filename}\"")
        self.status_label.setText("")
        self.device_button.setEnabled(True)
        self.link_button.setEnabled(True)
