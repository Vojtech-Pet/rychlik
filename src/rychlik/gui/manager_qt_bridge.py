"""Qt-safe event bridge from DownloadManagerService to the GUI thread
(Prompt A11).

A10's `subscribe()` callback may run on a background thread (the manager's
event-pump thread, or occasionally the calling thread of a synchronous
command). A Qt widget must never be mutated from that thread. This module
is the ONLY place that crosses that boundary: it re-emits every
`ManagerEvent` as a Qt signal, and Qt's cross-thread signal/slot delivery
(auto-queued whenever the emitting thread differs from the receiving
QObject's thread) marshals the callback onto the GUI thread automatically
-- no manual QMetaObject.invokeMethod plumbing needed.

`ManagerQtBridge` itself performs no widget mutation and holds no
reference to any window/widget -- it only forwards events. Rendering
logic lives entirely in `DownloadManagerWidget`.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from rychlik.core.download_manager_service import DownloadManagerService, ManagerEvent


class ManagerQtBridge(QObject):
    """Construct on the GUI thread, connect `manager_event` there, then
    call `attach()`. `detach()` unsubscribes from the service -- always
    call it before/around service shutdown so no further callback can run
    (§74/§75)."""

    manager_event = Signal(object)  # ManagerEvent

    def __init__(self, manager: DownloadManagerService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._token: int | None = None

    def attach(self) -> None:
        if self._token is not None:
            return  # idempotent -- avoid a duplicate subscription (§73)
        self._token = self._manager.subscribe(self._on_backend_event)

    def detach(self) -> None:
        if self._token is None:
            return
        self._manager.unsubscribe(self._token)
        self._token = None

    def _on_backend_event(self, event: ManagerEvent) -> None:
        """Runs on WHATEVER thread the backend used to call subscribe()'s
        callback -- must do nothing except re-emit. Qt marshals delivery
        of `manager_event` onto the GUI thread for any slot connected from
        there, via an automatically-queued connection."""
        self.manager_event.emit(event)
