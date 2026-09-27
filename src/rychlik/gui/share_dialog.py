"""Functional-skeleton Share dialog (Prompt 04, Device Mode wired in A13).

No final styling/icons/branding. Device Mode (rychlik.device) integration
is deliberately small (§105/§106): this dialog is not redesigned, only the
existing "Send to device / app" button is wired to a real
DeviceHandoffService when one is supplied. Production code (main.py) does
not construct a DeviceHandoffService yet -- there is no real FriendSend
Android app to pair with -- so `device_handoff_service=None` (the default)
keeps this dialog's device list truthfully empty rather than showing a
fake phone (§107/§109).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from rychlik.core.artifact import Artifact
from rychlik.share.contracts import LinkShareRequest, ShareStatus
from rychlik.share.share_link_service import ShareLinkService


class ShareDialog(QDialog):
    """Two explicit choices: Send to device/app, Share by link.

    No AUTO mode. Only a completed, valid Artifact may be shared.
    """

    def __init__(
        self,
        artifact: Artifact | None,
        parent=None,
        *,
        share_link_service: ShareLinkService | None = None,
        device_handoff_service=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share")
        self.artifact = artifact
        self._share_link_service = share_link_service or ShareLinkService()
        self._device_handoff_service = device_handoff_service

        layout = QVBoxLayout(self)

        self.title_label = QLabel()
        layout.addWidget(self.title_label)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_button = QPushButton("Send to device / app")
        device_row.addWidget(self.device_combo)
        device_row.addWidget(self.device_button)
        layout.addLayout(device_row)

        self.link_button = QPushButton("Share by link")
        layout.addWidget(self.link_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)

        self.link_button.clicked.connect(self._on_share_by_link_clicked)
        self.device_button.clicked.connect(self._on_send_to_device_clicked)

        self._refresh_state()

    def _on_share_by_link_clicked(self) -> None:
        # Functional placeholder only: no URL, no QR, no transport (Prompt 06 scope).
        if self.artifact is None:
            return
        result = self._share_link_service.create_link(LinkShareRequest(artifact=self.artifact))
        if result.status == ShareStatus.FAILED:
            self.status_label.setText(f"Share link failed: {result.error}")
        else:
            self.status_label.setText(f"Share link created\nStatus: {result.status.name}")

    def _on_send_to_device_clicked(self) -> None:
        if self.artifact is None or self._device_handoff_service is None:
            return
        device_id = self.device_combo.currentData()
        if device_id is None:
            self.status_label.setText("No FriendSend devices paired.")
            return
        try:
            handoff_id = self._device_handoff_service.send(device_id, self.artifact)
        except Exception as exc:  # noqa: BLE001 -- bounded, no raw traceback shown
            self.status_label.setText(f"Could not start device send: {exc}")
            return
        # Async, like every other Device Mode command (§79-§82 of the
        # backend prompt): this only means the send was ACCEPTED, never
        # that the peer received it yet. A caller wanting live progress
        # subscribes to the service's own event stream separately -- this
        # functional dialog only shows the bounded initial acknowledgement.
        self.status_label.setText(f"Device send started (handoff {handoff_id[:8]}...)")

    def _refresh_state(self) -> None:
        if self.artifact is None:
            self.title_label.setText("No artifact selected")
            self.status_label.setText("Nothing to share.")
            self.device_button.setEnabled(False)
            self.link_button.setEnabled(False)
            self.device_combo.setEnabled(False)
            return

        self.title_label.setText(f"Share \"{self.artifact.filename}\"")
        self.status_label.setText("")
        self.link_button.setEnabled(True)
        self._refresh_device_list()

    def _refresh_device_list(self) -> None:
        self.device_combo.clear()
        if self._device_handoff_service is None:
            self.device_combo.addItem("Device Mode not available")
            self.device_combo.setEnabled(False)
            self.device_button.setEnabled(False)
            return

        devices = self._device_handoff_service.devices()
        if not devices:
            # §109: truthful empty state, never a fake/simulated device.
            self.device_combo.addItem("No FriendSend devices paired")
            self.device_combo.setEnabled(False)
            self.device_button.setEnabled(False)
            return

        for device in devices:
            self.device_combo.addItem(device.display_name, userData=device.device_id)
        self.device_combo.setEnabled(True)
        self.device_button.setEnabled(True)
