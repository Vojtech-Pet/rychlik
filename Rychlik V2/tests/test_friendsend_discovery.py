"""Prompt A15: real desktop-side mDNS/DNS-SD discovery.

Uses a real, second `zeroconf.Zeroconf` instance to register a real
service advertisement (standing in for a FriendSend device -- no
hardcoded fixture injection into the discovery code itself, §107) and
proves the real `FriendSendDiscoveryService` (backed by the same
`zeroconf` library) finds it through genuine multicast DNS-SD traffic on
the loopback/local network, not a mock.
"""

from __future__ import annotations

import socket
import time

import pytest
from rychlik.device.security.discovery import SERVICE_TYPE, FriendSendDiscoveryService
from zeroconf import ServiceInfo, Zeroconf


def _wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _local_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class _Advertiser:
    def __init__(self) -> None:
        self.zc = Zeroconf()
        self.registered: list[ServiceInfo] = []

    def register(self, *, device_id: str, port: int, protocol_version: int = 1, security_profile: str = "pinned-tls-signature-v1"):
        info = ServiceInfo(
            SERVICE_TYPE,
            f"{device_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(_local_ipv4())],
            port=port,
            properties={
                "device_id": device_id,
                "protocol_version": str(protocol_version),
                "security_profile": security_profile,
            },
        )
        self.zc.register_service(info)
        self.registered.append(info)
        return info

    def unregister(self, info: ServiceInfo) -> None:
        self.zc.unregister_service(info)
        if info in self.registered:
            self.registered.remove(info)

    def close(self) -> None:
        for info in self.registered:
            try:
                self.zc.unregister_service(info)
            except Exception:
                pass
        self.zc.close()


@pytest.fixture
def advertiser():
    adv = _Advertiser()
    yield adv
    adv.close()


def test_real_discovery_finds_a_real_advertised_service(advertiser):
    found = []
    service = FriendSendDiscoveryService()
    service.subscribe(on_found=found.append)
    service.start()
    try:
        advertiser.register(device_id="disco-device-1", port=54321)
        assert _wait_until(lambda: any(d.device_id == "disco-device-1" for d in found), timeout=15)
        device = next(d for d in found if d.device_id == "disco-device-1")
        assert device.endpoint.port == 54321
        assert device.protocol_version == 1
        assert device.security_profile == "pinned-tls-signature-v1"
    finally:
        service.stop()


def test_real_discovery_removal_transitions_online_to_offline(advertiser):
    found = []
    removed = []
    service = FriendSendDiscoveryService()
    service.subscribe(on_found=found.append, on_removed=removed.append)
    service.start()
    try:
        info = advertiser.register(device_id="disco-device-2", port=54322)
        assert _wait_until(lambda: any(d.device_id == "disco-device-2" for d in found), timeout=15)

        advertiser.unregister(info)  # zeroconf sends a real goodbye packet
        assert _wait_until(lambda: "disco-device-2" in removed, timeout=15)
    finally:
        service.stop()


def test_discovery_alone_never_grants_trust_no_persistence_side_effect(advertiser):
    """§57: discovery produces only ephemeral DiscoveredFriendSendDevice
    records -- this test asserts the discovery service itself has no API
    that could persist trust, by construction (no trust_store reference
    anywhere in FriendSendDiscoveryService)."""
    import inspect

    from rychlik.device.security.discovery import FriendSendDiscoveryService

    source = inspect.getsource(FriendSendDiscoveryService)
    assert "trust_store" not in source
    assert "TrustedFriendSendDevice" not in source
