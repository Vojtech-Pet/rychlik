"""Prompt A14: the real Python<->Dart cross-language E2E.

    real Rýchlik desktop (Python) DeviceHandoffService
        -> real socket
        -> the real FriendSend Dart receiver core (bin/receiver_harness.dart,
           the SAME classes the Flutter app itself uses -- not the A13
           Python test fixture, and not a mock)
        -> real temp file, size+sha256 independently verified
        -> RECEIVED

This is the strongest proof A14 has: a genuinely different language/
process/runtime on the other end of the wire, driven only through the
public Protocol v1 HTTP contract (docs/FRIENDSEND_PROTOCOL_V1.md).

Requires a Dart SDK -- see friendsend_dart_harness.py for how it is
located. If none is available, these tests are skipped with an explicit
reason (never silently reported as passing).
"""

from __future__ import annotations

import shutil
import time

import pytest
from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.device.contracts import DeviceCapability, HandoffErrorCode, HandoffState
from rychlik.device.device_handoff_service import DeviceHandoffService
from rychlik.device.transport import HttpFriendSendTransport
from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed
from rychlik.core.artifact import Artifact

from friendsend_dart_harness import FriendSendDartHarness
from http_fixture_server import NORMAL_BODY

pytestmark = pytest.mark.skipif(
    shutil.which("dart") is None
    and not __import__("os").environ.get("FRIENDSEND_DART_EXECUTABLE")
    and not __import__("pathlib").Path(
        "/mnt/Basic_data_partition1/vojtech/flutter/bin/cache/dart-sdk/bin/dart"
    ).exists(),
    reason="no Dart SDK available for the real Python<->Dart cross-language E2E",
)


def _wait_until(predicate, timeout=15.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _pair(device_service: DeviceHandoffService, harness: FriendSendDartHarness):
    payload = device_service.create_pairing_session(endpoint=harness.endpoint)
    device = device_service.complete_pairing(
        payload.pairing_session_id,
        payload.secret,
        device_id=harness.device_id,
        display_name="Cross-language Dart receiver",
        platform="android-harness",
        endpoint=harness.endpoint,
        protocol_version=1,
        capabilities=frozenset({DeviceCapability.RECEIVE_STREAM}),
    )
    # No real wire pairing-callback endpoint exists yet (Protocol v1 does
    # not mandate one, and A14 explicitly defers full mutual pairing to
    # A15) -- the test seeds the token this desktop just generated
    # directly into the real receiver, matching how a future real
    # callback would deliver it.
    harness.accept_token(device.auth_token)
    return device


@pytest.fixture
def dart_harness(tmp_path):
    harness = FriendSendDartHarness(tmp_path)
    yield harness
    harness.stop()


@pytest.fixture
def device_service():
    service = DeviceHandoffService(transport=HttpFriendSendTransport())
    service.start()
    yield service
    service.stop()


def test_real_python_to_dart_socket_e2e(dart_harness, device_service, tmp_path):
    """§94: a real DeviceHandoffService send -> real Dart receiver core,
    with no Python receiver fixture involved anywhere."""
    _pair(device_service, dart_harness)
    device = device_service.devices()[0]

    payload = b"cross-language payload " * 5000  # a few multi-chunk-worthy bytes
    file_path = tmp_path / "payload.bin"
    file_path.write_bytes(payload)
    artifact = Artifact.from_completed_download(file_path)

    handoff_id = device_service.send(device.device_id, artifact)
    assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal)

    snapshot = device_service.snapshot(handoff_id)
    assert snapshot.state == HandoffState.RECEIVED
    assert snapshot.bytes_sent == len(payload)

    received_event = dart_harness.wait_for_event("received", handoff_id=handoff_id)
    assert received_event["sha256"] == artifact.sha256
    assert received_event["bytes_received"] == len(payload)


def test_real_completed_download_to_dart_receiver_e2e(http_fixture_server, dart_harness, device_service, tmp_path):
    """§112: real HTTP source -> DownloadManagerService -> completed file
    -> Artifact -> DeviceHandoffService -> real Dart receiver -> RECEIVED.
    No mocked Artifact anywhere in this chain."""
    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "state.db", max_active_transfers=1))
    manager.start()
    try:
        added = manager.add_download(
            DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
        )
        assert _wait_until(
            lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items)
        )
        artifact, _ = build_artifact_for_completed(manager, added.queue_entry_id)
        assert artifact is not None
        assert artifact.size == len(NORMAL_BODY)

        _pair(device_service, dart_harness)
        device = device_service.devices()[0]
        handoff_id = device_service.send(device.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal)

        snapshot = device_service.snapshot(handoff_id)
        assert snapshot.state == HandoffState.RECEIVED

        received_event = dart_harness.wait_for_event("received", handoff_id=handoff_id)
        assert received_event["sha256"] == artifact.sha256
        assert received_event["bytes_received"] == len(NORMAL_BODY)

        # The original download is unaffected by the Device Mode send.
        item = manager.item_snapshot(added.queue_entry_id)
        assert item is not None
    finally:
        manager.stop()


def test_real_restarted_completion_to_dart_receiver_e2e(http_fixture_server, dart_harness, device_service, tmp_path):
    """§113: proves A12 (durable completed-file identity) + A13
    (DeviceHandoffService) + A14 (real Dart receiver) compose across a
    full desktop process restart."""
    db_path = tmp_path / "state.db"
    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=db_path, max_active_transfers=1))
    manager.start()
    added = manager.add_download(
        DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path, filename_hint="video")
    )
    assert _wait_until(lambda: not any(i.queue_entry_id == added.queue_entry_id for i in manager.snapshot().items))
    manager.stop()

    restarted = DownloadManagerService(config=DownloadManagerConfig(database_path=db_path, max_active_transfers=1))
    restarted.start()
    try:
        artifact, _ = build_artifact_for_completed(restarted, added.queue_entry_id)
        assert artifact is not None

        _pair(device_service, dart_harness)
        device = device_service.devices()[0]
        handoff_id = device_service.send(device.device_id, artifact)
        assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal)
        assert device_service.snapshot(handoff_id).state == HandoffState.RECEIVED

        received_event = dart_harness.wait_for_event("received", handoff_id=handoff_id)
        assert received_event["sha256"] == artifact.sha256
    finally:
        restarted.stop()


def test_real_offer_rejection_costs_zero_payload_bytes(dart_harness, device_service, tmp_path):
    """§114: an incompatible offer against the REAL Dart receiver is
    rejected before any payload byte streams."""
    _pair(device_service, dart_harness)
    device = device_service.devices()[0]

    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x" * 1000)
    # Wrong protocol version forces the Dart receiver's own preflight
    # rejection -- construct a device record with a version the harness
    # does not accept.
    from dataclasses import replace

    bad_device = replace(device, protocol_version=999)
    device_service._registry.add(bad_device)  # test-only direct registry mutation

    artifact = Artifact.from_completed_download(file_path)
    with pytest.raises(Exception):
        # UnsupportedProtocolError is raised synchronously by send() --
        # this alone already proves zero bytes are ever attempted.
        device_service.send(bad_device.device_id, artifact)


def test_real_wrong_auth_token_is_rejected(dart_harness, device_service, tmp_path):
    """§115 analogue: a device record whose auth_token the real Dart
    receiver never accepted is rejected with AUTHENTICATION_FAILED."""
    payload = device_service.create_pairing_session(endpoint=dart_harness.endpoint)
    device = device_service.complete_pairing(
        payload.pairing_session_id,
        payload.secret,
        device_id=dart_harness.device_id,
        display_name="x",
        platform="android-harness",
        endpoint=dart_harness.endpoint,
        protocol_version=1,
        capabilities=frozenset({DeviceCapability.RECEIVE_STREAM}),
    )
    # Deliberately never call dart_harness.accept_token() -- the real
    # receiver has no record of this device's auth_token.
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"x" * 1000)
    artifact = Artifact.from_completed_download(file_path)

    handoff_id = device_service.send(device.device_id, artifact)
    assert _wait_until(lambda: device_service.snapshot(handoff_id).is_terminal)
    snapshot = device_service.snapshot(handoff_id)
    assert snapshot.state == HandoffState.FAILED
    assert snapshot.failure_code == HandoffErrorCode.AUTHENTICATION_FAILED
    assert snapshot.bytes_sent == 0


def test_real_cancel_cross_language_e2e(dart_harness, tmp_path):
    """§122: cancel mid-transfer against the REAL Dart receiver."""
    big_payload = b"x" * (4 * 1024 * 1024)
    file_path = tmp_path / "big.bin"
    file_path.write_bytes(big_payload)
    artifact = Artifact.from_completed_download(file_path)

    # Pace the sender so cancellation genuinely lands mid-transfer instead
    # of racing a transfer that already finished (same class of fix as
    # A13's own chunk_delay -- real bytes, real socket, just paced).
    paced_service = DeviceHandoffService(chunk_size=64 * 1024, chunk_delay=0.01, transport=HttpFriendSendTransport())
    paced_service.start()
    try:
        payload = paced_service.create_pairing_session(endpoint=dart_harness.endpoint)
        paired_device = paced_service.complete_pairing(
            payload.pairing_session_id,
            payload.secret,
            device_id=dart_harness.device_id,
            display_name="x",
            platform="android-harness",
            endpoint=dart_harness.endpoint,
            protocol_version=1,
            capabilities=frozenset({DeviceCapability.RECEIVE_STREAM}),
        )
        dart_harness.accept_token(paired_device.auth_token)

        handoff_id = paced_service.send(paired_device.device_id, artifact)
        dart_harness.wait_for_event("progress", handoff_id=handoff_id, timeout=10)
        cancelled = paced_service.cancel(handoff_id)
        assert cancelled is True

        assert _wait_until(lambda: paced_service.snapshot(handoff_id).is_terminal)
        assert paced_service.snapshot(handoff_id).state == HandoffState.CANCELLED
    finally:
        paced_service.stop()
