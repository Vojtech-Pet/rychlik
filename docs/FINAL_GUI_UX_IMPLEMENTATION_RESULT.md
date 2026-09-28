# Final GUI/UX Implementation — Result

Design baseline: approved mockups at commit `b0ae92ea` (tokens `design/final_design_tokens.json`).
Scope guard: no backend rewrite, no A18/monetization/cloud/download-engine work. A14/A15 physical validation debts stay **OPEN**; A17 stays **BLOCKED** (no physical Android device).

## Commits

| Stage | Commit | Content |
|---|---|---|
| I1 | `45bef68` | Desktop design system: tokens, QSS generator, icon registry, ThemeManager |
| I2a | `d4e0574` | Additive presentation data (`source_host`, `added_at`, retry countdown) |
| I2/I3 | `90018a3` | Desktop shell, model/view table, queue controls, dialogs, settings |
| I5 | `491e4ff` | DeviceModeController, Share/Send/Pair dialogs, Devices page, trust-store device resolution |
| I5.1 | `013b035` | Share by Link restored to its pre-redesign behaviour (independent branch) |
| I6/I7 | `c2eca2d` | FriendSend theme (generated tokens), presentation state machine, screens, Discard via TempCache |
| I8 | `b4c4265` | Kotlin ShareTargetResolver, targeted ACTION_SEND, picker wiring |
| I9/I10 | this commit | Visual polish from screenshot review, screenshot harnesses, regression guard, this document |

I6 and I7 share one commit because the screens render the state machine directly; splitting would have left a non-compiling intermediate.

## Design decisions that follow function, not mockup

- Only real service data is shown. No fake sizes, speeds, devices or delivery claims.
- Identity is `queue_entry_id` end to end; one service call per action; no optimistic state.
- Hold ≠ Pause. Reorder only inside a priority band. Retry now only for RETRY_WAIT.
- Device identity is `device_id`; discovery refreshes only the endpoint before send; the TLS pin is never re-pinned; identity-changed offers only "Forget device".
- FriendSend `verifying` is entered only when every byte has arrived and the hash check is still running; `handoffAccepted` means only that Android accepted the launch.

## Screen implementation matrix

Desktop (real widgets, screenshots in `artifacts/gui_implementation_review/desktop/{dark,light}/`)

| Screen | Class | Data source | Status |
|---|---|---|---|
| Main window (1100/1366/1920/2560) | `MainWindow` + `DownloadManagerWidget` | DownloadManagerService snapshot | Implemented |
| Multi-selection bulk bar | `DownloadManagerWidget` | selection + `presentation.bulk_actions` | Implemented |
| Context menus (state-sensitive) | `build_context_menu` | `presentation.available_actions` | Implemented |
| Queues view | `queue_view.py` | manager snapshot | Implemented |
| Details / Add / Settings | `dialogs/*` | snapshot / service | Implemented |
| Share selector | `ShareSelectorDialog` | artifact + trusted devices | Implemented |
| Share by Link | `ShareByLinkDialog` | `ShareLinkService.create_link` | Implemented; shows only real status |
| Send to Device (select, Preparing, Remux, Transcoding, Sending, Received, Identity changed, Error) | `SendToDeviceDialog` | `DeviceHandoffSnapshot` | Implemented |
| Pair FriendSend | `PairDeviceDialog` | `SecurePairingManager` real session | Implemented |
| Devices page + Forget confirm | `DevicesPage` | trust store + discovery | Implemented |

FriendSend (Flutter; renders in `artifacts/gui_implementation_review/friendsend/{390x844,360x800,412x915}/{dark,light}/`, emulator capture in `.../emulator/`)

| Mockup | Screen | Status |
|---|---|---|
| 01 pairing / 01d error / 01e progress | `PairingScreen` | Implemented (01f success merges into Ready) |
| 02 ready | `ReadyScreen` | Implemented; 02c "no Wi-Fi" **not** implemented (no real connectivity signal wired) |
| 03 receiving | `ReceivingScreen` | Implemented, speed measured from real events |
| 04 verifying | `VerifyingScreen` | Implemented; 04b "preparing to share" folded into the picker loading state |
| 05 received | `ReceivedScreen` | Implemented |
| 06 / 07b–07e picker | `TargetPickerSheet` | Implemented, separate "More apps… / Open Android Sharesheet" row always present |
| 07f handoff accepted | `HandoffAcceptedScreen` | Implemented |
| 08 / 08b / 08c error, cancelled, integrity | `ErrorScreen`, `CancelledScreen` | Implemented |
| 09 / 10 trusted computer + forget | `TrustedComputerScreen` | Implemented; name shown is "Rýchlik" (the phone does not know the computer's name) |

## Known deviations from the mockups

- Desktop: no Start/Stop Queue, Scheduler, bandwidth limit, Remove/Delete file, Connections/Log tabs, or retry for FAILED (the backend has none). Sort default is "Queue order". Dialogs use the native window frame.
- Share by Link: no public URL, QR or "Open in browser" — `ShareLinkService` is domain-only and holds no address, so none is shown.
- Desktop Devices sidebar entry exists only when Device Mode is composed.
- FriendSend: QR pairing not implemented (code copy/paste only); no Wi-Fi-off state; verifying progress bar is indeterminate.
- Production RETRY_WAIT is unreachable with the default failure mapper (tests inject a retryable mapper).

## Fonts

Desktop: Noto Sans (Inter is not installed and none is downloaded). FriendSend: Roboto (Android system font; screenshots use Roboto from the Flutter SDK cache). Layouts were re-checked with these real fonts; the pairing arrow glyph was replaced by "›" after Roboto lacked "→".

## Responsive results

Desktop tiers 1000/1100/1366/1920/2560 tested (columns/sidebar collapse). FriendSend rendered without overflow at 360x640, 360x800, 390x844, 412x915, dark and light, with a 150-character filename (widget test `every state renders without overflow`).

## Tests and builds (final run)

- Desktop: `pytest tests` → **1089 passed**.
- Flutter: `flutter analyze` clean; `flutter test` → **100 passed, 1 skipped** (screenshot harness, enabled by `FS_SCREENSHOT_DIR`).
- Kotlin JVM (JDK 21): **12 passed** (`ShareTargetPolicyTest` 8, `SanitizeShareDisplayNameTest` 4).
- `flutter build apk --debug` and `--release` both succeed (release 53.1 MB).
- Token drift: `tests/test_friendsend_token_drift.py` (generator `--check`) and `friendsend/test/design_tokens_test.dart`.
- Desktop visual guard: `tests/test_gui_screenshots.py` (all approved screens, both themes, none blank).

## Not verified

- ShareTargetResolver and targeted ACTION_SEND were verified by JVM policy tests, Dart bridge-protocol tests (simulated native replies) and a native compile — **not** on a device with real share targets. The debug APK was installed on the emulator and the new pairing screen rendered correctly, but no file was received through the new picker there.
- No physical Android device: A14/A15 physical debts remain OPEN; A17 remains BLOCKED.
- Desktop screenshots use a fake `DownloadManagerService` for row data (fixture data, labelled in the harness); dialogs and windows are the real code.
- Desktop screenshots were compared with the mockups by eye on key screens, not pixel-diffed.
