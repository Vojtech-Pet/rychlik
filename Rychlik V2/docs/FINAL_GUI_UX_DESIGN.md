# Final GUI/UX Design Gate - Rýchlik Desktop + FriendSend

Status: **DESIGN ONLY.** Nothing here is implemented in Qt/PySide6 or Flutter. No backend, download, sharing,
security, pairing, media or transport code was changed. A14/A15 physical validation debt is untouched.
Companion files: `design/final_design_tokens.json`, `design/SCREEN_INVENTORY.md`, `design/UX_FLOWS.md`,
`design/CONTRAST_AUDIT.md`, `design/mockups/`, `design/prototype/` (non-production HTML generator).

## 1. Design goals

1. One product family, two platform-appropriate apps: same identity (accent, status colors, icon language, type
   character), different geometry (compact desktop table vs calm mobile state machine).
2. Rýchlik stays a download manager first. Send/Share is secondary and contextual.
3. FriendSend is a bridge, not a library: pair once, wait, receive, verify, choose an app, hand off, discard.
4. Truthful states only: every state maps to something the architecture really has or is explicitly marked planned.
5. Calm visuals: color only where it carries meaning; every status also has an icon and a text label.

## 2. Source-material audit (read before designing)

| Input | What it is | Used for |
|---|---|---|
| `/mnt/Data/Rychlik/design.txt` | Desktop implementation prompt: AB-style compact download manager, 30 sections | Canonical desktop layout/hierarchy (menu 28, toolbar ~54, sidebar ~190, row 42, icons 14-18, progress 5-8, details in a separate dialog, Send contextual) |
| `Desktop UI Pack v2/DESIGN_SPEC_V3.md`, `design_tokens_v3.json`, `rychlik_v3_dark.qss`, `rychlik_v3_light.qss`, `preview_*_ab_style.png`, README, manifest, 34 component PNGs | v3 desktop pack | Starting palette, radii (7 px controls), control height 30, 9 pt base (=12 px), progress 7 px |
| `assets/*.zip` (3 icon/UI packs) | Earlier icon packs | Inspected. Not reused: raster icons at 24 px conflict with the 14-18 px rule. The design uses one original stroke icon set (see section 8). Listed for replacement in the handoff notes |
| `Prompt/*.md` (Dual Share, Share by Link) | Product model + the "functional skeleton only" GUI rule | Confirms final design starts only after workflow gates; Share = Send to device/app + Share by link |
| `Rychlik_FriendSend_Dual_Share_Model_Research_2026.md` | Research | Share dialog wording (two explicit choices, no AUTO), Live Link UX ("Available while this PC is online"), link availability states |
| Real code: `src/rychlik/gui/share_dialog.py`, `friendsend/lib/ui/home_screen.dart`, `handoff_controller.dart` | Current functional skeletons | Reality check for states and labels |

### Conflicts found (documented, not silently resolved)

| # | Conflict | Decision |
|---|---|---|
| C1 | v3 previews use Slovak labels; `design.txt` and the app use English | English is canonical for design and mockups. All strings must be localizable; Slovak is a follow-up translation |
| C2 | v3 dark preview: the selected sidebar item is violet text on a pale pill and is nearly unreadable | Fixed: selected item = 16% accent tint + `accent.text` (#B4ADFF, 7.4:1 on the tint) + 2 px accent edge |
| C3 | `design.txt` names the context action "Send..."; the current product model and this brief say "Share..." | "Share..." opens the two-choice selector. The bulk bar keeps "Send..." because it can only mean Send to Device for several completed files |
| C4 | v3 pack has no Share, Devices, pairing or FriendSend screens | Designed here from scratch in the same language |
| C5 | v3 tokens lack accent hover/pressed, `border.strong`, focus ring, and text-safe status colors for the light theme | Added; all 50 audited pairs pass (see `CONTRAST_AUDIT.md`). v3 files are unmodified |
| C6 | `design.txt` allows "Connections" to show segments; the backend downloads over one resumable connection (segmenting is a known A9 limitation) | Connections tab shows one connection with an honest note; more rows only when segmenting exists |
| C7 | `design.txt` says pairing UI is out of scope; the real app has no desktop pairing UI (`SecurePairingManager` is not wired to the GUI) | Designed (D20-D23); listed as an implementation dependency |
| C9 | The current functional skeleton exposes Hold, Release, Pause, Resume, Retry now, Cancel, Up, Down, priority and Open Folder, Share; the first design pass dropped most queue controls | Restored (section 4a). Dropping backend capabilities in a redesign would be a functional regression |
| C8 | The v3 preview leaves most of the window empty and uses very small type | Dense sample data (14-30 rows) and a fixed 12 px body; wide screens gain columns, not larger text |

## 3. Design hierarchy

```text
Rýchlik:   download management > queue/scheduling > media downloading > history/categories > optional devices/Send
FriendSend: pair (setup, once) > ready > receive > verify > choose app > handoff > discard
```

## 4. Rýchlik Desktop architecture

```text
+------------------------------------------------------------------+
| File  Tasks  Tools  Help                                   (28)   |
| [+ Add] | Start Queue  Stop Queue | Queues  Settings        (54)   |
+-----------+------------------------------------------------------+
| SIDEBAR   | All Downloads                                        |
| (190)     | [Search...] [Status: All v] [Sort: Newest v]  14 tasks|
| Downloads | table: Name Type Size Progress Status Speed Remaining |
| Categories| rows 42 px, progress 6 px, icons 16 px                |
| Tools     | (bulk bar replaces the filter row when rows selected) |
+-----------+------------------------------------------------------+
| * 2 active  3 waiting  v 43.6 MB/s               Limit: Unl. (28)|
+------------------------------------------------------------------+
```

- No primary Send destination. Share lives in the completed-row context menu, bulk bar and Details.
- Devices is a Tools page (list + pairing), not a workflow. Send-related dialogs are 440-560 px modal dialogs.
- Details opens in a separate 660 px dialog (Information / Connections / Log); no permanent inspector.
- The window client area is designed; minimum window 1100 x 640.

## 4a. Queue-control actions (added after the first review)

Source of truth: `DownloadManagerService` (`hold`, `release_hold`, `pause_transfer`, `resume_transfer`, `cancel`, `retry_now`, `set_priority`, `move_before/after`).

- **Hold is not Pause.** Pause stops a running transfer; Hold stops the scheduler from starting an item and never interrupts a running transfer. They have different labels, glyphs (lock vs pause bars) and tooltips. Held is a secondary chip beside the task status.
- **State-aware menus only.** Retry now appears only for RETRY_WAIT, Resume only for PAUSED, Pause only while TRANSFERRING. FAILED has no retry command, so its menu offers Details, Open folder, Remove, Delete file.
- **Order and priority.** Priority > High / Normal / Low; Move up / Move down work only within one priority band (the backend refuses cross-band moves), so they disable at a band edge. The Queues view (D26) shows the three bands with positions; bulk selection deliberately has no reorder.
- **Details** gains Priority, Queue position and Held. **Cancelled** is a real terminal state and now has its own status.
- Mockups: `desktop/06_completed_context.png`, `06b_context_downloading.png`, `06c_context_retrying.png`, `06d_context_held.png`, `06e_priority_submenu.png`, `06f_context_failed.png`, `15_multi_selection.png`, `17_details_information.png`, `34_queues_view.png`, `35_retry_now_hover.png`.
- Flow table: `design/UX_FLOWS.md` section 11. Token change: two new status semantics (held, cancelled) and the action glyph map in `final_design_tokens.json`.

## 5. FriendSend architecture

A state machine with almost no navigation: `PAIRING -> READY -> RECEIVING -> VERIFYING -> RECEIVED -> CHOOSE APP -> HANDOFF`.
No tabs, drawer, inbox, history or settings tree. The only secondary screen is "Trusted computer" (header icon), which
holds Forget. Setup (F01-F04) is visually distinct from everyday use (F05+): different hierarchy, no pairing-code controls once paired.

## 6. Design tokens (source: `design/final_design_tokens.json`)

**Color (dark / light).** surface.background #121217 / #F6F7F9; surface.primary #18181F / #FFFFFF; surface.secondary #1F1F28 / #F8F8FB;
surface.elevated #272832 / #FFFFFF; text.primary #E8E9EF / #282A32; text.secondary #A3A6B4 / #5F6270; text.disabled #686B79 / #8F929D;
border.default #353744 / #D6D8E0; border.strong #4B4E60 / #B7BAC6; accent.primary #6C5DF6 / #6050E2; hover #6152EC / #5142D0; pressed #5344D8 / #4536B8;
accent.text #B4ADFF / #4C3FCB. Status fill (info/success/warning/error): #468BFF #4AC26B #E6B336 #E14E5F / #377DE9 #35A658 #B98207 #CB3F4F.
Status text variants (`*Text`) and `errorSolid` (white-text destructive button: #C4364A / #B02B3B) are defined for AA contrast.

**Spacing:** 2, 4, 6, 8, 12, 16, 20, 24, 32. **Radius** (desktop / mobile): small 4 / 8, medium 7 / 12, large 10 / 20, pill 999 / 999.
**Typography:** desktop caption 11/16, body 12/18, table 12/16 (name 600), heading 15/22 (600), dialog title 14/20 (600);
mobile caption 12/16, body 15/22, title 20/28 (600), heading 28/36 (600). Families: desktop Inter (fallback Noto Sans / system-ui),
mobile Roboto. Mockups were rendered with Noto Sans (desktop) and Roboto (mobile) because Inter is not installed on the design machine.
**Elevation:** dialog `0 12 40 rgba(0,0,0,.55)` + 1 px border; popover `0 6 20`; mobile card 1 px border (dark) / soft shadow (light).
**Motion:** fast 120 ms, normal 200 ms, one easing curve; reduced-motion = instant.

## 7. Component inventory

Desktop: MenuBar, Toolbar (primary Add + icon/text actions), SidebarItem (active = tint + edge + accent text), TaskTable, TaskRow (42 px),
ProgressBar (6 px), StatusBadge (icon + text), SearchField, FilterCombo, BulkToolbar (36 px), ContextMenu, Dialog (elevated, 10 px radius),
DeviceRow, EmptyState (compact), BottomStatusBar, RowActions (hover, state-valid), HeldChip, PriorityMark, QueueBand, Tooltip. Sheets: `desktop/30_components_dark.png`, `31_components_light.png`.

FriendSend: AppHeader (56 dp), StatusHero (orb + title + one sentence), TransferCard, ProgressIndicator (8 dp pill, determinate and indeterminate),
PrimaryButton / SecondaryButton / TertiaryButton / DestructiveButton (52 dp pill), StepList (verify pipeline), ShareTargetTile (84 x 92 dp),
DeviceIdentityCard, ErrorState, BottomSheet (28 dp top radius), AlertDialog. Sheets: `friendsend/11_components_dark.png`, `12_components_light.png`.

### Button hierarchy

| Level | Desktop | FriendSend | Rule |
|---|---|---|---|
| Primary | filled accent | filled accent pill | at most one per dialog/screen |
| Secondary | outlined | outlined pill | |
| Tertiary | text in accent.text | text in accent.text | low-emphasis actions (Retry, Pair a new...) |
| Destructive | filled `errorSolid` only for the confirming action; otherwise red text | same | Forget/Delete confirmation; never the default focus |
| Icon-only | 30 px, tooltip required | 48 dp touch target | only for repeated, recognizable actions |

### Iconography

One original set: 24 px grid, 1.75 px round stroke, drawn for this design (no third-party or brand assets). Desktop renders it at 14-18 px;
FriendSend at 20-24 dp (hero 40+). No emoji, no mixed families, no large decorative icons except the single modest empty-state/hero orbs.
Share-target tiles in the mockups use neutral lettered placeholders: real app icons and labels come from Android at runtime.

## 8. State inventory

Download: Waiting, Downloading, Retrying, Paused, Completed, Failed, Cancelled (plus the Held chip; Resolving/Verifying/Post-processing map to Waiting/Downloading wording). Device: Trusted - Online, Trusted - Offline, Pairing, Identity changed,
Not paired (discovered). Send: Preparing, Converting, Sending, RECEIVED, Failed, Cancelled. Link: CREATING, ONLINE, OFFLINE, EXPIRED, REVOKED, ERROR.
FriendSend: Unpaired, Pairing, Paired, Ready, No network, Receiving, Verifying, Preparing to share, Received, Choosing app, Handed off, Cancelled, Failed,
Integrity failure. Every status pairs a glyph with a text label (status is never color-only).

## 9. Interaction flows

See `design/UX_FLOWS.md` (download, Share by Link, Send to Device, pairing, secure receive, media preparation, receive -> destination app,
cancel, device offline, forget/re-pair).

## 10. Responsive rules

Desktop (client area):

| Width | Layout |
|---|---|
| >= 2200 | all 8 columns + Category + Added; name column absorbs the rest; type size unchanged (OS scaling handles DPI) |
| 1280-2199 | all 8 columns. At 1366 the fixed columns take ~740 px and Name keeps ~370 px: nothing collapses |
| 1100-1279 | Type hidden; Speed and Remaining merge into one cell ("31.2 MB/s . 00:41"); Filename, Status and Progress never disappear |
| < 1100 | sidebar becomes a 48 px icon rail with tooltips; Size moves under the name; toolbar shows icons only for Queues/Settings |

Toolbar keeps icon + text down to 1100. Filter row: search flexes (min 220), Status and Sort keep labels. Bulk bar replaces the filter row.
Height: at 1366 x 768 about 13 rows are visible (design target >= 10). At 2560 x 1440 about 28-30 rows.

FriendSend: 360 x 800, 390 x 844, 412 x 915 dp. Side padding 20 dp; tile grid = floor((width - 40) / 84) columns (min 3): 3 at 360, 4 at 390/412.
The bottom sheet grows with rows up to 62% of the screen height, then scrolls. Primary buttons are full width and pinned to the bottom.

## 11. Dark / light rules

Both themes share layout; only surface, text and border tokens change. Accent stays violet. In light theme text-safe status variants replace the fill
colors for text (fill colors remain for bars/glyphs). Mobile cards use a soft shadow in light, a 1 px border in dark. Follow the system theme by default
with a manual override on desktop (Settings > Appearance). Do not copy desktop QSS colors into Flutter; consume `final_design_tokens.json`.

**Wording rule.** User-facing copy says "pairing code" (FriendSend field: "Paste code from Rýchlik"); "payload" is an internal protocol term.

## 12. Accessibility

- Contrast: 50 audited pairs pass (text >= 4.5:1, graphics >= 3:1); `text.disabled` is exempt and never carries information. See `CONTRAST_AUDIT.md`.
- Status is never color-only (glyph + label on every status; identity/offline chips include an icon and text).
- Focus: 2 px `focus` ring, offset 1-2 px, on every focusable control; tab order follows visual order (toolbar, sidebar, filter row, table, dialogs are focus-trapped).
- Desktop keyboard: Ctrl+N add, Ctrl+A select all visible, Delete remove (with confirmation policy), Esc closes dialog/menu/selection, Enter open, Alt+Enter details, Space pause/resume only when safe, double-click details, right-click menu.
- Mobile: 48 dp minimum touch targets even where the icon is 20 dp; text >= 12 sp; one primary action per screen; buttons keep visible labels.
- Motion respects the reduced-motion setting. Errors use icon + sentence, never color alone.

## 13. Truthfulness constraints (backend reality)

- Wording after a phone receive is RECEIVED ("arrived and was verified"), never delivered. HANDOFF_ACCEPTED only means Android accepted the share.
- Preparing/Converting show the real fraction; no invented ETA. "Converted 1:12 of 2:48" requires duration and processed time (see handoff note 6).
- "The original file will not be changed" is a real invariant (A16).
- The phone is passive: it cannot retry a failed receive, so F14 says "send the file again from Rýchlik".
- FriendSend shows the sanitized received filename (for example holiday.mp4), never `incoming-<uuid>.bin`. Whether the Android system Sharesheet preview chip
  honors the provider's DISPLAY_NAME is a physical-device verification point, not a design decision.
- FriendSend has no recipient/friend/contact concept. The target app owns recipient selection and delivery.

## 14. Mockup index

`design/mockups/00_family_overview.png` shows both apps together. Desktop (42 PNG) and FriendSend (37 PNG, including size variants and component sheets):
listed per screen, with filenames, in `design/SCREEN_INVENTORY.md`. Key files: `desktop/01_main_dark_1366.png`, `02_main_dark_1920.png`, `03_main_dark_2560.png`,
`04_main_light.png`, `05_add_download.png` ... `12_pair_device.png`; `friendsend/01_pairing.png`, `02_ready.png`, `03_receiving.png`, `04_verifying.png`,
`05_received.png`, `06_share_target_picker.png`, `07_share_target_picker_dark.png`, `08_transfer_error.png`, `09_trusted_desktop.png`, `10_forget_confirmation.png`.
Regenerate with `python3 design/prototype/build.py all` (needs a Chromium-based browser at `/usr/bin/brave`, adjust `BROWSER` in `build.py`).

## 15. Design review (side-by-side check)

| Question | Result |
|---|---|
| Still primarily a download manager? | Yes: table-first, dense rows, Send only in menus |
| 10+ rows comfortable? | Yes: about 13 at 1366 x 768, about 28-30 at 2560 x 1440 |
| Send clearly secondary? | Yes: no toolbar/sidebar Send; Devices is under Tools |
| FriendSend understandable immediately? | Yes: one title, one sentence, one primary action per screen |
| Pairing gone from daily use? | Yes: F05 shows only "Ready to receive" and a small trusted-computer card |
| Receive -> choose app obvious? | Yes: the picker opens over the received file |
| Related but not identical? | Yes: shared accent/status/icons, different geometry and density |
| Dark/light coherent? | Yes: same tokens, audited contrast |
| Errors/security understandable? | Yes: plain sentences, one action, identity change offers Forget only |
| Generic Qt/Flutter look? | No: custom tokens, radii, badges and icon set, but implementation must not fall back to default widget styles |

Issues found during review and fixed (including a second review round: queue-control actions dropped from the first pass, and Share/Send dialogs naming a still-downloading file instead of the selected completed one): 14th row overflowing into the status bar, Details dialog height varying by tab, no-contrast accent hover in dark theme,
white text on plain error red, mis-anchored annotation over the FriendSend header, stretched tile grid in the component sheet.

## 16. Known unresolved visual questions

1. Real fonts: mockups use Noto Sans/Roboto stand-ins; re-check widths with Inter.
2. Share-target ordering (recent vs alphabetical vs pinned) and whether to cap the grid at 8 before "More apps...".
3. Real app icons in the picker come from the system; the padding/mask style for non-adaptive icons is untested.
4. Desktop name in the phone's "Trusted computer" card: the pairing data currently carries no display name (record shows null), so the design says "Rýchlik / Your computer".
5. Whether bulk "Send..." should exist in the first implementation (single-file send may ship first).
6. QR pairing is not designed: the current pairing is paste-based; a QR option would need its own design and protocol decision.
7. Slovak strings and the longer-label behavior of toolbar/table columns.
8. The system Sharesheet filename preview (physical device).

## 17. Implementation handoff notes

1. **Desktop pairing GUI does not exist.** `SecurePairingManager`/`DesktopIdentityStore`/`FriendSendTrustStore` are not wired to the GUI and `main.py` does not construct a `DeviceHandoffService`. D13, D20-D23 require that wiring first.
2. **Endpoint refresh.** A17-E1 showed the phone's port changes on every app start. "Trusted - Online" and sending need discovery to refresh the stored endpoint (`with_endpoint`) before connecting.
3. **Identity changed** maps to TLS pin / device-identity mismatch results; there is no persisted "identity changed" flag today, so the state is derived from the last failed check. "Resolve..." opens the forget flow only.
4. **FriendSend states.** Current `AppState` has unpaired/paired/receiving/received/error. New: verifying, preparing-to-share, choosing, handed-off, cancelled, no-network. Discard and Share buttons are currently placeholders (`onPressed: () {}`) and must be wired.
5. **Share-target picker** needs `ShareTargetResolver`, package-visibility `<queries>` for `ACTION_SEND` by MIME type, targeted-package intents, and the permanent More apps... fallback. Today `autoShare` opens the system chooser immediately; the design replaces that with the F10 picker. `More apps...` is a separate row under the grid ("Open Android Sharesheet"), not a seventh tile, and the picker must never replace the system Sharesheet path (Direct Share people/conversations exist only there). Independent of Device Mode transport/security.
6. **Preparing progress:** `DeviceHandoffSnapshot` exposes only a fraction. "Converted 1:12 of 2:48" needs processed time/duration (available inside the A16 preparation progress but not on the snapshot) or must be dropped.
7. **Share by Link extras** (Open in browser, QR, Share via phone) are design placeholders.
8. **Assets to replace:** the 24 px raster icons in `assets/*.zip` and the v3 QSS pixel/`pt` mix; use tokens and the single stroke icon set.
9. **Both themes** need centralized tokens (no hardcoded colors). Consume `design/final_design_tokens.json`.
10. **Queue actions are existing backend capabilities** (hold, release, pause, resume, cancel, retry now, priority, reorder). Any implementation must keep them reachable; the GUI must disable Move up/down at band edges and show Retry now only for RETRY_WAIT. The backend offers no retry for FAILED items.
11. **Snapshot data.** Priority, queue position and Held must come from the existing manager snapshot; if a field is not exposed today the row must be omitted rather than guessed.
12. **Nothing in this phase closes A14-PHYSICAL-ANDROID-SHARE-SMOKE or A15-PHYSICAL-MDNS-DISCOVERY.** They remain OPEN.
