"""In-memory paired-device registry (Prompt A13, §74/§75).

Runtime-only by design: long-term device persistence is explicitly
deferred until real Android trust semantics exist, and this phase must
not freeze the wrong credential contract by wedging pairing tokens into
the A12 download-state SQLite database (§76).
"""

from __future__ import annotations

import threading

from rychlik.device.contracts import FriendSendDevice, UnknownDeviceError


class PairedDeviceRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: dict[str, FriendSendDevice] = {}

    def add(self, device: FriendSendDevice) -> None:
        with self._lock:
            self._devices[device.device_id] = device

    def get(self, device_id: str) -> FriendSendDevice:
        with self._lock:
            device = self._devices.get(device_id)
        if device is None:
            raise UnknownDeviceError(device_id)
        return device

    def remove(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)

    def all_devices(self) -> tuple[FriendSendDevice, ...]:
        with self._lock:
            return tuple(self._devices.values())
