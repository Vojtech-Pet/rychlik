PHASE:
Prompt A17 — Device Mode Release Hardening / Physical Android Validation

STATUS:
BLOCKED

BASELINE COMMIT:
fde1969ccb4dc871497059f06d0beb63dcf362e0

ACTUAL BASELINE HEAD:
fde1969ccb4dc871497059f06d0beb63dcf362e0 (worktree clean before implementation started)

COMMIT:
82af5849f8278347e7ffa89da4702c74baea24d2

FILES CHANGED:
New:
- tests/_a9_crash_range_worker.py
- tests/test_a9_crash_range_e2e.py
- docs/DEVICE_MODE_RELEASE_CHECKLIST.md
- docs/PHYSICAL_ANDROID_VALIDATION.md
- docs/DEVICE_MODE_RELEASE_HARDENING_RESULT.md (this file)

Modified:
- docs/OPEN_VALIDATION_DEBT.md (A9-CRASH-RANGE-E2E CLOSED with evidence; A17-attempt notes added to A14-PHYSICAL-ANDROID-SHARE-SMOKE / A15-PHYSICAL-MDNS-DISCOVERY, both left OPEN)
- docs/SAFE_PARTIAL_RESUME_RESULT.md (A17 addendum recording the combined crash->Range debt closure without rewriting A9 history)

No production source code (`src/rychlik/**`, `friendsend/lib/**`) was
changed in A17 -- this phase found no release-blocking defect requiring a
fix, only debt to close and evidence to gather.

PHYSICAL ANDROID DEVICE AVAILABLE:
NO

ANDROID DEVICE:
none -- `adb devices -l` returned no attached devices; no `emulator`
binary present (`emulator -list-avds`: command not found); `flutter
devices` lists only the Linux desktop target. Same environment constraint
independently hit during A14 and A15.

FRIENDSEND APK:
debug: PASS (`flutter build apk --debug`)
release: PASS (`flutter build apk --release`, 51.7MB)
Both are real release-candidate builds; neither was installed on a
physical device (none available).

DESKTOP ENVIRONMENT:
Linux (Manjaro-based, kernel 7.3.0-rc1), Python 3.14.7 (project `.venv`),
FFmpeg/FFprobe n9.0.1, Flutter 3.41.2 (stable), Dart 3.11.0, `adb`
1.0.41/37.0.0-android-tools installed but with no device ever attached.

A9 CRASH→RANGE E2E:
CLOSED. `tests/test_a9_crash_range_e2e.py::test_a9_crash_range_e2e`
proves the full combined chain in one test: a real child subprocess
(`tests/_a9_crash_range_worker.py`) downloads against a real
`/resumable/<key>` fixture route (ETag `"a17-crash-range-v1"`, ~1MB body,
50,000-byte checkpoint threshold); the parent waits for a real durable
`PartialTransferState` (`durable_bytes > 0`, `prefix_sha256` set,
validator captured) committed to the real SQLite file, confirms the raw
`.part` file on disk is `>= durable_bytes` (§9), then `SIGKILL`s the
child; a fresh process runs `RestartRecovery` (`TRANSFERRING -> READY`,
`attempt_count` preserved at 1, durable partial/validator carried forward
intact); a brand-new dispatch in that process issues a real HTTP request
the fixture server actually observed with `Range:
bytes=<durable_bytes>-` and `If-Range: "a17-crash-range-v1"` (never
`bytes=0-`); the task reaches `COMPLETED` with `attempt_count == 2`, a
byte-exact final file, and all partial/queue metadata removed. Passed 6x
consecutively with no flake, and as part of 3 full clean desktop suite
runs (907/907 each).

A9 DURABLE OFFSET PROOF:
Confirmed before kill: `durable_bytes > 0`, `prefix_sha256` non-empty,
`validator_value == '"a17-crash-range-v1"'` on the persisted
`PartialTransferState`; raw `.part` file size observed `>=
durable_bytes` at the same instant, proving recovery logic cannot be
fooled into trusting uncheckpointed bytes on disk.

A9 RANGE / IF-RANGE PROOF:
`http_fixture_server.resumable_last_request(key)` (the fixture server's
own record of the real request it received) shows `range ==
f"bytes={durable_bytes}-"` and `if_range == '"a17-crash-range-v1"'` --
sourced from the persisted checkpoint, never a recomputed/raw file size.

A9 FINAL BYTE INTEGRITY:
Final file byte-identical to the ~1MB fixture body; `attempt_count == 2`;
queue entry `REMOVED`; partial-transfer metadata removed from the store.

PHYSICAL PAIRING:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL PERSISTENT TRUST RESTART:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL ANDROID NSD ADVERTISEMENT:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL DESKTOP MDNS DISCOVERY:
NOT EXECUTED (no physical peer to discover) — BLOCKED

PHYSICAL SECURE PROFILE:
NOT EXECUTED (no device) — BLOCKED. Automated regression continues to
prove the production default is `pinned-tls-signature-v1`
(`test_default_transport_is_secure_not_plain_http`,
`tests/test_device_handoff_service.py`) with no reachable code path that
falls back to `plain-http-bearer-v1`.

PHYSICAL TLS / SPKI VALIDATION:
NOT EXECUTED (no device) — BLOCKED. Static audit: no `verify=False`
without-pin-check-after or disabled hostname/cert check exists outside
the documented, deliberate `connect_and_verify_pin()` design (SPKI
pinning IS the trust model here, by design, and a mismatch raises before
any application byte is sent) — grepped across
`src/rychlik/device/security/` and `friendsend/lib/`; no
`badCertificateCallback`/trust-all pattern found on the Dart side either.

PHYSICAL DESKTOP AUTHENTICATION:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL DEVICE SEND:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL RECEIVED INTEGRITY:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL CONTENT URI:
NOT EXECUTED (no device) — BLOCKED. Static audit only: the merged release
manifest (`build/app/intermediates/packaged_manifests/release/
processReleaseManifestForPackage/AndroidManifest.xml`) confirms the
`FileProvider` is `exported="false"`, `grantUriPermissions="true"`, with
`file_paths.xml` exposing only the `friendsend/` cache subtree — never
the whole cache dir or external storage.

PHYSICAL ANDROID SHARESHEET:
NOT EXECUTED (no device) — BLOCKED. This is the specific real-device
scenario `A14-PHYSICAL-ANDROID-SHARE-SMOKE` exists to track, and it
remains OPEN.

PHYSICAL PASSTHROUGH:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL REMUX:
NOT EXECUTED (no device) — BLOCKED

PHYSICAL TRANSCODE:
NOT EXECUTED (no device) — BLOCKED

ORIGINAL FILE INTEGRITY:
Not re-verified physically this phase; already proven automatically in
A16 (`test_real_remux_handoff_e2e`, `test_real_transcode_handoff_e2e`,
still green: source SHA-256 unchanged after every remux/transcode).

PHYSICAL CANCEL / CLEANUP:
NOT EXECUTED (no device) — BLOCKED. Automated proof remains: A16's
`test_real_cancel_during_transcode_handoff_e2e` and the Dart receiver's
own cancellation test still pass.

FRIENDSEND RESTART:
NOT EXECUTED (no device) — BLOCKED

DESKTOP RESTART:
NOT EXECUTED (no device, and this specific check is only meaningful
paired with a real phone) — BLOCKED

BOTH-SIDES RESTART:
NOT EXECUTED (no device) — BLOCKED

FORGET / RE-PAIR:
NOT EXECUTED (no device) — BLOCKED

NETWORK-LOSS BEHAVIOR:
NOT EXECUTED physically (no device) — BLOCKED. Automated equivalents
(connection-drop-mid-transfer, wrong-pin/wrong-signature zero-payload
tests from A15) remain green.

LARGE-FILE PHYSICAL TEST:
NOT EXECUTED (no device) — BLOCKED

TEMP CACHE / TTL:
Desktop-side TTL/ownership-safe cleanup already proven automatically in
A16 (`tests/test_device_media_temp_cache.py`, still green). Phone-side
TTL cleanup could not be exercised on real hardware this phase.

ANDROID RELEASE PERMISSION AUDIT:
PASS (static). Merged release manifest requests only `INTERNET`, plus
standard AndroidX-injected entries (`DYNAMIC_RECEIVER_NOT_EXPORTED_
PERMISSION` self-permission, `ProfileInstallReceiver` with the standard
`android.permission.DUMP`). No storage/contacts/phone/camera/microphone/
location permission present. No custom NSD-specific runtime permission
was added ("just in case") since Android's `NsdManager` requires none
beyond `INTERNET` on the target SDK actually used here.

FILEPROVIDER AUDIT:
PASS (static) — see PHYSICAL CONTENT URI above; narrowest possible
exposed subtree, not exported, `grantUriPermissions=true`.

ANDROID BACKUP / KEY AUDIT:
No change from A15's design this phase (identity/trust private material
excluded from generic Android backup, per `docs/FRIENDSEND_ANDROID_
SECURITY.md`); not re-verified against a real backup/restore on physical
hardware (BLOCKED, no device).

RELEASE SECURITY-CONFIG AUDIT:
PASS (static). No `android:usesCleartextTraffic` attribute present
anywhere in the manifest (defaults to `false`/disallowed on the target
SDK used); no custom network security config file exists to grant a
broader cleartext exception. `android:debuggable` is not set in the
release build type and does not appear in the merged release manifest
(defaults to `false`). Release `buildTypes.release.signingConfig`
currently reuses the debug signing config (a known, pre-existing Flutter
template default, unrelated to the A15 TLS-pin trust model, which does
not depend on APK signing identity) — recorded as a known non-blocking
limitation below; a real release signing key is required before any
public distribution, independent of Device Mode security.

DESKTOP SECRET-LOG AUDIT:
PASS. `src/rychlik/device/`, `src/rychlik/device/security/`, and
`src/rychlik/device/media/` contain no `logging.*`/`print` calls at all —
nothing in these layers can leak a secret via logs because nothing in
them logs anything.

ANDROID LOGCAT SECRET AUDIT:
PASS (static). `friendsend/lib/security/` and `friendsend/lib/receiver/`
contain no `print`/`debugPrint`/`log` calls. Could not be verified
against a real `adb logcat` capture (no device).

DESKTOP VISIBLE SMOKE:
PARTIAL. Launched via `launch.sh` (`./.venv/bin/python main.py`) against
the real graphical session available in this environment: the process
started, ran without crashing or emitting any startup error to
stdout/stderr, and shut down cleanly on `SIGTERM` (no zombie, no
traceback). Its window could not be located via `wmctrl`/`xdotool` in
this session (title-matching issue or compositor quirk, not a crash), so
a full interactive click-through (destination chooser, live download
with progress, pause/resume clicks, Open Folder, Share, Device Mode
panel) was NOT performed in this pass — this environment's graphical
session appeared to be a real, possibly shared desktop rather than an
isolated throwaway display, and blind coordinate-based UI automation
against it was judged not worth the risk versus the low marginal value
(desktop functionality is already covered by 907 real automated tests).
Recorded here as a soft, non-blocking limitation, not claimed as a full
pass.

SHARE-BY-LINK REGRESSION:
PASS. Existing Share-by-Link automated suite remains green as part of
the full 907/907 desktop regression; A16/A17 changes do not touch that
code path (A16's Share-by-Link/Device-Mode coexistence E2E test itself
also remains green).

FLUTTER ANALYZE:
PASS — "No issues found!"

FLUTTER TESTS:
58/58, run 3x consecutively, no flake.

DESKTOP TESTS:
907/907 (906 baseline + 1 new `test_a9_crash_range_e2e`), run 3x
consecutively, no flake.

APK DEBUG BUILD:
PASS

APK RELEASE BUILD:
PASS (51.7MB)

TEST STABILITY:
Desktop full suite: 3/3 clean runs, 907/907 each.
Flutter full suite: 3/3 clean runs, 58/58 each.
New A9 combined E2E test additionally run 6x in isolation, no flake.

A9-CRASH-RANGE-E2E DEBT:
CLOSED

A14-PHYSICAL-ANDROID-SHARE-SMOKE:
OPEN

A15-PHYSICAL-MDNS-DISCOVERY:
OPEN

NEW RELEASE-BLOCKING DEBT:
NONE

BUGS FOUND:
NONE. This phase closed one piece of validation debt (A9) and performed
static security/permission/config audits; no real defect was exposed by
either the new combined test or the audits.

BUGS FIXED:
N/A (none found)

KNOWN NON-BLOCKING LIMITATIONS:
- Release APK is signed with the debug signing config (Flutter template
  default) — needs a real release signing key before any public
  distribution; unrelated to and does not weaken the A15 TLS-pin Device
  Mode trust model.
- No background FriendSend receive when the app is fully closed (already
  documented, unchanged).
- No cloud relay, no automatic recipient/target-app selection, no
  permanent mobile receive history, no transcode cache, no monetization,
  no final visual redesign — all explicitly out of scope for A17, per
  §82-84.

GATE:
FAIL — physical-validation debt (`A14-PHYSICAL-ANDROID-SHARE-SMOKE`,
`A15-PHYSICAL-MDNS-DISCOVERY`) remains OPEN because no physical Android
device or emulator is available in this environment. Per this phase's
own explicit acceptance policy (§2/§120/§121), A17 must not be reported
`COMPLETE`/`PASS` while that debt is open, regardless of how much of the
automatable scope passed cleanly.

NEXT PHASE READY:
NO — A17 itself is not done. Re-run the physical portions of this phase
(see `docs/DEVICE_MODE_RELEASE_CHECKLIST.md` and
`docs/PHYSICAL_ANDROID_VALIDATION.md`) against a real Android device or
a working emulator before considering A18 or a final GUI design gate.

NEXT RECOMMENDED PHASE:
Re-attempt Prompt A17's physical-validation scope once a physical Android
device (or a working emulator: system image + `emulator` binary) is
available. Only after `A14-PHYSICAL-ANDROID-SHARE-SMOKE` and
`A15-PHYSICAL-MDNS-DISCOVERY` are genuinely closed should A18 (FriendSend
Trial/€1.99 Lifetime Unlock) or a final GUI/design gate be considered.

WORKTREE:
CLEAN (before commit)

---

A17-E1 ADDENDUM — Android Emulator Pairing / Secure Device Mode Validation (2026-09-28)

STATUS (A17-E1): PARTIAL — emulator evidence gathered, two real bugs found and fixed
A17 OVERALL STATUS: BLOCKED (unchanged; no physical Android device)

BASELINE: c86ff989dca893d4cd274254499d9c94eb713603

SOURCE CODE CHANGES:
- friendsend/lib/handoff/handoff_controller.dart (new restoreTrustState)
- friendsend/lib/main.dart (calls restoreTrustState at startup)
- friendsend/test/handoff_controller_test.dart (+2 tests)
- friendsend/android/app/src/main/kotlin/app/friendsend/friendsend/MainActivity.kt (4-arg FileProvider display name, sanitizeShareDisplayName, generic chooser title)
- friendsend/android/app/build.gradle.kts (JUnit test dependency)
- friendsend/android/app/src/test/kotlin/app/friendsend/friendsend/SanitizeShareDisplayNameTest.kt (new, 4 tests)
No Python/desktop source changed.

EMULATOR: AVD Medium_Phone, Android 17 (API 37), sdk_gphone16k_x86_64
FRESH PAIRING PAYLOAD GENERATED: YES (local only, never printed or reused)
EXPOSED OLD PAIRING SECRET REUSED: NO
REAL PAIRING: PASS (real FriendSend UI, real A15 production path, security_profile = pinned-tls-signature-v1)
PAIRING SECRET PERSISTENCE AUDIT: PASS (absent from desktop trust store, Android app files, logcat)
FRIENDSEND RESTART TRUST: initially FAIL (bug 1), fixed, PASS on debug and release
DESKTOP RESTART TRUST: PASS
BOTH-SIDES RESTART TRUST: PASS
TLS / SPKI PIN + DESKTOP ED25519 AUTH: PASS (real secure sends succeeded through the production transport; no plain-HTTP fallback exists in this path)
REAL SECURE SEND / RECEIVED INTEGRITY: PASS (byte counts matched; SHA-256 verified by receiver before RECEIVED)
CONTENT URI / SHARESHEET: PASS (real content:// URI, real system chooser opened) — emulator only
ANDROID NSD ADVERTISEMENT + DESKTOP MDNS DISCOVERY: PASS in emulator (real NsdManager -> real zeroconf); emulator NAT address not directly dialable, test-only adb forward used for TCP. Does NOT close A15-PHYSICAL-MDNS-DISCOVERY.
PASSTHROUGH / REMUX / TRANSCODE: PASS / PASS / PASS, originals unchanged
CANCEL: PASS (cancelled at 4,456,448 of 6,566,667 bytes; desktop CANCELLED; receiver honest INCOMPLETE_TRANSFER; no false RECEIVED)
TEMP CACHE / DISCARD / TTL: NOT RUN on emulator (release build not inspectable); existing Flutter tests cover it
LOGCAT SECRET AUDIT: PASS
PERMISSION / FILEPROVIDER AUDIT: PASS (merged release manifest unchanged: INTERNET only)

TESTS/BUILDS AFTER FIXES:
- flutter analyze: No issues found
- flutter test: 60/60 (58 baseline + 2 new)
- Kotlin JVM unit tests: 4/4 (new)
- APK debug build: PASS
- APK release build: PASS (51.7MB)
- desktop suite: not re-run (no Python change); last full run 907/907 x3 in A17

BUGS FOUND / FIXED: 2 (see docs/PHYSICAL_ANDROID_VALIDATION.md, "Android Emulator Validation"):
1. Persistent trust not reflected in UI after restart (critical-blocker class "pairing required again after normal restart") — fixed.
2. Shared file displayed as internal incoming-<uuid>.bin — fixed at FileProvider level; emulator Sharesheet preview chip still shows the internal name (needs confirmation on a real handset).

NOTES:
- The Android Studio flatpak GUI launch of the emulator crashed (exit 134); the SDK emulator run directly with -gpu swiftshader_indirect works.
- The release APK is still signed with the debug key (pre-existing, non-blocking).

A9-CRASH-RANGE-E2E: CLOSED
A14-PHYSICAL-ANDROID-SHARE-SMOKE: OPEN (emulator does not satisfy the physical-device requirement)
A15-PHYSICAL-MDNS-DISCOVERY: OPEN (emulator does not satisfy the physical-device requirement)
NEXT REQUIRED ACTION: physical Android A17 validation only.

PLANNED POST-A17 FEATURE (not implemented, not scoped, not part of A17): FriendSend Direct Share Target Picker
After FriendSend receives and verifies media, it should resolve installed Android apps that can handle ACTION_SEND for the received MIME type and present a simple in-app destination picker (Messenger, WhatsApp, Telegram, Signal, etc.). Selecting an app launches a targeted ACTION_SEND intent with the existing content:// FileProvider URI. The selected target application remains responsible for showing its own recipient/contact chooser. FriendSend does not access friend lists, contacts, private APIs, accessibility automation, or social-app internals. A permanent "More apps..." fallback opens the normal Android Sharesheet.
Implementation would need a ShareTargetResolver, Android package-visibility <queries> declarations, MIME-aware filtering, safe package targeting, and fallback behavior. It must remain independent of Device Mode transport/security. A separate prompt is opened only after A17 passes.

PHYSICAL VERIFICATION POINT (not new debt): Sharesheet filename
The FileProvider already returns DISPLAY_NAME = the real received name (verified by an in-process query). On the physical phone, confirm whether the system Sharesheet preview chip shows the provider DISPLAY_NAME or derives the name from the URI path. Fold this into the A14-PHYSICAL-ANDROID-SHARE-SMOKE run.
