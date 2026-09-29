# FriendSend Android — Security Implementation (Prompt A15)

Implementation detail companion to
[FRIENDSEND_SECURITY_PROFILE_V1.md](FRIENDSEND_SECURITY_PROFILE_V1.md)
(normative) -- covers exactly how the Flutter/Kotlin app realizes that
profile.

## TLS identity persistence

`lib/security/tls_identity.dart` (`TlsIdentityStore`) generates a
self-signed ECDSA P-256 certificate on first launch
(`lib/security/self_signed_cert.dart`) and persists `{certificate_pem,
private_key_pem, public_key_pem}` as one JSON file in
`getApplicationSupportDirectory()` (app-private, not Downloads/external
storage, §10). The write is atomic (write to `.tmp`, then rename).

**Why a custom generator, not `basic_utils`'s directly**: the library's
own `X509Utils.generateSelfSignedCertificate` encodes the ECDSA signature
algorithm's parameters as `ASN1Null()`, which RFC 5480 requires to be
*absent* for `ecdsa-with-SHAxxx` -- Python's `cryptography` library
(used by the desktop) refused to parse the result at all. `self_signed_cert.dart`
reuses the same standard `package:pointycastle` ASN.1 primitives with
that one field corrected -- see the file's own docstring for the full
story and the exact wire-capture evidence that led to the fix.

## Trusted-desktop storage

`lib/security/desktop_trust_store.dart` (`DesktopTrustStore`) persists
`{desktop_instance_id, desktop_public_signing_key (base64), display_name,
paired_at_utc}` records, atomically, in app-private storage. The raw
pairing secret is never stored here.

## Android backup exclusion

**Current status**: no explicit `android:allowBackup="false"` or
`dataExtractionRules`/`fullBackupContent` XML was added in this phase --
Flutter's generated manifest does not set `android:allowBackup`
explicitly (Android's own default for a targetSdk in this project's range
is `allowBackup="true"`), so this remains **open, documented debt** for a
production release rather than a silently-assumed protection. A
production build MUST add a `dataExtractionRules.xml` excluding the
identity/trust JSON files under `getApplicationSupportDirectory()`
before shipping the secure profile with backup enabled. Tracked
alongside `A13-PRODUCTION-SECURE-CHANNEL`/`A15-PHYSICAL-MDNS-DISCOVERY`
in `docs/OPEN_VALIDATION_DEBT.md` as a known limitation of this MVP
security implementation (not silently claimed solved).

## Hardware-backed keystore

Not used in A15 -- the TLS private key lives in app-private storage
(protected by normal Android app sandboxing), not the hardware-backed
Android Keystore. `HttpServer.bindSecure`'s `SecurityContext` cannot
directly consume a Keystore-resident key without significant additional
platform-channel work; this is documented as possible future hardening,
not attempted here (Prompt A15 §12 explicitly permits this).

## Auth challenge / Ed25519 verification

`lib/security/auth_challenge.dart` (`AuthChallengeManager`) issues
256-bit-nonce, 45-second-TTL, one-time challenges and verifies Ed25519
signatures via `package:cryptography` against the persisted
`DesktopTrustStore` record for the claimed `desktop_instance_id`. An
unknown `desktop_instance_id` -> `UNTRUSTED_DESKTOP`; a signature that
does not verify -> `AUTHENTICATION_FAILED`; a reused/expired
`challenge_id` -> `AUTH_REPLAY`/`CHALLENGE_EXPIRED`. All before the
existing Protocol v1 offer preflight even runs.

## Secure receiver server

`lib/security/secure_receiver_server.dart`
(`SecureFriendSendReceiverServer`) binds via `HttpServer.bindSecure`
using the app's `TlsIdentity`. It implements `GET /hello`,
`GET /auth/challenge`, `POST /handoff/offer` (now requiring the
`X-FriendSend-Desktop-Id` / `X-FriendSend-Challenge-Id` /
`X-FriendSend-Signature` headers, verified before any existing A14
preflight check), and `POST /handoff/stream` -- reusing the exact same
bounded-chunk, incremental-SHA-256 streaming/integrity model as A14's
`FriendSendReceiverServer` (§52 of the A15 prompt: this phase adds a
layer around A13/A14's guarantees, never replaces them).

## mDNS advertisement (Android NSD)

`lib/security/mdns_advertiser.dart` (`MdnsAdvertiser`) is a two-method
`MethodChannel` (`app.friendsend/mdns`) bridge to `MainActivity.kt`'s
`registerFriendSendService`/`unregisterFriendSendService`, implemented
with Android's native `NsdManager` (service type `_friendsend._tcp`) --
no custom multicast protocol. Only non-secret metadata (`device_id`,
`protocol_version`, `security_profile`) is set as `NsdServiceInfo`
attributes (§56).

**Known limitation**: no Android emulator or physical device was
available in this environment to exercise `NsdManager` at runtime. The
Kotlin code compiles successfully against the real Android SDK (proven
by the debug/release APK builds in this phase) and follows the
documented `NsdManager` API correctly, but was never observed actually
advertising over real multicast DNS on a real Android OS. Tracked as
`A15-PHYSICAL-MDNS-DISCOVERY` in `docs/OPEN_VALIDATION_DEBT.md`. The
desktop-side discovery half is proven with a real `zeroconf` advertiser
in `tests/test_friendsend_discovery.py`.

## Permissions

Unchanged from A14: only `android.permission.INTERNET`. `NsdManager`'s
basic registration/discovery APIs do not require
`ACCESS_WIFI_STATE`/`CHANGE_WIFI_MULTICAST_STATE`-class permissions on
the SDK levels this project targets (unlike raw multicast socket usage),
consistent with Prompt A15 §114's "no location permission unless proven
required."

## Forget desktop

`DesktopTrustStore.forget(desktopInstanceId)` removes the stored public
signing key; future authentication attempts from that
`desktop_instance_id` fail with `UNTRUSTED_DESKTOP` immediately after.
Not yet wired into a GUI action in this phase (no final GUI redesign,
§183) -- the underlying API exists and is directly testable.
