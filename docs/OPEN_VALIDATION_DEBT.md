# Open Validation Debt

Tracks real end-to-end scenarios that are known to be untested (or only
partially tested) even though the underlying unit/integration coverage is
green. An item here is closed only when the actual combined scenario is
executed, never merely because its component parts pass separately.

---

## A9-CRASH-RANGE-E2E

**Status:** CLOSED (Prompt A17, 2026-09-28)
**Blocking A10:** NO
**Blocking functional GUI (A11):** NO
**Blocking beta/release gate:** N/A -- closed

**Description:** A real combined end-to-end scenario —

```text
real subprocess starts a real Range-capable HTTP download
    -> a durable partial checkpoint is written
    -> the process is SIGKILLed mid-transfer (real, ungraceful)
    -> a fresh process opens the same database
    -> RestartRecovery runs
    -> normal A3/A5 dispatch issues a REAL HTTP Range request
       using the recovered, locally-re-validated partial
    -> byte-exact completion
```

has not been executed as ONE subprocess test. What exists instead,
separately:

- Prompt A8's `test_real_crash_recovery_e2e`
  (`tests/test_real_process_crash_e2e.py`): a real `SIGKILL` against a
  real child process, real recovery, and a real redownload — but against
  the plain `/slow` fixture route (no `ETag`/`Range` support), so the
  redownload after recovery is a full restart from byte 0, not a Range
  resume.
- Prompt A9's real pause/resume/retry-resume E2E tests
  (`tests/test_runtime_pause_resume_e2e.py`,
  `tests/test_dispatch_coordinator_resume.py`): real `Range`/`If-Range`
  resume against the `/resumable/<key>` fixture, including a real dropped
  connection mid-transfer — but no process is ever actually killed;
  interruption is simulated by closing the fixture's own connection or by
  a Python-level `DownloadPaused`/retry, all within the same test process.
- Focused non-subprocess tests
  (`tests/test_restart_recovery.py::test_paused_with_valid_partial_stays_paused`
  and friends) prove `RestartRecovery`'s local partial-validation logic in
  isolation, without a real process boundary at all.

Each piece is real and independently proven, but the exact combination —
a genuinely killed process whose surviving partial state is then actually
validated over the network via a real `Range` request in a new process —
has never run as a single test.

**Why not closed during A9 or A10:** explicitly scoped out of A9 (see
`docs/SAFE_PARTIAL_RESUME_RESULT.md`, KNOWN LIMITATIONS) given the time
that phase already took; A10 is an application-facade phase and
intentionally does not touch acquisition/resume mechanics, so it is not
positioned to close this either.

**What would close it:** a test that starts a real child process against
a `/resumable/<key>`-style fixture route, waits for real evidence of a
durable partial checkpoint (not just `TRANSFERRING`), `SIGKILL`s it, then
in a fresh process runs recovery and normal dispatch and asserts the
resulting real HTTP request carries `Range: bytes=<durable_bytes>-` (not
`bytes=0-`) before completing byte-exact.

**Closed by:** `tests/test_a9_crash_range_e2e.py::test_a9_crash_range_e2e`
(Prompt A17). A real child process (`tests/_a9_crash_range_worker.py`)
downloads against a real `/resumable/<key>` fixture route with ETag
`"a17-crash-range-v1"` and a small (50,000-byte) checkpoint threshold. The
parent waits for real evidence of a durable `PartialTransferState` with
`durable_bytes > 0` and a captured `prefix_sha256`/validator committed to
the real SQLite file (also confirming the raw `.part` file on disk was
`>= durable_bytes`, proving recovery cannot trust uncheckpointed bytes),
then `SIGKILL`s the child. A fresh process opens the same database,
`RestartRecovery` moves the task `TRANSFERRING -> READY` with
`attempt_count` preserved at `1` and the durable partial/validator carried
forward intact, and a brand-new dispatch in that same process issues a
real HTTP request the fixture server actually observes with
`Range: bytes=<durable_bytes>-` and `If-Range: "a17-crash-range-v1"`
(never `bytes=0-`), completing `COMPLETED` with `attempt_count == 2`, a
byte-exact final file, and all partial/queue metadata cleaned up. Run 6x
consecutively with no flake, and as part of 3 full clean desktop suite
runs (907/907 each).

---

## A13-PRODUCTION-SECURE-CHANNEL

**Status:** CLOSED (Prompt A15)

**Original description:** Prompt A13's `HttpFriendSendTransport` (the
real, non-mock Device Mode transport) was plain HTTP plus a bearer-style
auth token established during pairing, with no TLS, no certificate
pinning, no Noise-protocol-grade authenticated encryption, and no
pairing-derived session key.

**Why it is closed now, and exactly what changed:** Prompt A15 replaced
the *default* production Device Mode transport with
`pinned-tls-signature-v1`
(`rychlik.device.security.secure_transport.SecureFriendSendTransport`):

- `DeviceHandoffService()` with no explicit `transport` argument now
  constructs the secure transport, not `HttpFriendSendTransport` — proven
  by `test_default_transport_is_secure_not_plain_http`. The plain-HTTP
  transport remains available, but only when a caller explicitly passes
  `transport=HttpFriendSendTransport()` (all such call sites in the test
  suite were updated to do this explicitly, matching the A15 prompt's
  own §3 allowance for "isolated tests, legacy deterministic fixtures,
  explicit development mode").
- A real TLS connection with mandatory SPKI-pin verification happens
  before any application byte is sent
  (`secure_transport.connect_and_verify_pin`) — proven by
  `test_real_wrong_pin_e2e_sends_zero_payload` (0 bytes sent on a pin
  mismatch against a real second Dart TLS server).
- Desktop authentication is cryptographic (Ed25519 challenge/response,
  bound to the specific handoff + artifact identity) — proven by
  `test_real_wrong_signature_e2e_sends_zero_payload` and
  `test_real_auth_replay_e2e`.
- Persistent trust survives a full restart of both the desktop identity/
  trust store and the real Dart receiver process, without re-pairing —
  proven by `test_real_persistent_trust_restart_e2e`.
- No code path in `DeviceHandoffService`/`SecureFriendSendTransport`
  automatically falls back to the plain-HTTP profile.
- A real Python↔Dart secure pairing and secure handoff E2E both pass
  against the real Dart receiver core (`bin/secure_receiver_harness.dart`
  / `bin/pairing_client_harness.dart`), not a fixture --
  `tests/test_friendsend_secure_lan_e2e.py` (8 tests, including a real
  TLS wire-encryption capture proving a plaintext media marker never
  appears on the wire).

See `docs/FRIENDSEND_SECURITY_PROFILE_V1.md` for the full normative
design and threat model, and `docs/FRIENDSEND_DESKTOP_SECURITY.md` /
`docs/FRIENDSEND_ANDROID_SECURITY.md` for the implementation detail on
each side. Two narrower items remain open as their own debt entries
below: real Android `NsdManager` mDNS advertisement was never exercised
on a real Android OS (`A15-PHYSICAL-MDNS-DISCOVERY`), and Android backup
exclusion for the identity/trust files was not implemented in this phase
(see `docs/FRIENDSEND_ANDROID_SECURITY.md`, "Android backup exclusion").

---

## A14-PHYSICAL-ANDROID-SHARE-SMOKE

**Status:** OPEN
**Blocking A14 automated gate:** NO
**Blocking beta/release:** YES

**Description:** Prompt A14's strongest possible test --

```text
Rýchlik desktop -> real Android device running FriendSend
    -> real receive over a real LAN
    -> Android Sharesheet actually opens on that device
```

was never executed. Neither a usable Android emulator (system image +
`emulator` binary) nor a physical Android device were available in the
environment this phase was executed in, even though KVM (`/dev/kvm`) is
present and an Android SDK, platform, and build-tools were installed for
the phase (see `docs/FRIENDSEND_ANDROID_MVP_RESULT.md`, ANDROID
TOOLCHAIN). This item covers both the emulator-based E2E (A14 prompt
§105) and the physical-device manual smoke test (§107) -- both require
an actual running Android OS, which neither the emulator nor a physical
device provided here.

**Why not closed during A14:** explicitly permitted to remain open by
the phase's own acceptance gate (§108: "Do not fail otherwise-complete
automated A14 purely because no physical phone exists"). What *was*
independently verified instead: a real debug and release APK build
(`flutter build apk --debug` / `--release`), the FileProvider/manifest
configuration inspected directly against the built APK via `aapt dump
xmltree`/`aapt dump permissions`, and the full protocol/streaming/
integrity/cancellation logic proven via a real Python<->Dart
cross-language socket E2E (`tests/test_friendsend_android_cross_language_e2e.py`)
driving the exact same receiver code the Flutter app ships, without an
Android runtime.

**What would close it:** installing an Android emulator system image (or
attaching a physical device via `adb`) and running the real end-to-end
flow above, confirming the Android Sharesheet chooser genuinely opens
with the received file, backed by a real `FileProvider` `content://`
grant on a real Android OS -- something no host-side Dart harness or
cross-language socket test can prove by itself (Prompt A14 §97).

**A17 attempt (2026-09-28):** Prompt A17 was explicitly scoped as the
release-hardening gate meant to close this debt, and unlike A14/A15 does
NOT permit reporting `COMPLETE`/`PASS` while it remains open. `adb devices
-l` returned no attached devices and no `emulator` binary was present in
this environment (`emulator -list-avds` failed: command not found) --
confirmed again at A17 time, same constraint as A14/A15. A17 therefore
closed everything it could without hardware (see
`docs/DEVICE_MODE_RELEASE_HARDENING_RESULT.md`, STATUS: BLOCKED) and left
this item OPEN and unmodified rather than closing it on host-side evidence
alone. See `docs/PHYSICAL_ANDROID_VALIDATION.md` for the recorded attempt.

---

## A15-PHYSICAL-MDNS-DISCOVERY

**Status:** OPEN
**Blocking automated A15:** NO
**Blocking beta/release:** YES

**Description:** Android's native `NsdManager`-based mDNS/DNS-SD
advertisement (`MainActivity.kt::handleRegisterMdns`,
`lib/security/mdns_advertiser.dart`) was implemented and compiles
successfully against the real Android SDK (proven by real debug and
release APK builds in this phase), but was never exercised at runtime on
a real Android OS -- no emulator system image or physical device was
available in this environment (same constraint as
`A14-PHYSICAL-ANDROID-SHARE-SMOKE`).

**Why not closed during A15:** the A15 prompt's own §173/§177 explicitly
anticipates this exact gap and requires it to be tracked as debt rather
than silently skipped or falsely claimed proven, when no emulator/device
is available. What *was* independently verified instead: the
desktop-side discovery half (`rychlik.device.security.discovery
.FriendSendDiscoveryService`, backed by the real `zeroconf` library) was
proven against a real second `zeroconf` advertiser standing in for the
Android side's wire format (`tests/test_friendsend_discovery.py`,
including the combined mDNS-spoof + real-TLS-pin rejection test), and
the security profile's core claim -- "discovery is not trust" -- was
proven with a real second Dart TLS identity as the "spoofed" endpoint,
not a fake listener.

**What would close it:** installing an Android emulator system image (or
attaching a physical device via `adb`) and confirming a real
`NsdManager.registerService()` call from the FriendSend app is actually
discoverable by a real desktop `zeroconf` browser on the same LAN, with
the correct `device_id`/`protocol_version`/`security_profile` TXT
attributes.

**A17 attempt (2026-09-28):** same environment constraint recorded above
for `A14-PHYSICAL-ANDROID-SHARE-SMOKE` -- no physical device or emulator
available in this environment. Left OPEN.

---

## A16 note: no new physical-device debt entry added

Prompt A16 (Device Compatibility / Automatic Transcoding Pipeline) added
a real, fully-tested automatic media preparation pipeline in front of
Device Mode sends (probe -> plan -> remux/transcode -> re-validate),
proven end-to-end with 6 real full-stack tests
(`tests/test_device_media_handoff_e2e.py`) against the real Dart secure
receiver, including a real remux, a real full VP9/Opus transcode, and a
real cancel-mid-transcode.

**No new "A16 transcode smoke" physical-device debt item is added here.**
The one physical gap A16 shares with everything else in Device Mode --
confirming behavior on a real, physical Android device rather than the
Dart receiver core/APK-build/`aapt`-inspection proofs already performed
-- is the exact same gap already tracked by
`A14-PHYSICAL-ANDROID-SHARE-SMOKE` above. A16's prepared/transcoded media
is just another artifact flowing through that same untested physical
receive+Sharesheet path; closing `A14-PHYSICAL-ANDROID-SHARE-SMOKE` on a
real device closes A16's physical tail too, and re-testing it separately
here would be redundant. `A9-CRASH-RANGE-E2E` and
`A15-PHYSICAL-MDNS-DISCOVERY` are untouched by A16 and remain OPEN as
above.

---

*(Future validation debt items should be appended below, each with the
same Status/Blocking/Description/Why-not-closed/What-would-close-it
shape.)*
