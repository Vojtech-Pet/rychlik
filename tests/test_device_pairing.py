from datetime import datetime, timedelta, timezone

import pytest

from rychlik.device.contracts import DeviceCapability, FriendSendEndpoint
from rychlik.device.pairing import (
    DEFAULT_PAIRING_TTL_SECONDS,
    PairingAuthenticationFailedError,
    PairingExpiredError,
    PairingManager,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
ENDPOINT = FriendSendEndpoint(host="127.0.0.1", port=5000)


class FakeClock:
    def __init__(self, start=T0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += timedelta(seconds=seconds)


def _complete(manager, payload, secret=None, **overrides):
    kwargs = dict(
        device_id="dev-1", display_name="Phone", platform="test",
        endpoint=ENDPOINT, protocol_version=1, capabilities=frozenset({DeviceCapability.RECEIVE_STREAM}),
    )
    kwargs.update(overrides)
    return manager.complete_pairing(payload.pairing_session_id, secret or payload.secret, **kwargs)


def test_pairing_secret_has_128_bits_of_entropy():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    # 128 bits = 16 bytes = 32 hex chars
    assert len(payload.secret) == 32
    assert payload.secret != PairingManager().create_session(endpoint=ENDPOINT).secret


def test_pairing_session_id_is_random_and_unique():
    manager = PairingManager()
    a = manager.create_session(endpoint=ENDPOINT)
    b = manager.create_session(endpoint=ENDPOINT)
    assert a.pairing_session_id != b.pairing_session_id
    assert a.secret != b.secret


def test_successful_pairing_produces_device():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    device = _complete(manager, payload)
    assert device.device_id == "dev-1"
    assert device.auth_token
    assert device.paired_at_utc.tzinfo is not None


def test_wrong_secret_rejected():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    with pytest.raises(PairingAuthenticationFailedError):
        _complete(manager, payload, secret="0" * 32)


def test_unknown_session_id_rejected():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    with pytest.raises(PairingAuthenticationFailedError):
        manager.complete_pairing(
            "unknown-session", payload.secret, device_id="d", display_name="x", platform="t",
            endpoint=ENDPOINT, protocol_version=1, capabilities=frozenset(),
        )


def test_pairing_expires():
    clock = FakeClock()
    manager = PairingManager(clock=clock, ttl_seconds=300)
    payload = manager.create_session(endpoint=ENDPOINT)
    clock.advance(301)
    with pytest.raises(PairingExpiredError):
        _complete(manager, payload)


def test_pairing_default_ttl_matches_spec():
    assert DEFAULT_PAIRING_TTL_SECONDS == 300


def test_pairing_is_one_time_replay_rejected():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    _complete(manager, payload)  # first use succeeds
    with pytest.raises(PairingExpiredError):
        _complete(manager, payload)  # replay of the same session+secret


def test_payload_redacted_hides_secret():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    redacted = payload.redacted()
    assert redacted["secret"] != payload.secret
    assert payload.secret not in str(redacted)


def test_payload_wire_dict_carries_real_secret_explicitly():
    manager = PairingManager()
    payload = manager.create_session(endpoint=ENDPOINT)
    wire = payload.to_wire_dict()
    assert wire["secret"] == payload.secret
    assert wire["protocol_version"] == 1
