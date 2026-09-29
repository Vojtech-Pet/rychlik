# FriendSend Android MVP (Prompt A14)

This document describes the first real, non-Python FriendSend peer: an
Android application built with Flutter (Dart) plus a thin Kotlin platform
layer. It implements the receiver side of
[docs/FRIENDSEND_PROTOCOL_V1.md](FRIENDSEND_PROTOCOL_V1.md) against the
desktop's A13 `DeviceHandoffService`/`HttpFriendSendTransport`.

**FriendSend Android is an MVP receiver, not a finished consumer product.**
It proves the protocol end to end against a real second implementation; it
does not ship monetization, accounts, a real pairing UX, discovery, or a
production-secure transport (see "Known limitations" below and
`docs/OPEN_VALIDATION_DEBT.md`).

## Flutter/Kotlin split

```text
friendsend/
├── lib/
│   ├── protocol/     -- wire constants + JSON models (protocol.dart, models.dart)
│   ├── identity/      -- stable, non-secret device_id (device_identity.dart)
│   ├── pairing/        -- mobile-side pairing completion (pairing_manager.dart)
│   ├── receiver/        -- the real dart:io HttpServer + temp cache
│   │                      (receiver_server.dart, temp_cache.dart)
│   ├── handoff/          -- app state machine + Sharesheet orchestration
│   │                      (handoff_controller.dart)
│   ├── platform/          -- the Flutter<->Kotlin MethodChannel bridge
│   │                      (share_bridge.dart)
│   ├── ui/                 -- minimal functional screens (home_screen.dart)
│   └── main.dart
├── bin/
│   └── receiver_harness.dart  -- host-side test harness (see below)
├── android/
│   └── app/src/main/
│       ├── AndroidManifest.xml   -- INTERNET permission, FileProvider
│       ├── kotlin/.../MainActivity.kt  -- FileProvider + ACTION_SEND bridge
│       └── res/xml/file_paths.xml       -- narrow FileProvider path config
└── test/                      -- Dart unit + widget tests
```

Everything except `MainActivity.kt`'s `handleShareFile()` (FileProvider
URI creation, `Intent.ACTION_SEND`, `Intent.createChooser`) is Dart. The
protocol parser, pairing logic, the real receiver HTTP server, streaming,
integrity verification, and the temp-cache/TTL lifecycle are all pure Dart
application code (no platform channel involved) -- this is why the same
code can run headless via `bin/receiver_harness.dart` for the real
cross-language E2E tests (see below), without an Android device.

**Application ID:** `app.friendsend.friendsend` (Flutter's `--org
app.friendsend --project-name friendsend` default). This is explicitly
**provisional** -- no final commercial package identity has been decided;
no existing Android application ID was renamed (none existed before A14).

## Device identity

`DeviceIdentityStore` (`lib/identity/device_identity.dart`) generates a
128-bit, cryptographically random `device_id` on first launch and
persists it as a small JSON file under the app's private support
directory (`getApplicationSupportDirectory()`). It is never derived from
the Android device model, hostname, MAC address, or IMEI, and no
`READ_PHONE_STATE`-class permission is requested. `display_name` defaults
to `"FriendSend Android"` and is presentation-only -- identity is
`device_id` alone.

## Pairing

Protocol v1 (`docs/FRIENDSEND_PROTOCOL_V1.md`, "Pairing semantics")
deliberately does not mandate a wire endpoint for completing pairing,
since no real second peer existed yet to validate one against. A14
implements pairing as an explicit, documented simplification:

1. The desktop generates a `PairingPayload` (protocol version, session
   id, its own endpoint, a 128-bit secret, an expiry) via the existing
   A13 `rychlik.device.pairing.PairingManager.create_session()`.
2. That payload is copied to the phone out of band (a minimal paste
   field in this MVP -- see "Pairing UI" below; a QR flow is future work).
3. `PairingManager.parse()`/`completePairing()`
   (`lib/pairing/pairing_manager.dart`) validates the payload's format,
   protocol version, and expiry locally, then mints a **fresh** 128-bit
   auth token and registers it with the receiver
   (`FriendSendReceiverServer.acceptToken()`). This mirrors the desktop's
   own `PairingManager.complete_pairing()`, which also mints a fresh
   token rather than reusing the pairing secret.

There is **no real wire callback** from phone back to desktop in A14 --
the desktop has no listening pairing-completion endpoint yet, so the
phone-side auth token this step mints is not automatically known to the
desktop. The real cross-language E2E tests (below) work around this
explicitly and visibly: the test calls the desktop's own
`DeviceHandoffService.complete_pairing()` (exercising the real,
already-shipped A13 code path) and then seeds the resulting auth token
directly into the real Dart receiver via the harness's `ACCEPT_TOKEN`
command -- standing in for the not-yet-built wire callback. Building that
real callback endpoint, and a real device-to-device pairing UX (QR code,
NFC, manual entry), is deferred to **A15**.

Nothing about the raw pairing secret is ever persisted or logged
(`PairingPayload.redactedDescription` never includes `secret`); pairing
state does not survive an app/process restart (§14 of the A14 prompt
explicitly allows this for A14 -- persistent trust is A15 scope).

## Receiver server

`FriendSendReceiverServer` (`lib/receiver/receiver_server.dart`) is a real
`dart:io` `HttpServer` bound to `127.0.0.1` (an OS-assigned, ephemeral
port for tests; the same binding is currently used for the app itself --
a real LAN-reachable interface selection is explicitly deferred to A15's
discovery work, per the A14 prompt's own §24 allowance). It implements:

- `GET /hello` -- advertises protocol version, capabilities, device
  identity.
- `POST /handoff/offer` -- validates protocol version, auth token, a
  test-configurable capability/size/MIME preflight, **before** accepting
  any payload byte.
- `POST /handoff/stream` -- reads the request body as a `dart:io`
  `Stream<List<int>>` (inherently bounded/chunked -- the whole payload is
  never buffered in memory), incrementally hashing with
  `package:crypto`'s chunked SHA-256 conversion sink while writing
  straight to a private temp file.

## Streaming, memory, and integrity

Every chunk `dart:io` delivers from the request stream is written
directly to disk and folded into the running SHA-256 hash; nothing
accumulates a full-payload buffer (proven structurally by a 2 MiB
multi-chunk test in `test/receiver_server_test.dart` asserting more than
one progress event fires, and functionally by successfully transferring
multi-megabyte payloads in the cross-language E2E tests).

`RECEIVED` is reported only once **both** checks pass, using bytes the
receiver actually wrote, never the sender's own claim:

1. total bytes received exactly equals the offer's declared `size_bytes`
   (`INCOMPLETE_TRANSFER` otherwise, including when the underlying
   connection breaks mid-read -- the read loop treats any stream error
   the same as an intentionally-short body);
2. the computed SHA-256 exactly equals the offer's declared `sha256`
   (`INTEGRITY_MISMATCH` otherwise).

On any failure or cancellation, the partial temp file is deleted
immediately.

## Temporary storage and TTL cleanup

`TempCache` (`lib/receiver/temp_cache.dart`) spools incoming payloads
under the app's private cache directory
(`getTemporaryDirectory()/friendsend`), naming each file from the
`handoff_id` alone -- the sender-declared filename is **never** used to
build a filesystem path, so a malicious `../../evil.mp4` cannot escape
the cache root (proven directly in `test/temp_cache_test.dart`).

A **verified** (`RECEIVED`) file is retained -- unlike the A13 Python
test fixture, which deletes immediately, since it never needs to hand the
file to anything else -- until a bounded TTL (default 1 hour) elapses,
so the platform Sharesheet target has time to actually read it. TTL
cleanup uses an injectable clock (no real-time waiting in tests) and runs:

- once at startup (`TempCache.sweepUntrackedOnStartup()`), deleting any
  file left over from a killed/crashed prior process, since a fresh
  process has no way to know whether it was ever verified;
- periodically thereafter via a single bounded `Timer.periodic` in
  `HandoffController` -- never one thread/timer per file.

FriendSend never writes into `MediaStore`/Gallery/Downloads and never
requests legacy broad storage permissions.

## Cancellation

`FriendSendReceiverServer.requestCancel(handoffId)` stops the read loop
for that handoff and forcibly drops the connection
(`HttpResponse.detachSocket()` + `Socket.destroy()`) rather than
attempting a graceful HTTP response mid-upload -- there is no clean way
to say "I stopped reading your POST body" within a single HTTP exchange,
and Protocol v1 defines cancellation as a connection-level event ("a
connection closed early by either side" is explicitly treated as
incomplete/cancelled). The partial temp file is deleted immediately.
Sender-initiated cancellation (from the existing A13
`DeviceHandoffService.cancel()`) is proven end to end against this real
receiver in the cross-language E2E suite.

## Android platform bridge

`ShareBridge` (`lib/platform/share_bridge.dart`) is a single-method
`MethodChannel` (`app.friendsend/share`) wrapper. `MainActivity.kt`
implements the Android side:

1. **Defense in depth**: independently resolves the requested path's
   canonical form and refuses anything outside the app's own
   `<cacheDir>/friendsend` subtree, even though Dart is only ever
   supposed to request this for a just-verified file.
2. Builds a `content://` URI via `androidx.core.content.FileProvider`
   (never a raw `file://` URI).
3. Launches `Intent.ACTION_SEND` with `EXTRA_STREAM`, the verified MIME
   type, and `FLAG_GRANT_READ_URI_PERMISSION`, wrapped in
   `Intent.createChooser()` -- the standard OS Sharesheet. No specific
   target package (WhatsApp/Messenger/Telegram/...) is ever hard-coded,
   and no accessibility-service auto-click or private API is used.
4. Returns one of a small bounded result set
   (`SHARE_SHEET_OPENED`/`NO_SHARE_TARGET`/`INVALID_TEMP_FILE`/
   `PLATFORM_ERROR`) -- the raw Android exception object never crosses
   into Dart.

`AndroidManifest.xml` declares the `FileProvider` with
`android:exported="false"` and `android:grantUriPermissions="true"`, and
`res/xml/file_paths.xml` exposes only the `friendsend/` cache subtree --
never the whole cache directory or external storage. This was verified
both by a static Dart test over the manifest/file_paths source
(`test/android_manifest_test.dart`) and once directly against the built
APK via `aapt dump xmltree`/`aapt dump permissions` (see the RESULT
document).

## Acknowledgement semantics

`HandoffUiSnapshot.shareOpened` (`lib/handoff/handoff_controller.dart`) is
the local, honest analogue of the wire `HANDOFF_ACCEPTED` state: it is
set **only** once the platform bridge actually reports
`ShareResult.opened`, never merely because the payload was verified
(`RECEIVED`). If the platform cannot launch a share target
(`NO_SHARE_TARGET`) or hits a platform error, the app stays in the
`received` UI state with a retryable **Share** button -- the verified
file is not lost, and no delivery claim is made. `HANDOFF_ACCEPTED` never
means, and this app never claims, that any social app actually sent the
media or that a recipient received or viewed it.

## Worker/concurrency model

The receiver runs entirely on Dart's single-isolate event loop (real
`async`/`await` I/O, no manual thread pool needed) -- consistent with the
desktop's own bounded-concurrency philosophy but achieved differently
since Dart's `dart:io` HTTP stack is non-blocking by construction. Only
one Sharesheet launch is attempted per received file at a time
(`HandoffController.autoShare`).

## GUI integration

The UI (`lib/ui/home_screen.dart`) is intentionally minimal, matching the
A14 prompt's explicit "functional, not final design" scope: a pairing
paste field, a ready/idle message, a receiving progress view with a
Cancel button, and a received view with Share/Discard actions. No final
branding, animation, or onboarding artwork was built.

## Known limitations (see also `docs/OPEN_VALIDATION_DEBT.md`)

- **No production-secure transport.** `HttpFriendSendTransport`
  (desktop, A13) and this receiver both speak plain HTTP + a bearer-style
  auth token. There is no TLS, certificate pinning, or Noise-protocol
  handshake. Tracked as `A13-PRODUCTION-SECURE-CHANNEL` (still OPEN).
- **No real pairing wire callback.** See "Pairing" above -- the phone
  cannot yet report its auth token back to the desktop over the network;
  the cross-language E2E tests seed it directly as a documented,
  deliberate A14 boundary.
- **App must stay foregrounded while receiving.** No Android foreground
  service was built (explicitly out of scope per the A14 prompt's own
  §25-27 -- prove the foreground path first).
- **No real Android emulator/physical-device E2E was run in this
  environment** (no emulator system image / no attached device were
  available when this phase was executed) -- see the RESULT document for
  what *was* independently verified against the real built APK (manifest,
  permissions, FileProvider config) and why the emulator/device E2E items
  are tracked as explicit, honest debt rather than silently skipped.
- **No persistent multi-restart device trust.** Every app restart
  requires re-pairing (§14 of the A14 prompt explicitly allows this).
- **No discovery, no monetization, no accounts, no push, no history/
  Inbox.** All out of scope per the A14 prompt.
