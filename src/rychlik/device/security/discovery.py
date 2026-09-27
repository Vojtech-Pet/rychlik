"""Real LAN discovery for Device Mode (Prompt A15 §53-70).

Uses a standard, maintained DNS-SD/mDNS implementation (`zeroconf`) --
never raw multicast packet parsing (§55). Discovery is explicitly
untrusted (§57): a discovered record only ever produces a
`DiscoveredFriendSendDevice` (ephemeral), never a `TrustedFriendSendDevice`
(persistent) -- the two are deliberately kept as separate types (§63) so
they can never be silently mixed up. Nothing here ever grants trust by
itself; only `SecurePairingManager` (explicit pairing) and a real
SPKI-pin-verified re-endpoint (via `secure_transport.connect_and_verify_pin`)
may change persisted trust (§60/§137).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from rychlik.device.contracts import FriendSendEndpoint

SERVICE_TYPE = "_friendsend._tcp.local."


@dataclass(frozen=True)
class DiscoveredFriendSendDevice:
    """Ephemeral, untrusted (§57/§63) -- an mDNS device_id match is never
    proof of identity; only a subsequent successful SPKI pin check is."""

    device_id: str
    endpoint: FriendSendEndpoint
    protocol_version: int
    security_profile: str


DiscoveryCallback = Callable[[DiscoveredFriendSendDevice], None]
RemovalCallback = Callable[[str], None]  # device_id


class FriendSendDiscoveryService:
    """Owns a single `zeroconf`/`ServiceBrowser` pair (§69: not one
    per-UI-widget browser). Converts zeroconf's own internal-thread
    callbacks into plain function calls on a dedicated dispatch thread
    before they ever reach caller code (§115) -- callers' callbacks never
    run "under" zeroconf's own locks."""

    def __init__(self) -> None:
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._on_found: list[DiscoveryCallback] = []
        self._on_removed: list[RemovalCallback] = []
        self._lock = threading.Lock()
        self._dispatch_queue: list[Callable[[], None]] = []
        self._dispatch_event = threading.Event()
        self._dispatch_thread: threading.Thread | None = None
        self._running = False

    def subscribe(self, *, on_found: DiscoveryCallback | None = None, on_removed: RemovalCallback | None = None) -> None:
        with self._lock:
            if on_found is not None:
                self._on_found.append(on_found)
            if on_removed is not None:
                self._on_removed.append(on_removed)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatch_thread.start()
        self._zeroconf = Zeroconf()
        self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, _Listener(self))

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._dispatch_event.set()
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=5)
            self._dispatch_thread = None
        if self._zeroconf is not None:
            self._zeroconf.close()
            self._zeroconf = None
        self._browser = None

    # --- called from zeroconf's own internal thread -----------------------

    def _queue(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._dispatch_queue.append(fn)
        self._dispatch_event.set()

    def _dispatch_loop(self) -> None:
        while self._running:
            self._dispatch_event.wait(timeout=0.5)
            self._dispatch_event.clear()
            with self._lock:
                pending = self._dispatch_queue
                self._dispatch_queue = []
            for fn in pending:
                try:
                    fn()
                except Exception:
                    # A bad subscriber must never kill discovery (mirrors
                    # A13's DeviceHandoffService subscriber isolation).
                    pass


class _Listener(ServiceListener):
    def __init__(self, service: FriendSendDiscoveryService) -> None:
        self._service = service

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info is None or not info.addresses:
            return
        properties = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        try:
            device = DiscoveredFriendSendDevice(
                device_id=properties["device_id"],
                endpoint=FriendSendEndpoint(host=info.parsed_addresses()[0], port=info.port),
                protocol_version=int(properties.get("protocol_version", "0")),
                security_profile=properties.get("security_profile", ""),
            )
        except (KeyError, ValueError):
            return  # malformed advertisement -- never crash discovery (§111 spirit)

        def _fire():
            with self._service._lock:
                callbacks = list(self._service._on_found)
            for cb in callbacks:
                cb(device)

        self._service._queue(_fire)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        device_id = name.split(".")[0]

        def _fire():
            with self._service._lock:
                callbacks = list(self._service._on_removed)
            for cb in callbacks:
                cb(device_id)

        self._service._queue(_fire)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)
