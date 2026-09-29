"""Prompt A15: the canonical transcript/signature-input encoding must be
byte-for-byte reproducible -- these tests pin the Python side against the
shared cross-language vectors in protocol/fixtures/security_v1/, exactly
as friendsend/test/security_canonical_test.dart pins the Dart side
against the same files (§30/§93/§94 of the A15 prompt)."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from rychlik.device.security.canonical import (
    PAIRING_PROOF_A_DOMAIN,
    PAIRING_PROOF_B_DOMAIN,
    auth_signature_input,
    pairing_transcript,
)

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "protocol" / "fixtures" / "security_v1"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text())


def test_pairing_transcript_matches_shared_vector():
    vector = _load("pairing_transcript.json")
    inputs = vector["inputs"]
    transcript = pairing_transcript(
        security_profile=inputs["security_profile"],
        protocol_version=inputs["protocol_version"],
        pairing_session_id=inputs["pairing_session_id"],
        desktop_instance_id=inputs["desktop_instance_id"],
        desktop_public_signing_key=bytes.fromhex(inputs["desktop_public_signing_key_hex"]),
        desktop_nonce=bytes.fromhex(inputs["desktop_nonce_hex"]),
        device_id=inputs["device_id"],
        friendsend_display_name=inputs["friendsend_display_name"],
        friendsend_tls_spki_sha256=bytes.fromhex(inputs["friendsend_tls_spki_sha256_hex"]),
        friendsend_endpoint_host=inputs["friendsend_endpoint_host"],
        friendsend_endpoint_port=inputs["friendsend_endpoint_port"],
        device_nonce=bytes.fromhex(inputs["device_nonce_hex"]),
    )
    assert transcript.hex() == vector["expected_transcript_hex"]
    assert len(transcript) == vector["expected_transcript_length"]

    secret = vector["pairing_secret"].encode()
    proof_a = hmac.new(secret, PAIRING_PROOF_A_DOMAIN + transcript, hashlib.sha256).hexdigest()
    proof_b = hmac.new(secret, PAIRING_PROOF_B_DOMAIN + transcript, hashlib.sha256).hexdigest()
    assert proof_a == vector["expected_proof_a_hmac_sha256_hex"]
    assert proof_b == vector["expected_proof_b_hmac_sha256_hex"]
    assert proof_a != proof_b  # domain separation actually changes the output


def test_auth_signature_input_matches_shared_vector():
    vector = _load("signed_handoff.json")
    inputs = vector["inputs"]
    sig_input = auth_signature_input(
        security_profile=inputs["security_profile"],
        protocol_version=inputs["protocol_version"],
        desktop_instance_id=inputs["desktop_instance_id"],
        device_id=inputs["device_id"],
        challenge_id=inputs["challenge_id"],
        challenge_nonce=bytes.fromhex(inputs["challenge_nonce_hex"]),
        handoff_id=inputs["handoff_id"],
        artifact_sha256=bytes.fromhex(inputs["artifact_sha256_hex"]),
        artifact_size_bytes=inputs["artifact_size_bytes"],
    )
    assert sig_input.hex() == vector["expected_signature_input_hex"]
    assert len(sig_input) == vector["expected_signature_input_length"]

    seed = bytes.fromhex(vector["ed25519_private_key_seed_hex"])
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    signature = private_key.sign(sig_input)
    assert signature.hex() == vector["expected_signature_hex"]

    from cryptography.hazmat.primitives import serialization

    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert public_bytes.hex() == vector["ed25519_public_key_hex"]
    public_key.verify(bytes.fromhex(vector["expected_signature_hex"]), sig_input)  # raises on mismatch
