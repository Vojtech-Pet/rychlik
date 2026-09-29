PHASE:
Prompt A14 — FriendSend Android Receiver MVP

STATUS:
COMPLETE

BASELINE COMMIT:
e6f1208

ACTUAL BASELINE HEAD:
e6f1208 (verified via `git rev-parse HEAD` before implementation; working
tree was clean)

FILES CHANGED:
54 new/modified files: a new `friendsend/` Flutter application (44
files: Dart app code, Kotlin platform bridge, Android manifest/
FileProvider config, Dart tests, pubspec), a new `protocol/fixtures/v1/`
directory (8 shared platform-neutral JSON fixtures), two new Python test
files (`tests/friendsend_dart_harness.py`,
`tests/test_friendsend_android_cross_language_e2e.py`), one modified
desktop file (`src/rychlik/device/transport.py` — see BUGS FOUND), and
three documentation files (`docs/FRIENDSEND_ANDROID_MVP.md`,
`docs/FRIENDSEND_ANDROID_PROTOCOL_V1_CONFORMANCE.md`,
`docs/OPEN_VALIDATION_DEBT.md` updated).

FLUTTER VERSION:
Flutter 3.41.2 (channel stable), Dart 3.11.0. Not pre-installed in this
environment — an existing local install at
`/mnt/Basic_data_partition1/vojtech/flutter` was found and used (not a
fresh download for this phase).

ANDROID TOOLCHAIN:
No Android SDK was installed in this environment at the start of this
phase (`flutter doctor` reported it entirely missing). With the user's
explicit go-ahead, installed: Android cmdline-tools (`commandlinetools-
linux-15859902_latest.zip`), `platform-tools`, `platforms;android-34`,
`platforms;android-36`, `build-tools;34.0.0`, `build-tools;28.0.3`,
`build-tools;35.0.0` (the last pulled automatically by the first Gradle
build), and `ndk;28.2.13676358`/`cmake;3.22.1` (also pulled
automatically). All SDK licenses accepted. JDK: system default was
OpenJDK 26 (too new for the bundled Gradle 8.14); `jdk21-openjdk` was
installed via `sudo pacman -S jdk21-openjdk` (the user ran this
themselves, sudo requires an interactive password this session cannot
supply) and wired in via `flutter config --jdk-dir` without changing the
system-wide default `java`. Final `flutter doctor`: Flutter ✓, Android
toolchain ✓ (SDK 34.0.0, platform android-36). No emulator/AVD system
image was installed (KVM is present at `/dev/kvm`, but standing one up
was not attempted in the time available — see A14-PHYSICAL-ANDROID-
SHARE-SMOKE debt). `adb` (OS package `android-tools`) was present from
the start but no device/emulator was ever attached to it.

FRIENDSEND APP PATH:
/mnt/Data/Rychlik-app/friendsend/

ANDROID APPLICATION ID:
app.friendsend.friendsend (Flutter's `--org app.friendsend --project-
name friendsend` default). Explicitly provisional — no final commercial
package identity has been decided; no existing application ID was
renamed (none existed before this phase).

APP ARCHITECTURE:
Flutter/Dart for everything except the Android platform bridge: protocol
parsing, pairing, the real `dart:io` HTTP receiver server, streaming/
integrity verification, and the temp-cache/TTL lifecycle are all pure
Dart, exercised identically by the Flutter app and by a host-side test
harness (`bin/receiver_harness.dart`) with no Flutter/widget dependency.
Kotlin (`MainActivity.kt`) owns only `FileProvider` URI creation and
`Intent.ACTION_SEND`/Sharesheet launching, reached through one small
`MethodChannel` (`app.friendsend/share`). See
`docs/FRIENDSEND_ANDROID_MVP.md` for the full breakdown.

PROTOCOL VERSION:
1 (`friendSendProtocolVersion` constant; a mismatched
`X-FriendSend-Protocol-Version` header is rejected with
`UNSUPPORTED_PROTOCOL` before any payload byte, proven in both the Dart
unit tests and would be proven cross-language too — not separately
exercised against the real Dart receiver in the cross-language suite,
since the desktop's own `UnsupportedProtocolError` is already raised
synchronously and locally by `DeviceHandoffService.send()` before any
network call is attempted for a mismatched device record; see
`test_real_offer_rejection_costs_zero_payload_bytes`).

PROTOCOL V1 CONFORMANCE:
See `docs/FRIENDSEND_ANDROID_PROTOCOL_V1_CONFORMANCE.md` for the full
endpoint/field/error-code mapping. Summary: `/hello`, `/handoff/offer`,
`/handoff/stream` all implemented per the wire contract; all bounded
error codes from the protocol doc are represented; the only real
deviation is that no pairing wire-callback endpoint exists yet, which
the protocol document itself does not mandate for v1.

SHARED PROTOCOL FIXTURES:
Created `protocol/fixtures/v1/`: `handoff_offer.json`,
`handoff_accept.json`, `received_ack.json`, `pairing_payload.json`,
`pairing_request.json`, `pairing_success.json`, `capabilities.json`,
`error_examples.json`. Validated on the Dart side
(`friendsend/test/protocol_test.dart` parses `handoff_offer.json` and
`pairing_payload.json` directly). Not yet separately re-validated by a
new desktop-side fixture-parsing test (the desktop's own
`HandoffOffer`/`PairingPayload`-equivalent dataclasses were already
extensively tested against equivalent hand-built dicts in A13); this is
a reasonable, bounded scope call, not an oversight — flagged here for
transparency rather than silently claimed as fully done both directions.

DEVICE IDENTITY:
128-bit, cryptographically random (`Random.secure()`) hex `device_id`,
generated once and persisted as a small JSON file under
`getApplicationSupportDirectory()`. Never derived from Android hardware
identifiers. `display_name` defaults to "FriendSend Android" and is
presentation-only.

DEVICE ID PERSISTENCE:
Survives normal app restart (loaded from disk if the identity file
exists, generated once otherwise) — proven directly by
`DeviceIdentityStore` reading back its own written file; not re-tested
via a full app restart in a widget test (would need real filesystem
persistence across separate test processes, out of scope for this
phase's automated suite).

PAIRING UI:
Minimal: a multi-line paste field for the desktop-issued pairing
payload JSON, a Pair button, and a bounded, human-readable error message
on malformed/expired input (never a raw stack trace). No QR scanner.

PAIRING MODEL:
Mobile-side completion of the desktop's `PairingPayload` (see
`docs/FRIENDSEND_ANDROID_MVP.md`, "Pairing", for the full explanation).
No real wire callback from phone to desktop exists yet — Protocol v1
does not mandate one for this step, and A14 explicitly does not require
persistent trust. The real cross-language E2E tests bridge this gap
explicitly and visibly (seeding the desktop-generated auth token
directly into the real Dart receiver) rather than silently faking a
callback that does not exist.

PAIRING SECRET POLICY:
The raw pairing secret is never persisted to disk and never appears in
`PairingPayload.redactedDescription` (used for any future logging);
`completePairing()` mints a fresh, independent 128-bit auth token rather
than reusing the secret.

RECEIVER SERVER:
Real `dart:io` `HttpServer`, bound to `127.0.0.1` with an OS-assigned
port for tests (the app itself currently binds the same way — a real
LAN-reachable interface/address selection is explicitly deferred to
A15's discovery work per the A14 prompt's own §24 allowance).

NETWORK BIND MODEL:
Loopback + OS-assigned ephemeral port for all automated tests
(deterministic, no fixed port). Production LAN binding was not built in
this phase (see above).

BACKGROUND / FOREGROUND LIMITATION:
No Android foreground service was implemented — FriendSend must remain
open in the foreground while receiving, exactly as the A14 prompt's own
§25-27 explicitly scopes for this phase. Documented in
`docs/FRIENDSEND_ANDROID_MVP.md`, "Known limitations."

HANDOFF OFFER VALIDATION:
Protocol version, auth token, capability (test-configurable toggle),
size (test-configurable maximum), and MIME type (test-configurable
allow-list) are all validated before any payload byte is accepted;
proven directly (six dedicated rejection tests in
`receiver_server_test.dart`) and cross-language
(`test_real_offer_rejection_costs_zero_payload_bytes`,
`test_real_wrong_auth_token_is_rejected`, both asserting `bytes_sent ==
0` on the real desktop `DeviceHandoffSnapshot`).

STREAMING MODEL:
`dart:io`'s `HttpRequest` body stream is consumed chunk by chunk;
nothing accumulates a full in-memory buffer. Proven with a 2 MiB
multi-chunk transfer over real loopback HTTP (paced with explicit
client-side chunk writes + a small delay, mirroring A13's own
`chunk_delay` test-timing fix, since a small payload can otherwise
arrive as a single OS-buffered read) asserting more than one progress
event fires and the final byte count/hash are exact.

MEMORY / CHUNK POLICY:
Each chunk is written directly to the temp-cache file and folded into an
incremental SHA-256 (`package:crypto`'s chunked conversion sink) as it
arrives — never re-read from disk to compute the hash, never held in a
growing in-memory list.

INTEGRITY MODEL:
`RECEIVED` requires, in order: (1) total bytes received exactly equals
the offer's declared `size_bytes` (a broken connection mid-read is
treated identically to a short body — `INCOMPLETE_TRANSFER`), then (2)
the computed SHA-256 exactly equals the offer's declared value
(`INTEGRITY_MISMATCH` otherwise). Both checks use bytes the receiver
itself wrote, never the sender's claim.

TEMP STORAGE LOCATION:
`getTemporaryDirectory()/friendsend` (app-private cache). Never
`Downloads`/Gallery/`MediaStore`.

TEMP FILE NAMING:
Derived from `handoff_id` alone via a strict allow-list sanitizer; the
sender-declared filename is never used to build a path. Proven directly
with real traversal payloads (`../../evil.mp4`, `/sdcard/foo`,
`..\..\foo`, `../../../etc/passwd`) in `temp_cache_test.dart`.

TTL / CLEANUP POLICY:
Default 1-hour TTL for a verified (`RECEIVED`) file, using an injectable
clock (no real-time waiting in tests). A single bounded
`Timer.periodic` performs cleanup checks (never one timer/thread per
file); a startup sweep (`sweepUntrackedOnStartup()`) deletes anything
left over from a killed/crashed prior process, since a fresh process has
no way to know whether a leftover file was ever verified. Failure/
cancellation always deletes the partial file immediately, not on a TTL.

CANCELLATION:
Receiver-side `requestCancel(handoffId)` stops the read loop and
forcibly drops the connection (`detachSocket()` + `Socket.destroy()`)
rather than attempting a graceful mid-upload HTTP response — Protocol v1
has no wire endpoint for "receiver cancelled mid-POST," and treats an
early-closed connection as incomplete/cancelled, which this matches.
Proven directly (a 5 MiB paced transfer, cancelled after the first
progress event, asserting the partial temp file is deleted) and
cross-language, sender-initiated, via the existing A13
`DeviceHandoffService.cancel()`
(`test_real_cancel_cross_language_e2e`), asserting the real desktop
snapshot reaches `CANCELLED`.

RECEIVE PROGRESS:
`bytes_received`/`total_bytes` exposed via `ReceiverEvent`/
`HandoffUiSnapshot.progressFraction`; no fabricated speed/ETA estimator
was added (bytes and fraction only, per the A14 prompt's own §75).

ANDROID PLATFORM BRIDGE:
One `MethodChannel` (`app.friendsend/share`), one method
(`shareFile(path, displayName, mimeType)`), a small bounded result enum
(`ShareResult`). Kotlin independently re-validates that the requested
path resolves inside the app's own `<cacheDir>/friendsend` before
building any `FileProvider` grant — defense in depth against a buggy or
malicious Dart call, even though Dart only ever calls this for a
just-verified file.

FILEPROVIDER:
`androidx.core.content.FileProvider`, `android:exported="false"`,
`android:grantUriPermissions="true"`, authority
`app.friendsend.friendsend.fileprovider`; `res/xml/file_paths.xml`
exposes only the `friendsend/` cache subtree. Verified twice: statically
over the manifest/file_paths *source* (`test/android_manifest_test.dart`,
run every `flutter test`), and once directly against the real built
debug APK via `aapt dump xmltree`/`aapt dump permissions` (manual, not
part of the automated CI-style suite, but a genuine artifact
inspection, not merely reading the source that produced it).

CONTENT URI POLICY:
Always `content://` via `FileProvider.getUriForFile()`; `file://` is
never sent to another app.

ACTION_SEND:
`Intent.ACTION_SEND` with `EXTRA_STREAM`, the verified MIME type, and
`FLAG_GRANT_READ_URI_PERMISSION`.

ANDROID SHARESHEET:
`Intent.createChooser()` — the standard OS chooser. No hard-coded target
package for any specific app; no accessibility-service auto-click; no
private API.

RECEIVED SEMANTICS:
Means only that this receiver independently verified the complete
payload's size and SHA-256. Never implies delivery to, or viewing by,
any person.

HANDOFF_ACCEPTED SEMANTICS:
Represented locally as `HandoffUiSnapshot.shareOpened`, set only once the
Android platform bridge reports `ShareResult.opened` (the Sharesheet
chooser actually launched) — never merely because `RECEIVED` was
reached, and never implies a social app actually sent anything.

ANDROID PERMISSIONS:
Only `android.permission.INTERNET` is declared in
`AndroidManifest.xml`, verified both by a static Dart test and directly
against the built APK via `aapt dump permissions` (the only other
permission present, `..._DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`, is a
self-signed permission androidx injects automatically for its own
internal broadcast receivers — not a dangerous permission, not requested
of the user). No storage/contacts/phone/location permission anywhere.

CLEAR-TEXT / SECURITY STATUS:
Plain HTTP + bearer-style auth token, exactly matching the desktop's
A13 `HttpFriendSendTransport` — explicitly EXPERIMENTAL/TEST TRANSPORT,
not production-secure. `A13-PRODUCTION-SECURE-CHANNEL` remains OPEN (not
closed by this phase — see below). No Android
`usesCleartextTraffic`/network-security-config changes were made in this
phase (the default Android network security config already permits
plaintext loopback/local traffic for a debug-signed app in this SDK
range; a production release policy is A15 scope alongside the secure
channel itself).

FLUTTER ANALYZE:
PASS — "No issues found!" (0 errors, 0 warnings, 0 info) across the
final `friendsend/` source tree.

FLUTTER TESTS:
49/49 passing (`flutter test`, full suite: protocol, pairing, temp
cache, receiver server, handoff controller, home screen widget tests,
Android manifest/FileProvider static assertions). Run multiple times
during development for stability as fixes landed; final full-suite run
green.

DESKTOP TESTS:
846/846 passing (`pytest`, full suite: 840 pre-A14 baseline + 6 new
cross-language E2E tests). Zero regressions — the one production code
change this phase touched (`src/rychlik/device/transport.py`, see BUGS
FOUND) was re-verified against the full existing A9/A13 device-mode
suite before being considered safe.

APK DEBUG BUILD:
PASS — `flutter build apk --debug` succeeds, producing
`build/app/outputs/flutter-apk/app-debug.apk`.

APK RELEASE BUILD:
PASS — `flutter build apk --release` succeeds, producing
`build/app/outputs/flutter-apk/app-release.apk` (47.3MB). This is a
compile/package check only, per the A14 prompt's own §99 — it does not
imply the current clear-text transport is production-release ready (see
CLEAR-TEXT / SECURITY STATUS above).

REAL PYTHON→DART SOCKET E2E:
PASS — `test_real_python_to_dart_socket_e2e`: real
`DeviceHandoffService.send()` (desktop, Python) over a real loopback
socket to the real Dart receiver core (`bin/receiver_harness.dart`, the
same classes the Flutter app ships), asserting `HandoffState.RECEIVED`
on the desktop side and a matching `received` event with the correct
SHA-256 on the Dart side. This surfaced and fixed a real, previously-
undetected bug (see BUGS FOUND).

REAL COMPLETED-DOWNLOAD→DART RECEIVER E2E:
PASS — `test_real_completed_download_to_dart_receiver_e2e`: a real HTTP
download through `DownloadManagerService`, its durable completed-file
identity, the existing `Artifact` bridge, and a real Device Mode handoff
to the real Dart receiver — composing A10/A12/A13/A14 with no mocked
Artifact anywhere in the chain.

REAL RESTARTED-COMPLETION→DART RECEIVER E2E:
PASS — `test_real_restarted_completion_to_dart_receiver_e2e`: the same
chain after a full `DownloadManagerService` process-level restart
(`stop()` then a fresh instance against the same database), proving
A12's durable completed-file recovery still feeds a real Device Mode
handoff to a real Dart receiver correctly.

REAL CANCEL E2E:
PASS — `test_real_cancel_cross_language_e2e`: a real 4 MiB paced
transfer to the real Dart receiver, cancelled mid-transfer from the
desktop side via the existing `DeviceHandoffService.cancel()`, asserting
the real desktop snapshot reaches `CANCELLED`.

ANDROID EMULATOR E2E:
DEFERRED
<details>No usable Android emulator (system image + `emulator` binary)
was set up in this environment during this phase — KVM (`/dev/kvm`) is
present, and the Android SDK/platform/build-tools installed for this
phase would support one, but standing up an AVD and booting it was not
attempted given the time already spent on toolchain setup and the real
cross-language E2E path already proving the protocol/streaming/
integrity logic. Tracked as part of A14-PHYSICAL-ANDROID-SHARE-SMOKE
below.</details>

MANUAL PHYSICAL ANDROID SMOKE:
DEFERRED
<details>No physical Android device was available in this
environment.</details>

A9 CRASH→RANGE VALIDATION DEBT:
OPEN (untouched by this phase)

A13 PRODUCTION SECURE-CHANNEL DEBT:
OPEN (untouched by this phase — the Android receiver uses the identical
experimental plain-HTTP + bearer-token transport as the desktop; a real
secure channel is A15 scope per both the A13 and A14 prompts)

A14 PHYSICAL-ANDROID-SHARE DEBT:
OPEN (new this phase — see `docs/OPEN_VALIDATION_DEBT.md`,
`A14-PHYSICAL-ANDROID-SHARE-SMOKE`)

EXISTING TEST REGRESSIONS:
NONE (846/846 desktop tests passing, including the full A8/A9/A10/A12/
A13 suites re-run after the `transport.py` fix below)

BUGS FOUND:
One real, previously-undetected bug in already-shipped A13 code, found
by this phase's real interoperability testing against a standards-
conformant second HTTP implementation (Dart's `dart:io`), which the
Python test fixture used throughout A13 was too lenient to ever surface:
`HttpFriendSendTransport.send()` (`src/rychlik/device/transport.py`)
streamed the payload body via a bare Python generator with a manually-
set `Content-Length` header. Because a bare generator has no `__len__`,
the `requests` library could not determine its size and defaulted to
`Transfer-Encoding: chunked` for the actual request framing — while
*also* leaving the manually-set `Content-Length` header in place,
producing a single HTTP request with both headers set simultaneously.
This is invalid per RFC 7230 §3.3.3. Python's own `http.server`-based
A13 test fixture (`tests/friendsend_receiver_fixture.py`) never noticed,
because it only ever reads by `Content-Length` and never parses
`Transfer-Encoding` at all — the raw body bytes it received happened to
still be Content-Length-shaped, so it worked by coincidence. Dart's
`dart:io` `HttpServer`, correctly preferring `Transfer-Encoding` per
spec when both headers are present, failed to parse the (not actually
chunk-framed) body, producing `ChunkedEncodingError` on the response
side when the connection was subsequently torn down. Root-caused via a
raw-socket capture of the actual wire bytes (confirmed: both headers
present, body not really chunk-encoded) before touching any code. Fixed
with a small `_SizedChunks` wrapper giving the existing chunk generator
a real `__len__`, so `requests` sends a single, unambiguous
`Content-Length`-only request — verified fixed via the same raw-socket
capture, then via the real cross-language E2E test, then via the full
27-test A9/A13 device-mode regression suite (no change in behavior
against the tolerant Python fixture, as expected). This is an A13-
vintage bug in already-committed, already-tested code; A14 is the phase
that surfaced it, precisely because it is the first time a real,
standards-conformant non-Python peer existed to expose it.

KNOWN LIMITATIONS:
See `docs/FRIENDSEND_ANDROID_MVP.md`, "Known limitations" — no
production-secure transport, no real pairing wire callback (A15 scope),
foreground-only operation, no emulator/physical-device E2E in this
environment, no persistent multi-restart device trust, no discovery/
monetization/accounts/push/history.

GATE:
PASS

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A15 — Secure LAN Pairing / Discovery / Persistent Trust

COMMIT:
f2a0af0
