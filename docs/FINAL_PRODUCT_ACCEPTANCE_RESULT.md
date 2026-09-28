# Final GUI/UX + Device Mode Acceptance — Result

Baseline `a6a64ed` · acceptance changes committed on top (see "Final head"). Scope: test/acceptance only; only bugs proven by acceptance were fixed.
**Gate: PASS after physical run (2026-09-28, see "Physical Android run")** — emulator run left A14/A15 OPEN and A17 BLOCKED; the physical Samsung S24 run below closes them, with one new finding (stale NSD, `A17-STALE-NSD-ONLINE`).

## Evidence classes (kept separate on purpose)

| Class | What was run |
|---|---|
| Unit | Python presentation/model/controller tests; Dart state-machine and bridge tests; Kotlin JVM policy tests |
| Integration | Real `DownloadManagerService` + real HTTP fixture server + production Qt widgets (offscreen) |
| Real process | Real Dart receiver process + real FFmpeg + real A15 TLS-pinned transport driven through the production Send dialog |
| Real network | Loopback TCP (fixture server, Dart receiver, `adb forward`); real zeroconf browser |
| Android emulator | Emulator `sdk_gphone16k_x86_64` (getprop API level 37), debug **and** release APK, 9 real preinstalled ACTION_SEND video apps + 2 test apps |
| Physical Android | **None available** |

## Results

| Item | Result | Class |
|---|---|---|
| Desktop full suite | **1105 passed** (baseline 1089 + 16 acceptance tests) | unit/integration |
| Desktop 3× stability | 1105 / 1105 / 1105 passed, no intermittent failure | — |
| `flutter analyze` | no issues | — |
| `flutter test` | **101 passed, 1 skipped** | unit/widget |
| Skipped test | placeholder in `test/screenshots_test.dart` registered only when `FS_SCREENSHOT_DIR` is unset; the screenshot harness is manual infrastructure that writes PNGs and asserts nothing functional. Layout/overflow coverage is separate (`ui_states_test`, 6 viewport/theme combinations) and desktop screenshots have their own pytest guard. It hides no functional coverage. | — |
| Kotlin/JVM | 12 passed (`ShareTargetPolicyTest` 8, `SanitizeShareDisplayNameTest` 4) | unit |
| APK debug / release | both build; 182.9 MB / 53.1 MB | — |
| Merged manifest audit | release: `INTERNET` only (+ AGP's own `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`); `<queries>`: text/plain (Flutter) and ACTION_SEND video/* + audio/*; no QUERY_ALL_PACKAGES, READ_CONTACTS, MANAGE_EXTERNAL_STORAGE, no storage permission; `debuggable` only in the debug manifest | static |

### Desktop real GUI (production widgets, real service, scheduler observed from the server's request order)

New file `tests/test_final_acceptance_gui_queue.py` (9 tests):
- **Priority**: High/Low set through the context menu change real dispatch order (C high → B → A → D low), one service call per click.
- **Reorder**: "Move up" after Waiting-filter + name sort hits exactly `move_before(B, A)`; dispatch follows.
- **Hold ≠ Pause**: Hold → queue-level PAUSED, entry never requested by the server while another item ran, Release dispatches it; Pause → task PAUSED, `bytes_downloaded` frozen for 0.5 s, Resume continues; calls are `hold` vs `pause_transfer`.
- **Cancel**: terminal CANCELLED history entry, queue moves on, nothing else deleted.
- **Multi-selection** via the bulk bar: Hold/Release/Cancel one call per entry; selection identity survives backend churn.
- **Stale selection**: a menu built before a reorder still hits the original `queue_entry_id`.
- **Filter/sort**: search + sort do not change the target.
- **Retry now**: re-uses the real E2E (`test_gui_e2e.py::test_gui_retry_now_via_button`, injected retryable mapper — the production mapper is non-retryable by design).
- **Path privacy**: no table/model role contains the download directory; only `completed_file()` (Open / Open folder / Details) does.
- **Details** for active/paused/held/queued/completed/failed read only real snapshot fields.

Share by Link (`ShareLinkService.create_link`, no URL/QR fabricated) and the Share selector independence were covered in I5.1; Send-to-Device states (no device / offline / online / identity changed) in `test_gui_device_dialogs.py`.

### Desktop → real receiver through the final Send dialog (`tests/test_final_acceptance_device_gui.py`, 5 tests)
Real download → Artifact → Share/Send dialog → A16 preparation (real FFmpeg) → A15 TLS-pinned Ed25519 transport → real Dart receiver → RECEIVED:
- PASSTHROUGH: stages `Checking → Sending → Received`, receiver SHA-256 = original.
- REMUX: `Checking → Preparing compatible copy… → Sending → Received`, derived hash differs, original bytes unchanged.
- TRANSCODE: `Checking → Converting video… → Sending → Received`; no "Converted x of y" is shown because a downloaded Artifact has no known duration.
- Endpoint churn: stale stored port + discovery announcement → endpoint refreshed, SPKI pin and device_id unchanged, send succeeds.
- Identity change (a different TLS identity answering at the discovered endpoint): dialog shows "Device identity changed", only "Forget device" is offered (no trust/accept/certificate button), row is IDENTITY_CHANGED, the stored pin is untouched, no media reached the impostor.

### Android emulator (real APKs, real installed apps; desktop = production dialogs/services; transport accommodations: `10.0.2.2` alias for the pairing payload and `adb forward`, test setup only)
- Real pairing with the new UI (debug and release APK); FriendSend restart → Ready, trust restored; desktop process restarted between every step and no re-pair was needed.
- **ShareTargetResolver against real installed apps**: picker listed the 9 preinstalled ACTION_SEND video apps with their real labels and launcher icons (Maps, Bluetooth, Gmail, Chat, Drive, Messages, Quick Share, Photos, YouTube) plus **`test.sharesink`**, an APK built for this test that FriendSend has no knowledge of ("ZZ Test Sink"); `test.imageonly` (ACTION_SEND `image/*` only) was correctly **absent**. Sources: `tools/emulator_sink/`.
- **Targeted ACTION_SEND**: tapping the unknown app launched exactly that component; the app read `content://` with `DISPLAY_NAME=holiday.mp4`, `read == size == 297427`, SHA-256 equal to the desktop Artifact hash (passthrough); transcoded file arrived as `movie.mp4` (379838 bytes). Same on the **release** APK.
- **More apps… / Open Android Sharesheet**: genuine system chooser (`com.android.intentresolver`), separate from the grid; Back returns to FriendSend.
- **Target vanished** (uninstalled while the picker was open): no crash, the picker re-resolved and the app disappeared from the list.
- Received screen → Done removes the temporary copy (`cache/friendsend` empty); Discard likewise.
- PASSTHROUGH / REMUX / TRANSCODE all delivered to the emulator (`RECEIVED` on the desktop, "Video ready" on the phone), originals unchanged.
- **Cancel** at 4 % of a 21.7 MB transfer: desktop CANCELLED, no phone "received", partial file gone.
- **Forget / re-pair**: after the phone forgot the computer, a send failed with "FriendSend Android doesn't recognise this computer" (`UNTRUSTED_DESKTOP`); re-pairing restored sending.
- **Logs**: full `adb logcat` (2997 lines) contains no secret, key, pairing-payload or HMAC/proof strings; the desktop `rychlik.device` package contains no `logging`/`print` calls.
- **Real mDNS**: the desktop's real zeroconf browser found the emulator's `_friendsend._tcp` advertisement (correct device_id/profile/new port after a phone restart) in one run, but returned nothing in later runs although `dumpsys servicediscovery` showed the advertisement — emulator multicast is unreliable; the advertised `10.0.2.x` address is never dialable from the host. This is **not** evidence for A15's physical NSD item.

## Bugs found by acceptance

| # | Bug | Root cause | Fix | Regression |
|---|---|---|---|---|
| 1 | Desktop Send dialog said "Checking the file…" for the whole REMUX/TRANSCODE phase and never showed conversion progress | `DeviceHandoffService` published `preparation_kind` only **after** FFmpeg finished and emitted no event during preparation | `prepare(plan_callback=…)` reports the plan kind first; service emits `HANDOFF_PREPARING` when it is known, throttled progress events during preparation, and one event on CONNECTING | `test_plan_callback_reports_the_plan_kind_before_preparation_runs`, `test_snapshot_publishes_the_preparation_kind_during_a_real_transcode` (real Dart receiver + FFmpeg), GUI stage-sequence tests; both fail without the fix |
| 2 | FriendSend receiving screen never showed a speed on a real transfer | speed was computed per event with a 50 ms minimum gap, but the receiver emits an event per network chunk (~10–30 ms) | speed measured over a ≥ 0.4 s window with smoothing | `speed is measured even when progress events arrive only milliseconds apart` (fails without the fix); verified on the emulator (530 KB/s) |

## Observations (not defects, or by design)

- The **system Sharesheet preview** shows the internal cache name (`incoming-<uuid>.bin`) for the "More apps…" path on this emulator image, while every receiving app gets `DISPLAY_NAME=holiday.mp4`. This is the outstanding A17-E1 point: it must be re-observed on a physical phone before deciding whether it is a product problem.
- A desktop-initiated cancel appears on the phone as "Transfer interrupted" (the wire protocol cannot distinguish it from a lost connection); no false success, partial removed.
- A second incoming transfer replaces the received-file screen; the earlier verified temp file is then only removed by TTL cleanup.
- Desktop visual review vs approved mockups (1366/1920/2560/1100, dark+light, Noto Sans): table, sidebar, density, colours and control sizes match; documented deviations remain (no Start/Stop Queue, Scheduler, aggregate speed/limit in the status bar; Send dialog stepper is plain text rather than numbered pills; no info banner in the preparing dialog; native window frame). FriendSend at 360×800, 390×844, 412×915 (dark+light) rendered without overflow, real Roboto, and matches mockups apart from the documented items in `FINAL_GUI_UX_IMPLEMENTATION_RESULT.md`.

## Not verified

Direct Share behaviour, Wi-Fi network loss, physical A16 REMUX path, which real app was picked in Choose app (user did not name it), instrumented (non-manual) read of the shared file by the target app. Physical results below for Choose app / Sharesheet are user-observed, not logged by tooling.

## Physical Android run (Samsung S24, real Wi-Fi 192.168.123.x, no adb forwarding)

Evidence class: **physical**. Desktop side: production `DeviceModeController` with the real app data directory and real zeroconf; the send was driven by a script calling the same controller/service the GUI Send dialog uses (not by clicking the dialog).

1. **Pairing by QR** (new): desktop Pair dialog rendered the payload as a QR code, FriendSend scanned it with the camera, trust established (user-confirmed; trust store holds the device with endpoint 192.168.123.132). QR round-trip is also unit-tested by decoding the rendered image (OpenCV Aruco decoder).
2. **A15 physical NSD**: the phone appeared as `TRUSTED_ONLINE` at 192.168.123.132 from real mDNS; the endpoint was refreshed from discovery (endpoint metadata only, pin untouched), including after the receiver port changed on app restart.
3. **Secure send**: `pinned-tls-signature-v1` send of a small MP4 (PASSTHROUGH) → `RECEIVED` (15:22:11); source file unchanged.
4. **A16 physical transcode**: stages `Checking → Converting video → Connecting → Sending → Received` (15:26:31) → `RECEIVED`.
5. **A14 / A17-E1 (user-observed)**: Choose app → a real installed target opened the file; More apps… opened the Android Sharesheet whose preview shows **`holiday.mp4`** (the emulator showed the internal `incoming-<uuid>.bin`). Both paths "worked" per the user.
6. **New finding `A17-STALE-NSD-ONLINE`**: after the user left FriendSend, the phone's receiver port was closed but the desktop still listed the device `TRUSTED_ONLINE` (mDNS record not withdrawn); a send at 15:24:47 failed truthfully with `CONNECTION_FAILED` and no source damage. Re-sending after reopening FriendSend succeeded. Truthfulness of the *Online* label is the open point, not the send.
7. **Physical regression of `e044c62` (stale NSD fix), 2026-09-28, same phone**: FriendSend opened → `TRUSTED_ONLINE` 15:45:02; FriendSend closed (mDNS record still present, same endpoint) → `TRUSTED_OFFLINE` 15:45:52, within one 20 s probe interval; reopened (new port) → `TRUSTED_ONLINE` 15:46:09; small MP4 → `RECEIVED` 15:46:17. `A17-STALE-NSD-ONLINE` CLOSED. The transient "Checking…" state was not caught in the log (coalesced with Online); it is covered by a unit test. Identity mismatch was not simulated physically (covered by the real-Dart impostor acceptance test).
8. **Intermittent, not reproduced**: `test_pair_dialog_qr_decodes_to_exact_code` failed once in one full-suite run; 12 isolated runs, 180 random payloads at 3/4/5 px per module and two later full runs (1106 passed) did not fail.

## Validation debts

`A9-CRASH-RANGE-E2E` CLOSED · `A14-PHYSICAL-ANDROID-SHARE-SMOKE` CLOSED (physical, user-observed target/Sharesheet) · `A15-PHYSICAL-MDNS-DISCOVERY` CLOSED · `A17` PASS · `A17-STALE-NSD-ONLINE` CLOSED (`e044c62`, physically re-verified). Device Mode is frozen.

`tools/emulator_driver/` and `tools/emulator_sink/` hold the scripts and test-app sources used for the emulator run (reference; the scripts contain paths of the original session).
