"""Prompt A16 §138: real cross-language capability query -- Python must
correctly interpret the real Dart receiver's `/hello` media_profiles
field, over the real pin-verified TLS connection."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from rychlik.device.media.capability_profile import DeviceMediaCapabilities
from rychlik.device.media.capability_query import CapabilityQueryFailedError, fetch_device_media_capabilities
from rychlik.device.security.trust_store import SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1, TrustedFriendSendDevice

from friendsend_secure_dart_harness import FriendSendSecureDartHarness

pytestmark = pytest.mark.skipif(
    shutil.which("dart") is None
    and not __import__("os").environ.get("FRIENDSEND_DART_EXECUTABLE")
    and not Path("/mnt/Basic_data_partition1/vojtech/flutter/bin/cache/dart-sdk/bin/dart").exists(),
    reason="no Dart SDK available for the real capability query E2E",
)


def _trusted(receiver) -> TrustedFriendSendDevice:
    from datetime import datetime, timezone

    return TrustedFriendSendDevice(
        device_id=receiver.device_id,
        display_name="Test Phone",
        tls_spki_sha256=receiver.tls_spki_sha256,
        protocol_version=1,
        security_profile=SECURITY_PROFILE_PINNED_TLS_SIGNATURE_V1,
        endpoint_host=receiver.host,
        endpoint_port=receiver.port,
        paired_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def test_real_capability_query_against_real_dart_receiver(tmp_path):
    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        capabilities = fetch_device_media_capabilities(_trusted(receiver))
        assert not capabilities.is_empty
        ids = {p.profile_id for p in capabilities.profiles}
        assert "friendsend-generic-video-v1" in ids
        assert "friendsend-generic-audio-v1" in ids
    finally:
        receiver.stop()


def test_wrong_pin_capability_query_fails_closed(tmp_path):
    receiver = FriendSendSecureDartHarness(tmp_path)
    try:
        trusted = _trusted(receiver)
        wrong_pin_trusted = TrustedFriendSendDevice(
            device_id=trusted.device_id,
            display_name=trusted.display_name,
            tls_spki_sha256="0" * 64,
            protocol_version=trusted.protocol_version,
            security_profile=trusted.security_profile,
            endpoint_host=trusted.endpoint_host,
            endpoint_port=trusted.endpoint_port,
            paired_at_utc=trusted.paired_at_utc,
        )
        with pytest.raises(CapabilityQueryFailedError):
            fetch_device_media_capabilities(wrong_pin_trusted)
    finally:
        receiver.stop()
