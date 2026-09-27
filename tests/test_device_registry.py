from datetime import datetime, timezone

import pytest

from rychlik.device.contracts import FriendSendDevice, FriendSendEndpoint, UnknownDeviceError
from rychlik.device.registry import PairedDeviceRegistry

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _device(device_id="d1"):
    return FriendSendDevice(
        device_id=device_id, display_name="Phone", platform="test",
        endpoint=FriendSendEndpoint(host="127.0.0.1", port=1234),
        protocol_version=1, capabilities=frozenset(), auth_token="tok", paired_at_utc=T0,
    )


def test_unknown_device_raises():
    registry = PairedDeviceRegistry()
    with pytest.raises(UnknownDeviceError):
        registry.get("ghost")


def test_add_and_get():
    registry = PairedDeviceRegistry()
    registry.add(_device())
    assert registry.get("d1").device_id == "d1"


def test_all_devices():
    registry = PairedDeviceRegistry()
    registry.add(_device("a"))
    registry.add(_device("b"))
    assert {d.device_id for d in registry.all_devices()} == {"a", "b"}


def test_remove():
    registry = PairedDeviceRegistry()
    registry.add(_device())
    registry.remove("d1")
    with pytest.raises(UnknownDeviceError):
        registry.get("d1")
