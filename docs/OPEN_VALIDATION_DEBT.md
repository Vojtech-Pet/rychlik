# Open Validation Debt

Tracks real end-to-end scenarios that are known to be untested (or only
partially tested) even though the underlying unit/integration coverage is
green. An item here is closed only when the actual combined scenario is
executed, never merely because its component parts pass separately.

---

## A9-CRASH-RANGE-E2E

**Status:** OPEN
**Blocking A10:** NO
**Blocking functional GUI (A11):** NO
**Blocking beta/release gate:** YES

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

---

## A13-PRODUCTION-SECURE-CHANNEL

**Status:** OPEN
**Blocking A13 foundation:** NO
**Blocking real LAN beta:** YES

**Description:** Prompt A13's `HttpFriendSendTransport` (the real, non-mock
Device Mode transport) is plain HTTP plus a bearer-style auth token
established during pairing. It is explicitly labeled EXPERIMENTAL/TEST
TRANSPORT (see `docs/DEVICE_MODE_FOUNDATION.md`, "Security boundary").
There is no TLS, no certificate pinning, no Noise-protocol-grade
authenticated encryption, and no pairing-derived session key. An auth
token observed on an untrusted local network (e.g. via ARP spoofing or a
compromised device on the same LAN) could impersonate a paired device for
the lifetime of that token.

**Why not closed during A13:** deliberately deferred per the prompt's own
guidance (§35-37) — final network security choices (TLS/mTLS/Noise/
pairing-derived keys) are explicitly deferred until a real Android
FriendSend peer exists (A14), since designing the final secure channel
without a real second implementation to validate it against risks freezing
the wrong contract. A13's job was to prove the handoff *protocol*
(versioning, capability negotiation, integrity, cancellation, truthful
acknowledgement), not to ship a production-secure transport.

**What would close it:** a real authenticated, encrypted channel (e.g.
mutual TLS with pairing-derived certificate trust, or an equivalent
Noise-protocol handshake) implemented and verified against a real second
peer (the Android app from A14), with a test proving an attacker
observing the wire cannot replay a captured auth token from a different
network position/session.

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

---

*(Future validation debt items should be appended below, each with the
same Status/Blocking/Description/Why-not-closed/What-would-close-it
shape.)*
