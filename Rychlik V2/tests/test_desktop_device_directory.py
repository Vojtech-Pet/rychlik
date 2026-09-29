"""Final GUI/UX implementation: truthful device states, endpoint refresh without repinning, no trust from discovery."""

from datetime import datetime, timezone

from rychlik.device.contracts import FriendSendEndpoint
from rychlik.device.desktop_devices import DesktopDeviceDirectory, DeviceState
from rychlik.device.security.discovery import DiscoveredFriendSendDevice
from rychlik.device.security.trust_store import FriendSendTrustStore, TrustedFriendSendDevice

PROFILE = "pinned-tls-signature-v1"


def trusted(device_id="d1", name="Pixel 8", port=41000, pin="ab" * 32):
    return TrustedFriendSendDevice(
        device_id=device_id, display_name=name, tls_spki_sha256=pin, protocol_version=1, security_profile=PROFILE,
        endpoint_host="192.168.1.20", endpoint_port=port, paired_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def found(device_id="d1", host="192.168.1.20", port=41000, profile=PROFILE):
    return DiscoveredFriendSendDevice(device_id=device_id, endpoint=FriendSendEndpoint(host, port), protocol_version=1, security_profile=profile)


def make(tmp_path, *devices):
    store = FriendSendTrustStore(tmp_path / "trust.json")
    for d in devices:
        store.upsert(d)
    changes = []
    return store, DesktopDeviceDirectory(store, on_change=lambda: changes.append(1)), changes


def test_trusted_device_that_discovery_cannot_see_is_offline_not_unpaired(tmp_path):
    _, directory, _ = make(tmp_path, trusted())
    (row,) = directory.rows()
    assert row.state == DeviceState.TRUSTED_OFFLINE and row.trusted and not row.can_send  # remembered, just not reachable


def test_online_only_while_discovery_reports_it_with_the_same_profile(tmp_path):
    _, directory, changes = make(tmp_path, trusted())
    directory.on_discovered(found())
    assert directory.rows()[0].state == DeviceState.TRUSTED_ONLINE and directory.rows()[0].can_send
    directory.on_removed("d1")
    assert directory.rows()[0].state == DeviceState.TRUSTED_OFFLINE
    directory.on_discovered(found(profile="plain-http-bearer-v1"))  # downgrade-looking advertisement
    assert directory.rows()[0].state == DeviceState.TRUSTED_OFFLINE
    assert len(changes) >= 3


def test_discovery_alone_never_creates_trust(tmp_path):
    store, directory, _ = make(tmp_path)
    directory.on_discovered(found("stranger"))
    (row,) = directory.rows()
    assert row.state == DeviceState.UNPAIRED_DISCOVERED and not row.trusted and not row.can_send
    assert store.get("stranger") is None and store.all_devices() == ()


def test_new_endpoint_updates_metadata_only_and_never_the_pin(tmp_path):
    store, directory, _ = make(tmp_path, trusted(port=41000, pin="cd" * 32))
    directory.on_discovered(found(host="192.168.1.77", port=52000))
    record = store.get("d1")
    assert (record.endpoint_host, record.endpoint_port) == ("192.168.1.77", 52000)
    assert record.tls_spki_sha256 == "cd" * 32 and record.display_name == "Pixel 8" and record.paired_at_utc
    assert record.last_seen_at_utc is not None


def test_endpoint_of_an_unknown_or_other_profile_device_is_never_written(tmp_path):
    store, directory, _ = make(tmp_path, trusted())
    directory.on_discovered(found("stranger", port=1234))
    directory.on_discovered(found("d1", port=9999, profile="plain-http-bearer-v1"))
    assert store.get("stranger") is None and store.get("d1").endpoint_port == 41000


def test_refresh_endpoint_for_adopts_the_current_discovery_before_a_send(tmp_path):
    store, directory, _ = make(tmp_path, trusted(port=41000))
    assert directory.refresh_endpoint_for("d1") is False  # nothing discovered
    directory.on_discovered(found(port=41000))
    assert directory.refresh_endpoint_for("d1") is False  # unchanged
    directory._discovered["d1"] = found(port=60000)  # the phone restarted; discovery re-resolved  # noqa: SLF001
    assert directory.refresh_endpoint_for("d1") is True and store.get("d1").endpoint_port == 60000


def test_identity_changed_only_from_a_failed_check_cleared_by_success_or_forget(tmp_path):
    store, directory, _ = make(tmp_path, trusted())
    directory.on_discovered(found())
    directory.mark_identity_changed("d1")
    assert directory.rows()[0].state == DeviceState.IDENTITY_CHANGED and not directory.rows()[0].can_send
    directory.clear_identity_changed("d1")  # a later successful, pin-verified handoff
    assert directory.rows()[0].state == DeviceState.TRUSTED_ONLINE
    directory.mark_identity_changed("d1")
    directory.forget("d1")
    assert store.get("d1") is None
    (row,) = directory.rows()  # still visible on the LAN, but only as an unpaired device
    assert row.state == DeviceState.UNPAIRED_DISCOVERED
    directory.mark_identity_changed("ghost")  # unknown device: ignored
    assert all(r.state != DeviceState.IDENTITY_CHANGED for r in directory.rows())


def test_rows_order_trusted_first_by_name_then_unpaired_and_state_survives_restart(tmp_path):
    store, directory, _ = make(tmp_path, trusted("d2", "Tablet"), trusted("d1", "Pixel 8"))
    directory.on_discovered(found("zzz"))
    assert [r.display_name for r in directory.rows()][:2] == ["Pixel 8", "Tablet"]
    assert directory.rows()[2].state == DeviceState.UNPAIRED_DISCOVERED
    reopened = DesktopDeviceDirectory(FriendSendTrustStore(tmp_path / "trust.json"))
    assert [r.device_id for r in reopened.trusted_rows()] == ["d1", "d2"]  # persistent trust needs no re-pairing
    assert all(r.state == DeviceState.TRUSTED_OFFLINE for r in reopened.rows())  # ...and nothing is "online" until discovered
