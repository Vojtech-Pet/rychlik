"""Secure device media capability query (Prompt A16 §20-24).

Capability data used for transcoding decisions must come from an
authenticated relationship, never untrusted mDNS/display-name/IP data
(§20). This reuses A15's exact TLS-pin-verification mechanism
(`connect_and_verify_pin`) -- the same trust anchor already used for the
real handoff -- rather than trusting a capability reply from a
connection whose certificate was never checked. No media bytes are
involved (§22); this is a single `GET /hello` over the pin-verified
connection.
"""

from __future__ import annotations

from rychlik.device.media.capability_profile import DeviceMediaCapabilities
from rychlik.device.security.minimal_http import read_json_response, send_request
from rychlik.device.security.secure_transport import TlsPinMismatchError, connect_and_verify_pin
from rychlik.device.security.trust_store import TrustedFriendSendDevice


class CapabilityQueryFailedError(Exception):
    pass


def fetch_device_media_capabilities(trusted: TrustedFriendSendDevice, *, timeout: float = 10.0) -> DeviceMediaCapabilities:
    """§24: an older/unadvertising peer (no `media_profiles` field, or an
    empty list, or only unrecognized ids) naturally yields empty
    `DeviceMediaCapabilities` -- the planner then correctly refuses to
    transcode rather than guessing a profile is safe."""
    try:
        sock = connect_and_verify_pin(trusted.endpoint_host, trusted.endpoint_port, trusted.tls_spki_sha256, timeout=timeout)
    except TlsPinMismatchError as exc:
        raise CapabilityQueryFailedError(f"TLS pin mismatch querying capabilities: {exc}") from exc
    except OSError as exc:
        raise CapabilityQueryFailedError(f"connection failed querying capabilities: {exc}") from exc

    try:
        reader = sock.makefile("rb")
        send_request(sock, "GET", "/hello", {})
        status, body = read_json_response(reader)
    except (OSError, ValueError) as exc:
        raise CapabilityQueryFailedError(f"capability query failed: {exc}") from exc
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if status != 200:
        raise CapabilityQueryFailedError(f"capability query returned HTTP {status}")

    profile_ids = body.get("media_profiles", [])
    if not isinstance(profile_ids, list):
        profile_ids = []
    return DeviceMediaCapabilities.from_profile_ids([p for p in profile_ids if isinstance(p, str)])
