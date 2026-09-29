# Physical Android Validation Log

Non-sensitive evidence log for real, physical-device Device Mode
validation runs. No pairing secrets, private keys, certificate private
material, personal media, or device serials are recorded here -- only
outcomes. Introduced by Prompt A17; append future runs below rather than
overwriting this one.

---

## 2026-09-28 -- Prompt A17 attempt

**Date:** 2026-09-28
**Desktop commit at time of attempt:** fde1969ccb4dc871497059f06d0beb63dcf362e0 (A16)
**Result:** BLOCKED -- no physical Android device and no emulator available

**Environment checked:**

```text
$ adb --version
Android Debug Bridge version 1.0.41
Version 37.0.0-android-tools

$ adb devices -l
List of devices attached
(empty)

$ flutter devices
Found 1 connected device:
  Linux (desktop) • linux • linux-x64 • ...
(no Android device or emulator listed)

$ emulator -list-avds
bash: emulator: command not found
```

**Conclusion:** this environment has `adb` installed but no device ever
attached, and no Android emulator system image or `emulator` binary
present (same constraint independently confirmed during A14 and A15).
None of the physical scenarios required by Prompt A17 §14-98 (real
pairing, persistent trust across a real restart, real `NsdManager`
advertisement discovered by real desktop `zeroconf`, real secure send to
a real phone, real `content://` Sharesheet, physical passthrough/remux/
transcode, physical cancel, physical forget/re-pair) could be executed.

Per Prompt A17 §2/§120, this means A17 is reported `STATUS: BLOCKED`,
`GATE: FAIL` for the physical-validation portion of its scope -- not
`COMPLETE`, even though every automatable portion of A17 (the combined
A9 crash-to-Range E2E test, full desktop/Flutter regression, static
security/permission/manifest audits, both APK builds) passed. See
`docs/DEVICE_MODE_RELEASE_HARDENING_RESULT.md` for the full breakdown.

`A14-PHYSICAL-ANDROID-SHARE-SMOKE` and `A15-PHYSICAL-MDNS-DISCOVERY`
remain OPEN in `docs/OPEN_VALIDATION_DEBT.md`.

**What would unblock this:** attaching a real Android phone/tablet over
USB with developer options + USB debugging enabled (`adb devices -l`
showing it), or installing an Android emulator system image so
`emulator -list-avds` lists an AVD and `flutter devices` shows it as a
runnable target -- then re-running this checklist against
`docs/DEVICE_MODE_RELEASE_CHECKLIST.md`.

---

# Android Emulator Validation (Prompt A17-E1)

**This section is emulator evidence only. It is NOT physical-device
evidence and does not close `A14-PHYSICAL-ANDROID-SHARE-SMOKE` or
`A15-PHYSICAL-MDNS-DISCOVERY`, both of which explicitly require a real
physical Android device.** It strengthens confidence from "host Dart
receiver harness" to "the real FriendSend Android app running in a real
Android emulator".

**Date:** 2026-09-28
**Baseline commit:** c86ff989dca893d4cd274254499d9c94eb713603 (A17)
**Emulator:** Android Studio AVD `Medium_Phone`, `sdk_gphone16k_x86_64`, Android 17 (API 37), launched directly via the SDK `emulator` binary with `-gpu swiftshader_indirect` (the Android Studio flatpak GUI wrapper's own launch crashed with exit 134 after a GPU-driver fallback; running the SDK emulator directly from a shell worked).
**FriendSend build:** debug and release APKs from this run (both installed and exercised).
**Environment notes:** networking accommodations (`adb forward`, the `10.0.2.2` host alias used only while generating the pairing payload) live entirely in test setup, never in product code.

| Area | Result |
|---|---|
| Fresh pairing payload, new local secret, never printed/logged/reused | PASS |
| Real production A15 pairing (`pinned-tls-signature-v1`) via the real FriendSend UI | PASS |
| Pairing secret absent from desktop trust store, Android app storage, logcat | PASS |
| Persistent trust: FriendSend force-stop/relaunch | **FAIL, then FIXED** (see bug 1) → PASS |
| Persistent trust: desktop fresh-process reload | PASS |
| Persistent trust across debug↔release APK swap | PASS |
| Android `NsdManager` advertisement discovered by real desktop `zeroconf` | PASS (device_id/protocol/profile correct; advertised `10.0.2.x` address not directly dialable from the host, so a test-only `adb forward` was used for the actual TCP connection) |
| Real completed download → Artifact → secure handoff → emulator `RECEIVED` (PASSTHROUGH) | PASS, byte-exact |
| MKV/H.264/AAC → REMUX → secure handoff → `RECEIVED`, original unchanged | PASS |
| VP9/Opus/WebM → TRANSCODE_AUDIO_VIDEO → secure handoff → `RECEIVED`, original unchanged | PASS |
| Real Android Sharesheet opened (real `ACTION_SEND` chooser, real `content://` FileProvider URI) | PASS |
| Sharesheet/FileProvider display name | FIXED at provider level (see bug 2); this AVD's own Sharesheet preview chip still shows the internal name |
| Cancel mid-transfer (4.4 MB of 6.5 MB sent): desktop `CANCELLED`, receiver shows honest `INCOMPLETE_TRANSFER`, no false `RECEIVED`, no crash | PASS |
| logcat secret/key scan (pairing secret, key/bearer keywords) | PASS, none |
| Merged release manifest: only `INTERNET` + AndroidX signature permission, `FileProvider` `exported=false`, no cleartext/debuggable | PASS |

**Not exercised / limits of this evidence:** TTL expiry and Discard on the
phone (release build is not `run-as` debuggable; covered by existing
automated Flutter tests only), physical Wi-Fi/LAN mDNS (emulator NAT is
not a real LAN), background/screen-off behavior, forget/re-pair, large
files, network loss mid-send, and anything requiring a real handset.

**Bug 1 — persistent trust not reflected in UI after restart.**
- Symptom: after force-stop/relaunch, FriendSend showed the initial "Paste pairing payload" screen although `friendsend_trusted_desktops.json` still held the trusted desktop.
- Root cause: `main.dart` never consulted `DesktopTrustStore` at startup; `HandoffController` always initialised `AppState.unpaired`.
- Owning layer: FriendSend Dart app startup wiring.
- Fix: `HandoffController.restoreTrustState(DesktopTrustStore)`, called from `main()`.
- Regression: 2 new tests in `friendsend/test/handoff_controller_test.dart` (real `DesktopTrustStore`, real temp dir): trusted desktop on disk → `paired`; empty store → stays `unpaired`. Re-verified on the emulator (debug and release).

**Bug 2 — shared file displayed as the internal `incoming-<uuid>.bin`.**
- Symptom: the Android Sharesheet listed the shared item by its TempCache filename.
- Root cause: Kotlin `handleShareFile` passed the received `displayName` only as the chooser dialog title and used the 3-arg `FileProvider.getUriForFile`, so the URI's DISPLAY_NAME was the physical UUID filename.
- Owning layer: Android native Kotlin bridge (`MainActivity.kt`).
- Fix: keep the physical temp file UUID-named (TempCache invariant unchanged), use the 4-arg `getUriForFile(..., displayName)` overload (AndroidX Core 1.13.1 resolved; needs ≥1.5.0) with a new top-level pure `sanitizeShareDisplayName()` (basename only, control chars/NUL stripped, safe fallback), and a generic chooser title.
- Regression: 4 JVM unit tests (`SanitizeShareDisplayNameTest`: normal name, `../../evil.mp4` → `evil.mp4`, empty/dot fallback, control chars). Verified live: an in-process `ContentResolver.query()` on the generated URI returned `DISPLAY_NAME=a17e1_passthrough.mp4`, and the URI carried `?displayName=a17e1_passthrough.mp4`.
- Residual: this emulator's Sharesheet preview chip still renders the URI's physical last path segment. Any recipient that queries `OpenableColumns.DISPLAY_NAME` sees the correct name, but this cosmetic behavior was not confirmed on a real handset and remains for the physical run to check.
- Test gaps recorded honestly: no instrumented (`androidTest`) suite exists, so the FileProvider DISPLAY_NAME and path-containment behavior is verified only by the live in-process query above, not by an automated instrumented test (pre-existing A14 gap).
