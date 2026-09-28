"""Prompt A16 §125-134: real end-to-end Device Media Compatibility
E2E -- real completed download -> Artifact -> DeviceCompatibilityPlanner
-> (passthrough / real FFmpeg remux / real FFmpeg transcode) -> real A15
secure handoff -> real Dart receiver -> RECEIVED.

No mocked transport, no mocked FFmpeg, no mocked receiver."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.device.contracts import HandoffState
from rychlik.device.device_handoff_service import DeviceHandoffService
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.pairing_bootstrap import SecurePairingManager
from rychlik.device.security.secure_transport import SecureFriendSendTransport
from rychlik.device.security.trust_store import FriendSendTrustStore
from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed

from friendsend_secure_dart_harness import FriendSendSecureDartHarness, run_dart_pairing_client
from media_fixtures import make_compatible_mp4, make_full_transcode_source, make_remux_mkv

pytestmark = pytest.mark.skipif(
    shutil.which("dart") is None
    and not __import__("os").environ.get("FRIENDSEND_DART_EXECUTABLE")
    and not Path("/mnt/Basic_data_partition1/vojtech/flutter/bin/cache/dart-sdk/bin/dart").exists(),
    reason="no Dart SDK available for the real device media handoff E2E",
)


def _wait_until(predicate, timeout=20.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _download_manager(tmp_path, **overrides) -> DownloadManagerService:
    return DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", **overrides))


def _pair_secure(tmp_path, receiver: FriendSendSecureDartHarness):
    import json

    identity = DesktopIdentityStore(tmp_path / "desktop_identity").load_or_create()
    trust_store = FriendSendTrustStore(tmp_path / "trust.json")
    manager = SecurePairingManager(identity=identity, trust_store=trust_store)
    payload = manager.create_session()
    try:
        result = run_dart_pairing_client(
            identity_dir=receiver.identity_dir,
            device_id=receiver.device_id,
            display_name="Test Phone",
            friendsend_host=receiver.host,
            friendsend_port=receiver.port,
            payload_json_line=json.dumps(payload.to_wire_dict()),
        )
        assert result["pairing_result"] == "ok", result
    finally:
        manager.stop()
    return identity, trust_store


def _download_real_fixture(manager, http_fixture_server, source_path: Path, key: str, tmp_path):
    body = source_path.read_bytes()
    http_fixture_server.configure_resumable(key, body=body)
    added = manager.add_download(
        DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/{key}", destination_dir=tmp_path, filename_hint=source_path.name)
    )
    assert _wait_until(lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items))
    artifact, _ = build_artifact_for_completed(manager, added.queue_entry_id)
    assert artifact is not None
    assert artifact.sha256 == hashlib.sha256(body).hexdigest()
    return added, artifact


def test_real_passthrough_handoff_e2e(http_fixture_server, tmp_path):
    """§125: already-compatible media -> PASSTHROUGH -> receiver bytes/
    hash match the ORIGINAL Artifact exactly."""
    source = make_compatible_mp4(tmp_path / "source.mp4")
    manager = _download_manager(tmp_path / "dl", max_active_transfers=1)
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    try:
        _added, artifact = _download_real_fixture(manager, http_fixture_server, source, "passthrough", tmp_path / "dl")
        identity, trust_store = _pair_secure(tmp_path, receiver)

        device_service = DeviceHandoffService(
            transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store)
        )
        device_service.start()

        # The secure profile's registry is independent of the legacy
        # PairedDeviceRegistry that DeviceHandoffService.send() looks up
        # by device_id -- register a minimal FriendSendDevice entry so
        # send()'s existing protocol/capability checks pass; routing/pin
        # itself comes from the trust store via SecureFriendSendTransport.
        _register_legacy_device_record(device_service, receiver.device_id)

        handoff_id = device_service.send(receiver.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal, timeout=30)
        snapshot = device_service.snapshot(handoff_id)
        assert snapshot.state == HandoffState.RECEIVED, snapshot
        assert snapshot.preparation_kind == "PASSTHROUGH"

        received_event = receiver.wait_for_event("received", handoff_id=handoff_id)
        assert received_event["sha256"] == artifact.sha256

        # Original file on disk untouched.
        assert source.exists()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == artifact.sha256
        device_service.stop()
    finally:
        receiver.stop()


def _register_legacy_device_record(device_service: DeviceHandoffService, device_id: str) -> None:
    from datetime import datetime, timezone

    from rychlik.device.contracts import DeviceCapability, FriendSendDevice, FriendSendEndpoint

    device_service._registry.add(
        FriendSendDevice(
            device_id=device_id,
            display_name="Test Phone",
            platform="android-harness",
            endpoint=FriendSendEndpoint(host="127.0.0.1", port=1),
            protocol_version=1,
            capabilities=frozenset({DeviceCapability.RECEIVE_STREAM}),
            auth_token="unused-for-secure-profile",
            paired_at_utc=datetime.now(timezone.utc),
        )
    )


def test_real_remux_handoff_e2e(http_fixture_server, tmp_path):
    """§126: real completed MKV/H264/AAC -> real REMUX -> derived MP4
    Artifact -> secure handoff -> real Dart receiver -> RECEIVED. Original
    MKV remains untouched (§129)."""
    source = make_remux_mkv(tmp_path / "source.mkv")
    source_bytes = source.read_bytes()
    manager = _download_manager(tmp_path / "dl", max_active_transfers=1)
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    try:
        _added, artifact = _download_real_fixture(manager, http_fixture_server, source, "remux", tmp_path / "dl")
        identity, trust_store = _pair_secure(tmp_path, receiver)
        device_service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        device_service.start()
        _register_legacy_device_record(device_service, receiver.device_id)

        handoff_id = device_service.send(receiver.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal, timeout=30)
        snapshot = device_service.snapshot(handoff_id)
        assert snapshot.state == HandoffState.RECEIVED, snapshot
        assert snapshot.preparation_kind == "REMUX"

        received_event = receiver.wait_for_event("received", handoff_id=handoff_id)
        # §131: derived hash, never the source's.
        assert received_event["sha256"] != artifact.sha256

        # §129: original file integrity.
        assert source.read_bytes() == source_bytes

        # §86/§133: the desktop's own derived temp file is cleaned up
        # once the handoff reaches a terminal RECEIVED outcome.
        cache_root = device_service._media_preparation_service._temp_cache.root
        assert not cache_root.exists() or not any(cache_root.iterdir())

        device_service.stop()
    finally:
        receiver.stop()


def test_real_transcode_handoff_e2e(http_fixture_server, tmp_path):
    """§127: incompatible source (VP9/Opus/WebM) -> real FFmpeg transcode
    -> H.264/AAC MP4 derived Artifact -> secure handoff -> real Dart
    receiver -> RECEIVED."""
    source = make_full_transcode_source(tmp_path / "source.webm", duration=1.0)
    source_bytes = source.read_bytes()
    manager = _download_manager(tmp_path / "dl", max_active_transfers=1)
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    try:
        _added, artifact = _download_real_fixture(manager, http_fixture_server, source, "transcode", tmp_path / "dl")
        identity, trust_store = _pair_secure(tmp_path, receiver)
        device_service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        device_service.start()
        _register_legacy_device_record(device_service, receiver.device_id)

        handoff_id = device_service.send(receiver.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal, timeout=30)
        snapshot = device_service.snapshot(handoff_id)
        assert snapshot.state == HandoffState.RECEIVED, snapshot
        assert snapshot.preparation_kind == "TRANSCODE_AUDIO_VIDEO"

        received_event = receiver.wait_for_event("received", handoff_id=handoff_id)
        assert received_event["sha256"] != artifact.sha256
        assert source.read_bytes() == source_bytes
        device_service.stop()
    finally:
        receiver.stop()


def test_real_restarted_completion_transcode_e2e(http_fixture_server, tmp_path):
    """§128: proves A12 (durable completed-file identity) + A15 (secure
    trust) + A16 (compatibility preparation) compose across a full
    desktop process restart."""
    source = make_remux_mkv(tmp_path / "source.mkv")
    db_path = tmp_path / "dl" / "state.db"
    manager = _download_manager(tmp_path / "dl", max_active_transfers=1)
    manager.start()
    added, _artifact = _download_real_fixture(manager, http_fixture_server, source, "restart", tmp_path / "dl")
    manager.stop()

    restarted = DownloadManagerService(config=DownloadManagerConfig(database_path=db_path, max_active_transfers=1))
    restarted.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    try:
        artifact, _ = build_artifact_for_completed(restarted, added.queue_entry_id)
        assert artifact is not None

        identity, trust_store = _pair_secure(tmp_path, receiver)
        device_service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        device_service.start()
        _register_legacy_device_record(device_service, receiver.device_id)

        handoff_id = device_service.send(receiver.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal, timeout=30)
        assert device_service.snapshot(handoff_id).state == HandoffState.RECEIVED

        received_event = receiver.wait_for_event("received", handoff_id=handoff_id)
        assert received_event["sha256"] != artifact.sha256  # remuxed
        device_service.stop()
        restarted.stop()
    finally:
        receiver.stop()


def test_share_by_link_artifact_unaffected_by_device_mode_derivative(http_fixture_server, tmp_path):
    """§4/§130: Device Mode preparing and sending a transcoded derivative
    must never mutate the download's own completed-file identity --
    proven by independently re-deriving the Artifact via
    `build_artifact_for_completed` (exactly what Share by Link's own code
    path does) after the Device Mode handoff completes, and asserting it
    is still the untouched original."""
    source = make_remux_mkv(tmp_path / "source.mkv")
    source_bytes = source.read_bytes()
    manager = _download_manager(tmp_path / "dl", max_active_transfers=1)
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    try:
        added, artifact = _download_real_fixture(manager, http_fixture_server, source, "coexist", tmp_path / "dl")
        identity, trust_store = _pair_secure(tmp_path, receiver)
        device_service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        device_service.start()
        _register_legacy_device_record(device_service, receiver.device_id)

        handoff_id = device_service.send(receiver.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal, timeout=30)
        assert device_service.snapshot(handoff_id).state == HandoffState.RECEIVED
        assert device_service.snapshot(handoff_id).preparation_kind == "REMUX"
        device_service.stop()

        # Independently re-derive the Artifact the same way Share by Link
        # would -- must still be the original, byte-identical download.
        share_artifact, _ = build_artifact_for_completed(manager, added.queue_entry_id)
        assert share_artifact.sha256 == artifact.sha256
        assert share_artifact.local_path == artifact.local_path
        assert artifact.local_path.read_bytes() == source_bytes
    finally:
        receiver.stop()


def test_real_cancel_during_transcode_handoff_e2e(http_fixture_server, tmp_path):
    """§123/§73: cancelling a handoff while it is genuinely PREPARING
    (real FFmpeg actively transcoding) must terminate FFmpeg, clean up
    the derived temp file, reach CANCELLED, leave the source untouched,
    and never let any payload byte reach the receiver."""
    source = make_full_transcode_source(tmp_path / "source.webm", duration=4.0, width=640, height=480)
    source_bytes = source.read_bytes()
    manager = _download_manager(tmp_path / "dl", max_active_transfers=1)
    manager.start()
    receiver = FriendSendSecureDartHarness(tmp_path / "receiver")
    try:
        _added, artifact = _download_real_fixture(manager, http_fixture_server, source, "cancel", tmp_path / "dl")
        identity, trust_store = _pair_secure(tmp_path, receiver)
        device_service = DeviceHandoffService(transport=SecureFriendSendTransport(identity=identity, trust_store=trust_store))
        device_service.start()
        _register_legacy_device_record(device_service, receiver.device_id)

        handoff_id = device_service.send(receiver.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).state == HandoffState.PREPARING, timeout=10)
        assert _wait_until(
            lambda: (device_service.snapshot(handoff_id).preparation_progress or 0) > 0.05, timeout=10
        )
        assert device_service.cancel(handoff_id) is True

        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal, timeout=15)
        snapshot = device_service.snapshot(handoff_id)
        assert snapshot.state == HandoffState.CANCELLED, snapshot
        assert snapshot.bytes_sent == 0  # never reached the network phase

        cache_root = device_service._media_preparation_service._temp_cache.root
        assert not cache_root.exists() or not any(cache_root.iterdir())
        assert source.read_bytes() == source_bytes

        device_service.stop()
    finally:
        receiver.stop()
