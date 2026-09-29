"""Deterministic canonical byte encoding for the Prompt A15 pairing
transcript and auth-challenge signature input.

Never signs/HMACs arbitrary JSON produced by two different serializers
(§30 of the A15 prompt) -- every field is length-prefixed
(4-byte big-endian length + raw bytes), concatenated in a fixed order.
This scheme is deliberately simple (no whitespace/escaping ambiguity is
even possible) so it is trivial to reproduce byte-for-byte in Dart --
see friendsend/lib/security/canonical.dart, and
protocol/fixtures/security_v1/*.json for shared cross-language test
vectors both sides validate against.
"""

from __future__ import annotations

import struct


def encode_field(value: bytes | str | int) -> bytes:
    """Length-prefixes one field. `str` is UTF-8 encoded; `int` is encoded
    as its decimal ASCII string (never raw binary -- avoids any
    endianness/width ambiguity for plain integers; byte blobs like a
    public key or nonce are passed as `bytes` directly)."""
    if isinstance(value, int):
        raw = str(value).encode("utf-8")
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = value
    return struct.pack(">I", len(raw)) + raw


def canonical_bytes(*fields: bytes | str | int) -> bytes:
    """Concatenates `encode_field()` for each field, in the exact order
    given -- the order itself is part of the contract and must match the
    field lists documented in docs/FRIENDSEND_SECURITY_PROFILE_V1.md."""
    return b"".join(encode_field(f) for f in fields)


PAIRING_PROOF_A_DOMAIN = b"FRIENDSEND-PAIRING-A15-PROOF-A"
PAIRING_PROOF_B_DOMAIN = b"FRIENDSEND-PAIRING-A15-PROOF-B"


def pairing_transcript(
    *,
    security_profile: str,
    protocol_version: int,
    pairing_session_id: str,
    desktop_instance_id: str,
    desktop_public_signing_key: bytes,
    desktop_nonce: bytes,
    device_id: str,
    friendsend_display_name: str,
    friendsend_tls_spki_sha256: bytes,
    friendsend_endpoint_host: str,
    friendsend_endpoint_port: int,
    device_nonce: bytes,
) -> bytes:
    """The canonical pairing transcript (A15 prompt §28) -- binds both
    cryptographic identities (desktop signing key, FriendSend TLS SPKI
    pin) plus both nonces, so a LAN attacker cannot substitute either
    identity without knowing the pairing secret (§31)."""
    return canonical_bytes(
        security_profile,
        protocol_version,
        pairing_session_id,
        desktop_instance_id,
        desktop_public_signing_key,
        desktop_nonce,
        device_id,
        friendsend_display_name,
        friendsend_tls_spki_sha256,
        friendsend_endpoint_host,
        friendsend_endpoint_port,
        device_nonce,
    )


def auth_signature_input(
    *,
    security_profile: str,
    protocol_version: int,
    desktop_instance_id: str,
    device_id: str,
    challenge_id: str,
    challenge_nonce: bytes,
    handoff_id: str,
    artifact_sha256: bytes,
    artifact_size_bytes: int,
) -> bytes:
    """The canonical bytes the desktop signs (Ed25519) to authenticate
    itself for one specific handoff (A15 prompt §46) -- binds the
    signature to the exact handoff/artifact identity so a captured
    signature cannot authorize a different transfer (§49/§105/§106)."""
    return canonical_bytes(
        security_profile,
        protocol_version,
        desktop_instance_id,
        device_id,
        challenge_id,
        challenge_nonce,
        handoff_id,
        artifact_sha256,
        artifact_size_bytes,
    )
