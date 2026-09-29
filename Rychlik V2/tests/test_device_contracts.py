import pytest

from rychlik.device.contracts import (
    DeviceHandoffRequest,
    FriendSendDevice,
    FriendSendEndpoint,
)
from datetime import datetime, timezone

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_endpoint_rejects_empty_host():
    with pytest.raises(ValueError):
        FriendSendEndpoint(host="", port=1234)


def test_endpoint_rejects_invalid_port():
    with pytest.raises(ValueError):
        FriendSendEndpoint(host="127.0.0.1", port=0)
    with pytest.raises(ValueError):
        FriendSendEndpoint(host="127.0.0.1", port=70000)


def test_device_requires_device_id_and_token():
    endpoint = FriendSendEndpoint(host="127.0.0.1", port=1234)
    with pytest.raises(ValueError):
        FriendSendDevice(
            device_id="", display_name="x", platform="test", endpoint=endpoint,
            protocol_version=1, capabilities=frozenset(), auth_token="tok", paired_at_utc=T0,
        )
    with pytest.raises(ValueError):
        FriendSendDevice(
            device_id="d1", display_name="x", platform="test", endpoint=endpoint,
            protocol_version=1, capabilities=frozenset(), auth_token="", paired_at_utc=T0,
        )


def test_device_requires_timezone_aware_paired_at():
    endpoint = FriendSendEndpoint(host="127.0.0.1", port=1234)
    with pytest.raises(ValueError):
        FriendSendDevice(
            device_id="d1", display_name="x", platform="test", endpoint=endpoint,
            protocol_version=1, capabilities=frozenset(), auth_token="tok",
            paired_at_utc=datetime(2026, 1, 1),  # naive
        )


def test_handoff_request_validates_sha256_format():
    with pytest.raises(ValueError):
        DeviceHandoffRequest(
            handoff_id="h1", device_id="d1", artifact_id="a1", display_name="x",
            mime_type="video/mp4", size_bytes=10, sha256="not-a-hash",
        )


def test_handoff_request_rejects_negative_size():
    with pytest.raises(ValueError):
        DeviceHandoffRequest(
            handoff_id="h1", device_id="d1", artifact_id="a1", display_name="x",
            mime_type="video/mp4", size_bytes=-1, sha256="a" * 64,
        )


def test_handoff_request_valid():
    req = DeviceHandoffRequest(
        handoff_id="h1", device_id="d1", artifact_id="a1", display_name="x",
        mime_type="video/mp4", size_bytes=10, sha256="a" * 64,
    )
    assert req.handoff_id == "h1"
