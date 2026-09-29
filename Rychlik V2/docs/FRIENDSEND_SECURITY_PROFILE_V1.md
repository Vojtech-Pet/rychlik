# FriendSend Security Profile v1 (`pinned-tls-signature-v1`)

Normative for Prompt A15. This document is authoritative for the secure
Device Mode transport/trust layer; [FRIENDSEND_PROTOCOL_V1.md](FRIENDSEND_PROTOCOL_V1.md)
remains authoritative for the application-level handoff semantics
(offer/stream, `HandoffState`, acknowledgement meanings) -- this profile
adds a transport/authentication layer underneath that protocol, and does
not redefine it.

## 1. Identity types

**FriendSend TLS identity** -- a self-signed ECDSA P-256 X.509
certificate + private key, generated once, persisted in app-private
storage (`friendsend/lib/security/tls_identity.dart`). The trust anchor
is the certificate's SubjectPublicKeyInfo (SPKI), pinned by SHA-256
(`tls_spki_sha256`, 64 lowercase hex characters) -- never the
certificate's subject/CN, never a hostname or IP.

**Desktop identity** -- a stable, random `desktop_instance_id` (UUID4)
plus an Ed25519 signing keypair
(`src/rychlik/device/security/identity.py`). Used to authenticate the
desktop to a paired FriendSend device for every handoff. Never reused as
a TLS identity (the desktop is a client, not a TLS server, in this
profile).

## 2. Pairing transcript

Canonical, length-prefixed byte encoding (never arbitrary JSON from two
serializers) -- see `src/rychlik/device/security/canonical.py` /
`friendsend/lib/security/canonical.dart`, verified byte-for-byte
identical by shared vectors in `protocol/fixtures/security_v1/`.

Each field: `<4-byte big-endian length><raw bytes>` (integers encoded as
their decimal ASCII string; byte blobs raw). Fields, in this exact order:

```text
security_profile
protocol_version
pairing_session_id
desktop_instance_id
desktop_public_signing_key   (32 raw bytes)
desktop_nonce                (32 raw bytes)
device_id
friendsend_display_name
friendsend_tls_spki_sha256   (32 raw bytes)
friendsend_endpoint_host
friendsend_endpoint_port
device_nonce                 (32 raw bytes)
```

## 3. HMAC bootstrap proof

```text
proof_a = HMAC-SHA256(pairing_secret, "FRIENDSEND-PAIRING-A15-PROOF-A" + transcript)
proof_b = HMAC-SHA256(pairing_secret, "FRIENDSEND-PAIRING-A15-PROOF-B" + transcript)
```

The domain-separated prefixes prevent replaying one party's proof as the
other's. The pairing secret itself (128-bit minimum, `secrets.token_hex(16)`)
never crosses the network -- only these two proofs do.

## 4. Pairing wire flow

Protocol v1 does not mandate a wire endpoint for pairing completion (see
`FRIENDSEND_PROTOCOL_V1.md`, "Pairing semantics") -- A15 introduces one,
using a short-lived, plain-HTTP listener the **desktop** opens only while
a pairing session is pending (`SecurePairingManager` /
`pairing_bootstrap.py`). The `PairingBootstrapPayload` the user copies
from desktop to phone carries the desktop's own bootstrap endpoint (not
FriendSend's), the pairing secret, and the desktop's public identity.

```text
Round 1: FriendSend -> POST http://<desktop_bootstrap_endpoint>/pairing/offer
           body: {pairing_session_id, device_id, friendsend_display_name,
                   friendsend_tls_spki_sha256, friendsend_endpoint,
                   device_nonce, protocol_version, security_profile,
                   proof_a}
         Desktop verifies proof_a. If valid: STAGES (does not yet
           persist) the resulting TrustedFriendSendDevice record, and
           responds 200 {"accepted": true, "proof_b": <hex>}.
           If invalid: no trust, bounded error response.

Round 2: FriendSend verifies proof_b using its own copy of the secret.
           If valid: persists TrustedDesktop locally, THEN sends:
         FriendSend -> POST http://<desktop_bootstrap_endpoint>/pairing/confirm
           body: {pairing_session_id}
         Desktop, only now, persists the staged TrustedFriendSendDevice
           and marks the session consumed.
```

This ordering means a connection failure at any point before the desktop
receives `/pairing/confirm` leaves the desktop side with **no persisted
trust** -- there is no window where only one side is trusted (§34/§35 of
the A15 prompt; proven directly by the restart/half-pair reasoning in the
RESULT document).

Plain HTTP is acceptable here **only** because: the pairing secret itself
never appears on the wire (only HMAC proofs of it do); a substituted
identity is caught by the transcript binding both public keys; and no
media/private payload is ever sent over this listener.

## 5. TLS pin verification (production handoff path)

Before any Protocol v1 application byte is sent:

```text
1. TCP connect to the trusted device's last-known endpoint
2. TLS handshake (self-signed cert accepted at the TLS layer --
   there is no CA here, §7)
3. Extract the peer certificate's SPKI, compute SHA-256
4. Compare to the persisted tls_spki_sha256 pin
5. Mismatch -> close immediately, TLS_PIN_MISMATCH, zero payload bytes
6. Match -> proceed
```

Implemented in `rychlik.device.security.secure_transport.connect_and_verify_pin`
using a manual `ssl.SSLContext` (never `verify=False` followed by "send
now, check later" -- the pin check happens before the caller ever
receives a usable socket).

## 6. Desktop authentication (Ed25519 challenge/response)

After the TLS pin succeeds, FriendSend issues a bounded, one-time,
expiring challenge (`AuthChallengeManager`, default 45s TTL, 256-bit
nonce). The desktop signs a canonical, handoff-bound message:

```text
security_profile, protocol_version, desktop_instance_id, device_id,
challenge_id, challenge_nonce, handoff_id, artifact_sha256, artifact_size_bytes
```

FriendSend verifies the Ed25519 signature against the persisted
`desktop_public_signing_key` for that `desktop_instance_id`. The
challenge is consumed (removed) the moment it is looked up, regardless of
outcome, so a captured valid signature can never be replayed for a
second handoff (`AUTH_REPLAY`). Binding the signature to `handoff_id` +
`artifact_sha256` + `artifact_size_bytes` means a valid signature for one
transfer never authorizes a different one.

## 7. Secure handoff flow

```text
mDNS/last-known endpoint (untrusted candidate)
  -> TLS connect + SPKI pin verified
  -> GET /auth/challenge
  -> desktop signs, POST /handoff/offer (+ signature headers)
  -> receiver preflight (capability/size/MIME, Protocol v1)
  -> POST /handoff/stream (bounded chunks, incremental SHA-256)
  -> size + hash verified (Protocol v1, unchanged)
  -> RECEIVED
```

A15 does not replace Protocol v1's own artifact SHA-256/size
verification (§52) -- transport integrity (TLS) and file identity
(SHA-256) are separate, both-required guarantees.

## 8. Downgrade protection

A device persistently paired under `pinned-tls-signature-v1` never
automatically falls back to `plain-http-bearer-v1` (the A13/A14 legacy
profile, retained only for isolated tests/dev fixtures, §3). There is no
code path in `DeviceHandoffService`/`SecureFriendSendTransport` that
selects the legacy transport based on a TLS failure.

## 9. Discovery is not trust

mDNS/DNS-SD (`_friendsend._tcp.local.`) advertises only non-secret
metadata: `device_id`, `protocol_version`, `security_profile`. A
discovered record (`DiscoveredFriendSendDevice`) is a completely separate
type from a persisted `TrustedFriendSendDevice` (§63) -- discovery can
update a trusted device's **candidate endpoint**, but only after §5's TLS
pin check independently succeeds against that new endpoint. A spoofed
advertisement (same `device_id`, different real endpoint/identity) is
found by discovery but rejected at the pin-check step with zero payload
bytes -- proven in `test_real_mdns_spoof_combined_with_tls_pin_e2e`.

## 10. Threat model

Protects against a LAN attacker attempting:

- passive media sniffing (TLS encrypts the handoff payload);
- endpoint spoofing / mDNS spoofing (SPKI pin rejects it, §9 above);
- device impersonation (a spoofed device_id cannot present the real
  device's private key, so it cannot pass the TLS pin check);
- desktop impersonation (Ed25519 signature verification against the
  persisted public key);
- pairing-secret substitution (the transcript HMAC binds both public
  keys and both nonces -- an attacker without the secret cannot forge a
  valid proof for a different identity pair);
- authentication replay (one-time challenge consumption + handoff/
  artifact-bound signature).

**Explicitly out of scope** (no security theater -- these are not solved
by this profile):

- a fully compromised Android device or desktop account;
- malware with direct access to the private-key files on either side;
- a user who is socially engineered into pasting a malicious pairing
  payload from an untrusted source;
- confidentiality of the pairing bootstrap metadata itself (device_id,
  desktop_instance_id, public keys, endpoints) against a passive LAN
  observer -- only the pairing *secret* is protected from disclosure, not
  every field in the bootstrap exchange (§33's own scoping).

## 11. Known limitations

- No real Android emulator/physical-device E2E for the mDNS advertisement
  half (`A15-PHYSICAL-MDNS-DISCOVERY`, OPEN) -- desktop-side discovery is
  proven with a real second `zeroconf` advertiser instead.
- No certificate/key rotation support -- a changed TLS pin or desktop
  signing key always requires an explicit forget + re-pair (§138, by
  design, conservative).
- No production discovery UX (QR pairing, automatic endpoint healing UI)
  -- functional/manual only, per the A15 prompt's own scope.
