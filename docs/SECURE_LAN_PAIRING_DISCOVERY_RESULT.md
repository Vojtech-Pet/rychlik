PHASE:
Prompt A15 — Secure LAN Pairing / Discovery / Persistent Trust

STATUS:
COMPLETE

BASELINE COMMIT:
07fb80b

ACTUAL BASELINE HEAD:
07fb80b (verified via `git rev-parse HEAD` before implementation; working
tree was clean)

FILES CHANGED:
32 files: 8 new files under `friendsend/lib/security/` (canonical.dart,
self_signed_cert.dart, tls_identity.dart, desktop_trust_store.dart,
auth_challenge.dart, secure_pairing_manager.dart,
secure_receiver_server.dart, mdns_advertiser.dart), 8 new files under
`src/rychlik/device/security/` (canonical.py, identity.py, trust_store.py,
pairing_bootstrap.py, minimal_http.py, secure_transport.py, discovery.py,
__init__.py), 2 new Dart host harnesses (bin/secure_receiver_harness.dart,
bin/pairing_client_harness.dart), 1 new shared interface
(lib/receiver/receiver_interface.dart), 2 new shared cross-language
fixtures (protocol/fixtures/security_v1/), 4 new Python test files
(test_security_canonical_vectors.py, test_friendsend_secure_lan_e2e.py,
test_friendsend_discovery.py, friendsend_secure_dart_harness.py), 2 new
Dart test files (security_canonical_test.dart,
secure_receiver_server_test.dart), 3 new docs
(FRIENDSEND_SECURITY_PROFILE_V1.md, FRIENDSEND_ANDROID_SECURITY.md,
FRIENDSEND_DESKTOP_SECURITY.md), plus modifications to
device_handoff_service.py, contracts.py, three existing A13/A14 test
files (explicit legacy-transport opt-in), main.dart, home_screen.dart,
handoff_controller.dart, receiver_server.dart, protocol.dart,
MainActivity.kt, pubspec.yaml/lock, pyproject.toml,
FRIENDSEND_PROTOCOL_V1.md, OPEN_VALIDATION_DEBT.md.

SECURITY PROFILE:
`pinned-tls-signature-v1` -- introduced as the new production default;
the A13/A14 profile is retained as `plain-http-bearer-v1`, legacy/test/
dev-only (never the silent default -- see DOWNGRADE PROTECTION below).

PROTOCOL VERSION:
Unchanged: 1. Application-level handoff semantics (offer/stream,
`HandoffState`, error codes) are untouched; this phase adds a transport/
auth layer underneath them, documented as an update note in
`docs/FRIENDSEND_PROTOCOL_V1.md` rather than a rewrite.

TLS VERSION POLICY:
`ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` (desktop) /
`SecurityContext` via `HttpServer.bindSecure` (Android) -- both
negotiate the platform's default modern TLS (effectively TLS 1.3 where
both peers support it, TLS 1.2 minimum); no obsolete TLS version was
explicitly implemented or enabled.

FRIENDSEND TLS IDENTITY:
Self-signed ECDSA P-256 X.509 certificate + private key, generated once
via a custom RFC-5480-conformant generator
(`friendsend/lib/security/self_signed_cert.dart`) built on
`package:pointycastle`'s ASN.1 primitives -- required because
`basic_utils`'s own certificate generator encodes an invalid (NULL)
signature-algorithm parameter for ECDSA that Python's `cryptography`
library refuses to parse (see BUGS FOUND).

TLS KEY STORAGE:
App-private JSON file (`{certificate_pem, private_key_pem,
public_key_pem}`) under `getApplicationSupportDirectory()`, atomic
write. Not hardware-backed (Android Keystore) in this phase -- documented
as possible future hardening, not attempted (§12 of the prompt
explicitly permits this).

TLS SPKI PIN MODEL:
SHA-256 of the certificate's SubjectPublicKeyInfo DER bytes (RFC 7469
style), computed identically by both languages from the same PEM public-
key block. The trust anchor is this pin alone -- never certificate
subject/CN, never hostname/IP.

DESKTOP IDENTITY:
Random `desktop_instance_id` (UUID4) + Ed25519 signing keypair
(`rychlik.device.security.identity.DesktopIdentity`).

DESKTOP SIGNING KEY STORAGE:
`$XDG_DATA_HOME/rychlik/friendsend/desktop_identity.json` (fallback
`~/.local/share/rychlik/friendsend/`), directory `0700`, file `0600`
(best-effort on non-POSIX), atomic write, separate from `state.db`.

DESKTOP TRUST STORE:
`rychlik.device.security.trust_store.FriendSendTrustStore` -- explicit
JSON with atomic replacement, one file, `RLock`-protected (see BUGS
FOUND for why a plain `Lock` was wrong). Never stores the pairing
secret.

ANDROID TRUST STORE:
`friendsend/lib/security/desktop_trust_store.dart::DesktopTrustStore` --
explicit JSON with atomic replacement, app-private storage, designed to
hold more than one trusted desktop.

PAIRING BOOTSTRAP:
A real, new two-round-trip wire protocol
(`rychlik.device.security.pairing_bootstrap.SecurePairingManager` /
`friendsend/lib/security/secure_pairing_manager.dart`): the desktop opens
a short-lived plain-HTTP listener only while a session is pending; the
`PairingBootstrapPayload` the user copies to the phone carries the
desktop's own bootstrap endpoint (not FriendSend's), its public signing
key, and the one-time secret. See
`docs/FRIENDSEND_SECURITY_PROFILE_V1.md` §4 for the full flow.

PAIRING SECRET POLICY:
128-bit (`secrets.token_hex(16)`), single-use, 300s TTL by default,
never transmitted over the network itself -- only two HMAC-SHA256 proofs
of it are.

PAIRING TRANSCRIPT:
Canonical, length-prefixed byte encoding (4-byte big-endian length + raw
bytes per field, fixed field order) -- never arbitrary JSON from two
serializers. Verified byte-for-byte identical between Python and Dart
via shared vectors in `protocol/fixtures/security_v1/`.

PAIRING HMAC:
`HMAC-SHA256(secret, "FRIENDSEND-PAIRING-A15-PROOF-A" + transcript)` /
`"...-PROOF-B"` -- domain-separated so one party's proof can never be
replayed as the other's.

PAIRING COMMIT MODEL:
Staged commit (§34/§35 of the prompt): the desktop only *stages* trust
after verifying proof_a (round 1); it only *persists* after the phone
sends an explicit `/pairing/confirm` (round 2), which the phone only
sends after independently verifying proof_b. A connection failure at any
point before `/pairing/confirm` leaves the desktop with no persisted
trust.

PERSISTENT TRUST:
Both sides' trust stores are plain files reloaded fresh on the next
process start -- no in-memory-only state. Proven end to end (real
process destruction + reload + a real subsequent send) by
`test_real_persistent_trust_restart_e2e`.

UNPAIR / FORGET:
`FriendSendTrustStore.remove(device_id)` (desktop) /
`DesktopTrustStore.forget(desktopInstanceId)` (Android) exist and are
directly usable; not wired into a GUI action in this phase (no final GUI
redesign, §183).

AUTH CHALLENGE MODEL:
256-bit nonce, 45s default TTL, one-time consumption regardless of
verification outcome (`friendsend/lib/security/auth_challenge.dart::AuthChallengeManager`).

DESKTOP SIGNATURE MODEL:
Ed25519 over a canonical, handoff-and-artifact-bound message
(`security_profile, protocol_version, desktop_instance_id, device_id,
challenge_id, challenge_nonce, handoff_id, artifact_sha256,
artifact_size_bytes`) -- a valid signature for one handoff never
authorizes a different one (proven directly).

REPLAY PROTECTION:
Challenge consumed on lookup regardless of outcome -- a captured valid
signature+challenge_id pair cannot be replayed for a second offer
(`AUTH_REPLAY`), proven against the real Dart receiver.

DOWNGRADE PROTECTION:
`DeviceHandoffService()` with no explicit `transport` now constructs
`SecureFriendSendTransport` by default (previously
`HttpFriendSendTransport`) -- proven by
`test_default_transport_is_secure_not_plain_http`. All existing A13/A14
tests that intentionally exercise the legacy profile now explicitly pass
`transport=HttpFriendSendTransport()`. No code path automatically falls
back to the legacy profile on a TLS failure.

SECURE HANDOFF FLOW:
mDNS/last-known endpoint (untrusted) → real TLS connect + SPKI pin
verified → `GET /auth/challenge` → desktop signs → `POST /handoff/offer`
(+ signature headers, existing Protocol v1 preflight still applies) →
`POST /handoff/stream` (unchanged bounded-chunk/incremental-SHA-256
model) → size+hash verified → `RECEIVED`.

MDNS SERVICE TYPE:
`_friendsend._tcp.local.` (desktop `zeroconf`) / `_friendsend._tcp`
(Android `NsdManager`, which appends `.local.` implicitly).

ANDROID MDNS / NSD MODEL:
`MainActivity.kt` implements `registerFriendSendService`/
`unregisterFriendSendService` via native `NsdManager`, bridged through
one narrow `MethodChannel` (`app.friendsend/mdns`,
`lib/security/mdns_advertiser.dart`). Compiles and links successfully
against the real Android SDK (debug + release APK builds both pass);
never exercised at runtime on a real Android OS -- see
`A15-PHYSICAL-MDNS-DISCOVERY`.

DESKTOP DISCOVERY MODEL:
`rychlik.device.security.discovery.FriendSendDiscoveryService` wraps one
`zeroconf.Zeroconf`/`ServiceBrowser` pair, dispatching callbacks through
one dedicated thread (never running subscriber code "under" zeroconf's
own locks, and isolating bad subscribers). Proven with a real second
`zeroconf` advertiser (not a fixture) in `tests/test_friendsend_discovery.py`.

DISCOVERY TRUST BOUNDARY:
`DiscoveredFriendSendDevice` (ephemeral) is a distinct type from
`TrustedFriendSendDevice` (persistent) with no code path connecting
them automatically. Proven both structurally
(`test_discovery_alone_never_grants_trust_no_persistence_side_effect`
inspects the discovery class's own source for the absence of any
trust-store reference) and behaviorally
(`test_real_mdns_spoof_combined_with_tls_pin_e2e`: a real spoofed
advertisement claiming a trusted device_id, pointing at a second real
Dart TLS identity, is found by discovery but rejected at the pin-check
step with zero payload bytes).

ENDPOINT CHANGE POLICY:
A trusted device's candidate endpoint may be updated after independently
re-verifying the same SPKI pin at the new address (never merely because
the same `device_id` appeared somewhere new) -- proven directly in the
restart E2E test, which must re-verify the pin against the restarted
receiver's fresh ephemeral port before a send can succeed.

IDENTITY CHANGE POLICY:
No automatic re-pinning exists anywhere in the codebase; a genuinely
different TLS identity or desktop signing key for an already-trusted
`device_id`/`desktop_instance_id` fails the pin check /
signature-verification, respectively, exactly like an untrusted party
would (proven by the wrong-pin and wrong-signature E2E tests, which use
this exact mechanism).

GUI INTEGRATION:
Minimal, functional only (§183): `main.dart`/`home_screen.dart` now
construct and drive the secure stack by default (previously A14's
insecure stack); no new trust-management screens (list/forget UI) were
built in this phase -- the underlying APIs exist and are directly tested.

SHARED SECURITY TEST VECTORS:
`protocol/fixtures/security_v1/pairing_transcript.json`,
`signed_handoff.json` -- both validated independently by Python
(`test_security_canonical_vectors.py`, 2 tests) and Dart
(`security_canonical_test.dart`, 2 tests), including the Ed25519
signature itself matching byte-for-byte.

FLUTTER ANALYZE:
PASS -- "No issues found!" across the final `friendsend/` source tree.

FLUTTER TESTS:
57/57 passing (49 pre-A15 baseline + 8 new: 2 canonical-vector tests, 6
secure-receiver-server tests).

DESKTOP TESTS:
860/860 passing (846 pre-A15 baseline + 14 new: 2 canonical-vector tests,
8 real secure-LAN E2E tests, 3 real mDNS-discovery tests, 1
default-transport test). Zero regressions -- the 25 A13/A14 tests that
broke when the production default changed were fixed by explicitly
requesting the legacy transport (their actual test intent), not by
weakening any assertion.

APK DEBUG BUILD:
PASS

APK RELEASE BUILD:
PASS (51.7MB) -- compile/package check only, not a claim that the
plain-HTTP legacy profile or the current pairing bootstrap's plaintext
metadata exchange is production-hardened.

REAL PYTHON↔DART PAIRING E2E:
PASS -- `test_real_pairing_e2e_persists_mutual_trust`: real
`SecurePairingManager` (desktop) ↔ real `bin/pairing_client_harness.dart`
(the exact class the Flutter app's pairing screen uses), no fixture on
either side.

REAL PERSISTENT-TRUST RESTART E2E:
PASS -- `test_real_persistent_trust_restart_e2e`: pair once, destroy both
processes, reload persisted identities/trust from disk, real secure send
succeeds without re-pairing.

REAL SECURE HANDOFF E2E:
PASS -- `test_real_secure_handoff_e2e`: full TLS+pin+challenge+Ed25519+
streamed-artifact flow against the real Dart receiver core.

REAL WRONG-PIN E2E:
PASS -- `test_real_wrong_pin_e2e_sends_zero_payload`: 0 bytes sent,
`TLS_PIN_MISMATCH`.

REAL WRONG-SIGNATURE E2E:
PASS -- `test_real_wrong_signature_e2e_sends_zero_payload`: 0 bytes sent,
`AUTHENTICATION_FAILED`.

REAL REPLAY E2E:
PASS -- `test_real_auth_replay_e2e`: second offer reusing a consumed
challenge_id+signature is rejected with `AUTH_REPLAY`.

REAL MDNS DISCOVERY E2E:
PASS (desktop side only) -- `test_real_discovery_finds_a_real_advertised_service`,
`test_real_discovery_removal_transitions_online_to_offline`, both against
a real second `zeroconf` advertiser.

REAL MDNS-SPOOF E2E:
PASS -- `test_real_mdns_spoof_combined_with_tls_pin_e2e`: discovery finds
a spoofed advertisement pointing at a real, different Dart TLS identity;
the pin check rejects it.

REAL TLS WIRE-ENCRYPTION E2E:
PASS -- `test_real_tls_wire_encryption_e2e`: a real TCP relay proxy
between the real Python sender and the real Dart receiver never observes
the plaintext media marker in the relayed bytes.

PAIRING SECRET WIRE-LEAK TEST:
Not implemented as a dedicated raw-capture test in this phase (unlike the
TLS wire-encryption test) -- the pairing bootstrap sends only HMAC
proofs of the secret, never the secret field itself, by construction
(the wire body schema for `/pairing/offer`/`/pairing/confirm` contains no
`secret` field at all -- verified by direct code inspection of
`pairing_bootstrap.py`'s `_handle_offer`/`_handle_confirm` and
`secure_pairing_manager.dart`'s request bodies). Flagged here
transparently as a real test-coverage gap rather than silently claimed
proven by capture.

ANDROID EMULATOR SECURE-LAN E2E:
DEFERRED
<details>No Android emulator/physical device was available in this
environment (same constraint as A14). The Kotlin NSD bridge compiles and
links against the real SDK; it was never exercised at runtime.</details>

MANUAL PHYSICAL SECURE-LAN SMOKE:
DEFERRED
<details>No physical Android device was available.</details>

A9 CRASH→RANGE VALIDATION DEBT:
OPEN (untouched by this phase)

A13 PRODUCTION SECURE-CHANNEL DEBT:
CLOSED (this phase — see docs/OPEN_VALIDATION_DEBT.md for the exact
closure justification against every condition in the A15 prompt's §176)

A14 PHYSICAL-ANDROID-SHARE DEBT:
OPEN (untouched by this phase)

A15 PHYSICAL-MDNS-DISCOVERY DEBT:
OPEN (new this phase)

EXISTING TEST REGRESSIONS:
NONE (860/860 desktop, 57/57 Flutter passing; the 25 tests that initially
broke when the production transport default changed were fixed by
explicit legacy-transport opt-in, matching their actual test intent, not
by weakening assertions)

BUGS FOUND:
Two real, previously-undetected bugs, both found by this phase's own
real cross-language testing:

1. **`basic_utils`'s self-signed certificate generator produces an
   RFC-5480-invalid ECDSA signature algorithm identifier** (an
   `ASN1Null()` parameter where RFC 5480 requires the parameters field to
   be absent for `ecdsa-with-SHAxxx`). Python's `cryptography` library
   refused to parse the resulting certificate at all
   ("ExtraData" in `TbsCertificate::signature_alg`), even though Dart's
   own TLS stack served it without complaint. Root-caused via direct
   ASN.1/wire inspection before writing a fix. Fixed by writing a
   minimal, correct generator (`self_signed_cert.dart`) reusing the same
   standard `package:pointycastle` ASN.1 primitives with the one field
   corrected, rather than hand-rolling raw bytes or working around the
   symptom.

2. **`FriendSendTrustStore` self-deadlocked on its first real `upsert()`
   call** — `upsert()`/`remove()` hold `self._lock` while calling
   `_ensure_loaded()`, which itself acquires the same lock again via
   `load()` the first time it runs on a not-yet-loaded store; a plain
   `threading.Lock` cannot be re-entered by the same thread. Found via
   the real pairing E2E test hanging (not a mocked unit test — the real
   `/pairing/confirm` handler calling `trust_store.upsert()` for the
   first time in the process's life is exactly the code path that
   deadlocked). Root-caused with a raw curl reproduction isolating the
   exact request sequence before touching any code. Fixed by using an
   `RLock` instead of a plain `Lock`.

Additional non-bug findings surfaced and fixed during development
(documented for transparency, not filed as separate debt): dart:io's
`HttpClient` defaults to `Transfer-Encoding: chunked` for a POST body
written via `.write()` without an explicit `contentLength`, which
Python's plain `http.server`-based pairing bootstrap listener (like the
A13/A14 fixture) never decodes since it only reads by `Content-Length` --
fixed by setting `contentLength` explicitly on every Dart-side pairing
request; and the pairing bootstrap's `bind_host` default of `"0.0.0.0"`
advertised the literal unroutable string `"0.0.0.0"` as the connectable
endpoint -- fixed with a best-effort local-IPv4 resolution helper (and
the tests themselves default to `"127.0.0.1"` for determinism).

KNOWN LIMITATIONS:
See `docs/FRIENDSEND_SECURITY_PROFILE_V1.md` §11 and
`docs/FRIENDSEND_ANDROID_SECURITY.md` -- no Android backup exclusion for
identity/trust files yet (production release blocker, tracked
separately), no hardware-backed keystore, no certificate/key rotation
(explicit re-pair required by design), no production discovery UX (QR/
automatic endpoint healing UI), no real Android emulator/device E2E for
mDNS or the secure handoff's Sharesheet tail end.

GATE:
PASS

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A16 — Device Compatibility / Automatic Transcoding Pipeline

COMMIT:
5ef31f7
