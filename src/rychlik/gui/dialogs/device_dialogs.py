"""Approved Share / Send to Device / Share by Link / Pair / Forget dialogs and the Devices page.

Widgets only talk to DeviceModeController; they never build transports or touch the trust store.
Text is truthful: nothing is shown as sent/received/online unless the real service reported it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from rychlik.core.artifact import Artifact
from rychlik.device.contracts import HandoffState
from rychlik.device.desktop_devices import DeviceRow, DeviceState
from rychlik.gui.device_mode import DeviceModeController, PairingSession, friendly_error, pairing_expiry_text, send_stage
from rychlik.share.contracts import LinkShareRequest, ShareStatus
from rychlik.share.share_link_service import ShareLinkService

STATE_TEXT = {
    DeviceState.TRUSTED_ONLINE: "Trusted · Online",
    DeviceState.TRUSTED_OFFLINE: "Trusted · Offline",
    DeviceState.IDENTITY_CHANGED: "Identity changed",
    DeviceState.UNPAIRED_DISCOVERED: "Not paired",
}
STATE_TONE = {
    DeviceState.TRUSTED_ONLINE: "success",
    DeviceState.TRUSTED_OFFLINE: "muted",
    DeviceState.IDENTITY_CHANGED: "error",
    DeviceState.UNPAIRED_DISCOVERED: "info",
}
STEPS = ("Prepare", "Send", "Verified")
_TERMINAL = (HandoffState.RECEIVED, HandoffState.FAILED, HandoffState.CANCELLED)


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "dialogTitle")
    label.setWordWrap(True)
    return label


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "muted")
    label.setWordWrap(True)
    return label


class DeviceRowWidget(QWidget):
    """One device: state dot + name, real state text, and (for a changed identity) what to do next."""

    def __init__(self, row: DeviceRow, parent=None, *, dimmed: bool = False) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        tone = STATE_TONE[row.state]
        dot = QLabel("●")
        dot.setProperty("tone", tone if tone != "muted" else "")
        dot.setProperty("role", "muted" if tone == "muted" else "")
        text = QVBoxLayout()
        text.setSpacing(1)
        self.name_label = QLabel(row.display_name)
        font = self.name_label.font()
        font.setWeight(font.Weight.DemiBold)
        self.name_label.setFont(font)
        self.state_label = QLabel(STATE_TEXT[row.state])
        self.state_label.setProperty("role", "caption")
        if row.state == DeviceState.IDENTITY_CHANGED:
            self.state_label.setText(f"{STATE_TEXT[row.state]} · forget and pair again to continue")
            self.state_label.setProperty("tone", "error")
        text.addWidget(self.name_label)
        text.addWidget(self.state_label)
        layout.addWidget(dot)
        layout.addLayout(text, 1)
        if dimmed:
            self.setEnabled(False)


def confirm_forget(row: DeviceRow, parent=None) -> bool:
    box = QMessageBox(QMessageBox.Icon.Warning, f"Forget {row.display_name}?",
                      "Rýchlik will no longer send files to this device. You can pair it again at any time.",
                      QMessageBox.StandardButton.NoButton, parent)
    forget = box.addButton("Forget device", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is forget


class ShareSelectorDialog(QDialog):
    """Two truthful choices. 'Send to device' is enabled only when a trusted device exists."""

    def __init__(self, artifact: Artifact, controller: DeviceModeController | None, parent=None, *, link_service: ShareLinkService | None = None) -> None:
        super().__init__(parent)
        self.artifact, self._controller, self._link_service = artifact, controller, link_service
        self.setWindowTitle("Share")
        self.setModal(True)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(_title(f"Share “{artifact.filename}”"))
        self.device_button = QPushButton("Send to device")
        self.device_button.setProperty("variant", "primary")
        self.link_button = QPushButton("Share by link")
        self.hint = _muted()
        trusted = [r for r in controller.rows() if r.trusted] if controller else []
        self.device_button.setEnabled(bool(trusted))
        if controller is None:
            self.hint.setText("Device Mode is not available.")
        elif not trusted:
            self.hint.setText("No paired devices yet. Pair FriendSend from the Devices page to send files to a phone.")
        layout.addWidget(self.device_button)
        layout.addWidget(self.link_button)
        layout.addWidget(self.hint)
        self.cancel_button = QPushButton("Cancel")
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.cancel_button.clicked.connect(self.reject)
        self.device_button.clicked.connect(self._send_to_device)
        self.link_button.clicked.connect(self._share_by_link)

    def _send_to_device(self) -> None:
        self.accept()
        SendToDeviceDialog(self.artifact, self._controller, self.parentWidget()).exec()

    def _share_by_link(self) -> None:
        self.accept()
        ShareByLinkDialog(self.artifact, self.parentWidget(), link_service=self._link_service).exec()


class ShareByLinkDialog(QDialog):
    """Existing Share-by-Link behaviour (ShareLinkService.create_link), restyled. It reports exactly what
    the service produced: status, and a public address only if the service really holds one."""

    def __init__(self, artifact: Artifact, parent=None, *, link_service: ShareLinkService | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share by link")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(_title(f"Share “{artifact.filename}” by link"))
        service = link_service or ShareLinkService()
        result = service.create_link(LinkShareRequest(artifact=artifact))
        self.result = result
        if result.status == ShareStatus.FAILED:
            text = f"Share link failed: {result.error}"
        else:
            text = f"Share link created\nStatus: {result.status.name}"
            link = service.get_link(result.share_id)
            public_url = getattr(link, "public_url", None)
            if public_url:
                text += f"\n{public_url}"
            else:
                text += "\nNo public web address is published yet, so nobody can open this link."
        self.status_label = _muted(text)
        layout.addWidget(self.status_label)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)


class SendToDeviceDialog(QDialog):
    def __init__(self, artifact: Artifact, controller: DeviceModeController | None, parent=None) -> None:
        super().__init__(parent)
        self.artifact, self._controller = artifact, controller
        self._handoff_id: str | None = None
        self._device: DeviceRow | None = None
        self.setWindowTitle("Send to device")
        self.setModal(True)
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(_title(f"Send “{artifact.filename}”"))

        self.select_page = QWidget()
        sl = QVBoxLayout(self.select_page)
        sl.setContentsMargins(0, 0, 0, 0)
        self.device_list = QListWidget()
        self.device_list.setMinimumHeight(120)
        self.banner = _muted()
        sl.addWidget(self.device_list)
        sl.addWidget(self.banner)
        layout.addWidget(self.select_page)

        self.progress_page = QWidget()
        pl = QVBoxLayout(self.progress_page)
        pl.setContentsMargins(0, 0, 0, 0)
        self.stepper = QLabel()
        self.stepper.setTextFormat(Qt.TextFormat.RichText)
        self.headline = QLabel()
        self.headline.setProperty("role", "heading")
        self.headline.setWordWrap(True)
        self.detail = _muted()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.reassurance = _muted()
        for w in (self.stepper, self.headline, self.bar, self.detail, self.reassurance):
            pl.addWidget(w)
        layout.addWidget(self.progress_page)
        self.progress_page.hide()

        buttons = QHBoxLayout()
        self.forget_button = QPushButton("Forget device")
        self.forget_button.setProperty("variant", "dangerText")
        self.forget_button.hide()
        self.cancel_button = QPushButton("Cancel")
        self.send_button = QPushButton("Send")
        self.send_button.setProperty("variant", "primary")
        buttons.addWidget(self.forget_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.send_button)
        layout.addSpacing(8)
        layout.addLayout(buttons)

        self.cancel_button.clicked.connect(self._cancel)
        self.send_button.clicked.connect(self._start)
        self.forget_button.clicked.connect(self._forget)
        self.device_list.currentRowChanged.connect(lambda _r: self._update_send_enabled())
        if controller is not None:
            controller.devices_changed.connect(self.reload_devices)
            controller.handoff_updated.connect(self._on_updated)
        self.reload_devices()

    # --- selection -------------------------------------------------------------------------------------------

    def reload_devices(self) -> None:
        if self._handoff_id is not None or self._controller is None:
            return
        previous = self.selected_device_id()
        rows = [r for r in self._controller.rows() if r.trusted]
        self.device_list.clear()
        for row in rows:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row.device_id)
            widget = DeviceRowWidget(row, dimmed=not row.can_send)
            item.setSizeHint(widget.sizeHint())
            if not row.can_send:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            self.device_list.addItem(item)
            self.device_list.setItemWidget(item, widget)
            if row.device_id == previous and row.can_send:
                self.device_list.setCurrentItem(item)
        online = [r for r in rows if r.can_send]
        if not rows:
            self.banner.setText("No paired devices. Pair FriendSend from the Devices page first.")
        elif not online:
            self.banner.setText("No device is online. Open FriendSend on your phone and keep it on the same Wi-Fi network.")
        else:
            self.banner.setText("")
            if self.device_list.currentRow() < 0:
                for i in range(self.device_list.count()):
                    if self.device_list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable:
                        self.device_list.setCurrentRow(i)
                        break
        self._update_send_enabled()

    def selected_device_id(self) -> str | None:
        item = self.device_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _update_send_enabled(self) -> None:
        self.send_button.setEnabled(self._handoff_id is None and self.selected_device_id() is not None)

    # --- sending -----------------------------------------------------------------------------------------------

    def _start(self) -> None:
        device_id = self.selected_device_id()
        if device_id is None or self._controller is None or self._handoff_id is not None:
            return
        self._device = self._controller.row_for(device_id)
        try:
            self._handoff_id = self._controller.send(device_id, self.artifact)
        except Exception:  # noqa: BLE001 - bounded wording, no traceback
            self._show_failure(friendly_error(None, self._device.display_name if self._device else "the device"))
            return
        self.select_page.hide()
        self.progress_page.show()
        self.send_button.hide()
        self._on_updated(self._handoff_id)

    def _name(self) -> str:
        return self._device.display_name if self._device else "the device"

    def _on_updated(self, handoff_id: str) -> None:
        if handoff_id != self._handoff_id or self._controller is None:
            return
        snap = self._controller.snapshot(handoff_id)
        if snap is None:
            return
        if snap.state == HandoffState.FAILED:
            self._show_failure(friendly_error(snap.failure_code, self._name()))
            return
        stage = send_stage(snap, self._name(), self.artifact.duration)
        self._render_stepper(stage.step, done=snap.state in _TERMINAL)
        self.headline.setText(stage.headline)
        self.detail.setText(stage.detail)
        self.reassurance.setText(stage.reassurance or "")
        self.reassurance.setVisible(bool(stage.reassurance))
        if stage.indeterminate or stage.fraction is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, stage.fraction)) * 1000))
        if snap.state in _TERMINAL:
            self.cancel_button.setText("Close")

    def _render_stepper(self, step: int, *, done: bool) -> None:
        parts = []
        for i, name in enumerate(STEPS):
            if i < step or (done and step == 2):
                parts.append(f"● {name}")
            elif i == step:
                parts.append(f"<b>● {name}</b>")
            else:
                parts.append(f"○ {name}")
        self.stepper.setText("&nbsp;&nbsp;›&nbsp;&nbsp;".join(parts))

    def _show_failure(self, err) -> None:
        self.select_page.hide()
        self.progress_page.show()
        self.send_button.hide()
        self.stepper.setText("")
        self.headline.setText(err.title)
        self.detail.setText(err.text)
        self.bar.hide()
        self.reassurance.hide()
        self.cancel_button.setText("Close")
        self.forget_button.setVisible(err.identity_problem and self._device is not None)

    def _forget(self) -> None:
        if self._device is not None and self._controller is not None and confirm_forget(self._device, self):
            self._controller.forget_device(self._device.device_id)
            self.accept()

    def _cancel(self) -> None:
        if self._handoff_id is not None and self._controller is not None:
            snap = self._controller.snapshot(self._handoff_id)
            if snap is not None and snap.state not in _TERMINAL:
                self._controller.cancel(self._handoff_id)
                return  # the CANCELLED event closes the loop; the button then reads "Close"
        self.reject()


class PairDeviceDialog(QDialog):
    """Real pairing: shows the one-time pairing code, its expiry, and success only once trust really exists."""

    def __init__(self, controller: DeviceModeController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._session: PairingSession | None = None
        self.paired_row: DeviceRow | None = None
        self.setWindowTitle("Pair FriendSend")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(_title("Pair a phone with Rýchlik"))
        layout.addWidget(_muted("1. Open FriendSend on your phone (same Wi-Fi network).\n2. Choose “Paste code from Rýchlik”.\n3. Paste the pairing code below."))
        self.code_box = QLineEdit()  # shows the start of the code; "Copy code" copies all of it
        self.code_box.setReadOnly(True)
        layout.addWidget(self.code_box)
        self.copy_button = QPushButton("Copy code")
        self.copy_button.setProperty("variant", "primary")
        self.status_label = _muted()
        self.expiry_label = _muted()
        layout.addWidget(self.copy_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status_label)
        layout.addWidget(self.expiry_label)
        row = QHBoxLayout()
        self.new_code_button = QPushButton("New code")
        self.close_button = QPushButton("Cancel")
        row.addWidget(self.new_code_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.copy_button.clicked.connect(self._copy)
        self.new_code_button.clicked.connect(self.new_code)
        self.close_button.clicked.connect(self.reject)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        controller.devices_changed.connect(self.tick)
        self.new_code()
        self._timer.start()

    def new_code(self) -> None:
        try:
            self._session = self._controller.create_pairing_session()
        except Exception:  # noqa: BLE001
            self._session = None
            self.status_label.setText("Couldn’t start pairing on this network. Check your Wi-Fi connection and try again.")
            self.copy_button.setEnabled(False)
            return
        self.code_box.setText(self._session.code_text)
        self.code_box.setCursorPosition(0)
        self.copy_button.setEnabled(True)
        self.status_label.setText("Waiting for the phone…")
        self.tick()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code_box.text())
        self.status_label.setText("Code copied. Paste it in FriendSend…")

    def tick(self) -> None:
        session = self._session
        if session is None or self.paired_row is not None:
            return
        row = self._controller.completed_pairing(session)
        if row is not None:
            self.paired_row = row
            self._timer.stop()
            self.code_box.clear()
            self.status_label.setText(f"Paired with {row.display_name}.")
            self.expiry_label.setText("")
            self.copy_button.setEnabled(False)
            self.new_code_button.hide()
            self.close_button.setText("Done")
            return
        if session.seconds_left() <= 0:
            self.status_label.setText("This code expired.")
            self.expiry_label.setText("")
            self.copy_button.setEnabled(False)
        else:
            self.expiry_label.setText(pairing_expiry_text(session))

    def done(self, result: int) -> None:
        self._timer.stop()
        if self.paired_row is None:
            self._controller.cancel_pairing()
        super().done(result)


class DevicesPage(QWidget):
    """Trusted and visible devices with their real states; pairing and forgetting go through the controller."""

    def __init__(self, controller: DeviceModeController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        head = QHBoxLayout()
        head.addWidget(_title("Devices"))
        head.addStretch(1)
        self.pair_button = QPushButton("Pair FriendSend…")
        self.pair_button.setProperty("variant", "primary")
        head.addWidget(self.pair_button)
        layout.addLayout(head)
        self.problem = _muted()
        layout.addWidget(self.problem)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.empty = _muted("No devices yet. Pair FriendSend to send downloads to your phone.")
        layout.addWidget(self.empty)
        foot = QHBoxLayout()
        self.forget_button = QPushButton("Forget device")
        self.forget_button.setProperty("variant", "dangerText")
        self.forget_button.setEnabled(False)
        foot.addWidget(self.forget_button)
        foot.addStretch(1)
        layout.addLayout(foot)
        self.pair_button.clicked.connect(self.pair)
        self.forget_button.clicked.connect(self.forget_selected)
        self.list.currentRowChanged.connect(lambda r: self.forget_button.setEnabled(self.selected_row() is not None))
        controller.devices_changed.connect(self.refresh)
        controller.discovery_problem.connect(self.problem.setText)
        self.refresh()

    def set_theme(self, theme: str) -> None:  # styling comes from the application stylesheet
        pass

    def selected_row(self) -> DeviceRow | None:
        item = self.list.currentItem()
        return self._controller.row_for(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def refresh(self) -> None:
        selected = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        rows = self._controller.rows()
        self.list.clear()
        for row in rows:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row.device_id)
            widget = DeviceRowWidget(row)
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
            if row.device_id == selected:
                self.list.setCurrentItem(item)
        self.empty.setVisible(not rows)

    def pair(self) -> None:
        PairDeviceDialog(self._controller, self).exec()
        self.refresh()

    def forget_selected(self) -> None:
        row = self.selected_row()
        if row is not None and confirm_forget(row, self):
            self._controller.forget_device(row.device_id)
            self.refresh()
