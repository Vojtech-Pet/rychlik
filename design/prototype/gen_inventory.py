# NON-PRODUCTION DESIGN PROTOTYPE -- documentation generator only, not wired into the application.
"""Generates design/SCREEN_INVENTORY.md and verifies every referenced mockup file exists."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "mockups"

# id, purpose, entry, primary, secondary, error, empty, responsive, files
DESKTOP = [
    ("D01", "Main window, mixed download states", "App start / sidebar All", "Double-click a row for details", "Add, Start/Stop Queue, search, filter, sort", "Failed and Retrying rows are inline (D03-style status badge)", "D02", "1366 (all 8 columns), 1920, 2560 (+Category, +Added), 1100 (Type hidden, Speed+Remaining merged)", ["desktop/01_main_dark_1366.png", "desktop/02_main_dark_1920.png", "desktop/03_main_dark_2560.png", "desktop/04c_main_compact_1100.png"]),
    ("D02", "Main window, no downloads", "First run or all removed", "Add download", "Paste link (Ctrl+V), Ctrl+N", "n/a", "This is the empty state", "Same at all widths, compact block, no marketing illustration", ["desktop/13_empty_state.png"]),
    ("D03", "Main window filtered to Downloading", "Sidebar Downloading", "Pause / open details", "Status bar aggregate speed", "Failed items are not shown in this filter", "Empty filter shows compact 'Nothing here' text", "As D01", ["desktop/14_active_downloading.png"]),
    ("D04", "Multi-selection and bulk actions", "Checkbox or Ctrl/Shift click", "Start / Pause / Stop", "Send... (only when every selected item is completed), Delete, Clear selection", "Send disabled with tooltip when a selection includes unfinished items", "n/a", "Bulk bar replaces the filter row; 36 px", ["desktop/15_multi_selection.png"]),
    ("D05", "Add download", "Add button, Ctrl+N, paste link", "Download", "Browse..., Advanced (collapsed)", "Inline invalid-URL message under the field (text, not color only)", "Empty URL keeps Download disabled", "Fixed 560 px dialog", ["desktop/05_add_download.png"]),
    ("D06", "Media URL detected", "URL resolved by the media resolver", "Download (defaults preselected)", "Quality / audio track menus, Browse...", "Resolver failure falls back to D05 with an inline message", "n/a", "Fixed 600 px dialog", ["desktop/16_media_detected.png"]),
    ("D07", "Details - Information", "Double-click, context menu, Alt+Enter", "Close", "Open folder, Pause/Resume", "Failed item shows error reason row", "n/a", "660 px dialog; body height fixed at 322 px across tabs", ["desktop/17_details_information.png"]),
    ("D08", "Details - Connections", "Details dialog tab", "Close", "n/a", "n/a", "Single resumable connection today; more rows when segmented downloads exist", "As D07", ["desktop/18_details_connections.png"]),
    ("D09", "Details - Log", "Details dialog tab", "Close", "n/a", "Errors appear as red lines with a text prefix", "Empty log shows 'No events yet'", "Monospace, 11 px", ["desktop/19_details_log.png"]),
    ("D10", "Completed item context menu", "Right-click a completed row", "Share...", "Open, Open folder, Details, Copy source URL, Remove, Delete file...", "Menu content depends on state (Pause/Resume/Retry replace Share for unfinished items)", "n/a", "Menu flips upward near the bottom edge", ["desktop/06_completed_context.png"]),
    ("D11", "Share action selector", "Share...", "Choose Send to device or Share by link", "Cancel", "n/a", "n/a", "480 px dialog; explicit choice, no AUTO mode", ["desktop/07_share_selector.png"]),
    ("D12", "Share by Link (Online)", "Share by link", "Copy link", "Open in browser, Show QR code, Share via phone, Stop sharing", "Link states CREATING / ONLINE / OFFLINE / EXPIRED / REVOKED / ERROR use the same chip component", "n/a", "560 px dialog", ["desktop/20_share_by_link.png", "desktop/32_share_by_link_light.png"]),
    ("D13", "Send to Device - device selection", "Send to device", "Send", "Pair a new FriendSend..., Cancel", "See D14 and D19", "No paired devices: list replaced by 'Pair FriendSend...' call to action", "500 px dialog", ["desktop/08_send_device.png", "desktop/33_send_device_light.png"]),
    ("D14", "Send to Device - device offline", "Offline device selected", "Retry", "Cancel", "Warning banner names the fix; Send disabled", "n/a", "As D13", ["desktop/21_device_offline.png"]),
    ("D15", "Preparing compatible copy", "Send, media needs a compatible copy (REMUX)", "Cancel", "n/a", "PREPARED_MEDIA_INVALID / TRANSCODER_UNAVAILABLE map to the D19 pattern", "n/a", "480 px dialog", ["desktop/09_preparing.png"]),
    ("D16", "Transcoding", "Send, media needs conversion", "Cancel", "n/a", "HDR_TRANSCODE_UNSUPPORTED and NO_COMPATIBLE_PROFILE use D19 with a specific sentence", "n/a", "As D15", ["desktop/10_transcoding.png"]),
    ("D17", "Sending", "Compatible file ready", "Cancel", "n/a", "Connection loss goes to D19", "n/a", "As D15", ["desktop/22_sending.png"]),
    ("D18", "Device RECEIVED", "Phone verified the file", "Done", "Send again", "n/a", "n/a", "Wording never claims delivery to a person", ["desktop/23_received.png"]),
    ("D19", "Send failure", "Any failed handoff", "Try again", "Close, Details (technical code)", "This is the error state", "n/a", "500 px dialog", ["desktop/24_send_failure.png"]),
    ("D20", "Devices", "Sidebar Tools > Devices", "Pair FriendSend...", "Details, Resolve..., Pair...", "Identity changed row", "No devices: compact empty state with Pair FriendSend...", "Table columns collapse like D01 below 1280", ["desktop/11_devices.png"]),
    ("D21", "Pair FriendSend", "Pair FriendSend...", "Copy pairing code", "Cancel", "Expired code: banner + 'Create a new code'", "n/a", "560 px dialog; success variant D21b", ["desktop/12_pair_device.png", "desktop/29_pair_success.png"]),
    ("D22", "Trusted device details / identity changed", "Details or Resolve...", "Close / Forget device", "Copy device ID", "Identity changed dialog never offers to trust the new identity", "n/a", "500 px / 480 px dialogs", ["desktop/25_trusted_device.png", "desktop/26_identity_changed.png"]),
    ("D23", "Forget device confirmation", "Forget device...", "Forget device (destructive)", "Cancel", "n/a", "n/a", "440 px dialog", ["desktop/27_forget_confirmation.png"]),
    ("D24", "Settings", "Toolbar Settings, sidebar Tools", "Close", "Appearance, Downloads, Queue & speed, Devices & sharing", "Invalid values are flagged inline", "n/a", "720 x 400 dialog", ["desktop/28_settings.png"]),
    ("D25", "Light theme main view", "Settings > Appearance", "n/a", "n/a", "n/a", "n/a", "1920 and 1366 light variants", ["desktop/04_main_light.png", "desktop/04b_main_light_1366.png"]),
]
FRIEND = [
    ("F01", "First launch / unpaired", "No trusted desktop stored", "Pair", "Paste", "Inline error under the field", "n/a", "360/390/412", ["friendsend/01_pairing.png", "friendsend/01b_pairing_light.png"]),
    ("F02", "Pairing payload entered / invalid", "Text in the field", "Pair", "Paste", "'This does not look like a pairing code.' with icon + text", "Empty: Pair disabled", "360/390/412", ["friendsend/01c_pairing_filled.png", "friendsend/01d_pairing_error.png"]),
    ("F03", "Pairing in progress", "Pair tapped", "none (short, automatic)", "n/a", "Failure returns to F01 with a message", "n/a", "360/390/412", ["friendsend/01e_pairing_progress.png"]),
    ("F04", "Pairing success", "Pairing confirmed", "Done", "n/a", "n/a", "n/a", "360/390/412", ["friendsend/01f_pairing_success.png"]),
    ("F05", "Ready to receive", "Trusted desktop exists (every cold start)", "none (waiting)", "Trusted computer card, header desktop icon", "F16", "n/a", "360/390/412", ["friendsend/02_ready.png", "friendsend/02b_ready_light.png"]),
    ("F06", "Receiving file", "Desktop starts a send", "Cancel", "n/a", "F13/F14", "n/a", "360/390/412", ["friendsend/03_receiving.png", "friendsend/03b_receiving_light.png", "friendsend/sizes/receiving_360x800.png", "friendsend/sizes/receiving_412x915.png"]),
    ("F07", "Verifying", "All bytes received", "none", "n/a", "F15", "n/a", "360/390/412", ["friendsend/04_verifying.png"]),
    ("F08", "Preparing received file / ready transition", "Verification passed", "none", "n/a", "Failure to create the share URI shows the F14 pattern", "n/a", "360/390/412", ["friendsend/04b_preparing_share.png"]),
    ("F09", "Received", "Verified file exists", "Choose app", "Discard", "n/a", "n/a", "360/390/412", ["friendsend/05_received.png"]),
    ("F10", "Choose destination app (dynamic picker)", "Opens automatically over F09; reopened by Choose app", "Tap an app tile", "More apps..., Discard", "F19", "F19", "3 columns at 360, 4 at 390/412; scrolls beyond 3 rows", ["friendsend/06_share_target_picker.png", "friendsend/07_share_target_picker_dark.png", "friendsend/07b_picker_few_apps.png", "friendsend/07c_picker_many_apps.png", "friendsend/sizes/picker_360x800.png", "friendsend/sizes/picker_412x915.png", "friendsend/sizes/picker_light_360x800.png", "friendsend/sizes/picker_light_412x915.png"]),
    ("F11", "System More apps fallback", "More apps...", "Choose a system share target", "System UI dismiss", "n/a", "n/a", "System-owned; schematic only", ["friendsend/07e_system_more_apps.png"]),
    ("F12", "Handoff accepted", "App returns from a targeted ACTION_SEND", "Done", "Send with another app", "n/a", "n/a", "360/390/412", ["friendsend/07f_handoff_accepted.png"]),
    ("F13", "Cancelled", "User or desktop cancelled", "Done", "n/a", "n/a", "n/a", "360/390/412", ["friendsend/08b_cancelled.png"]),
    ("F14", "Transfer failed", "Connection interrupted", "Done", "Details", "This is the error state", "n/a", "360/390/412", ["friendsend/08_transfer_error.png"]),
    ("F15", "Integrity failure", "Size or hash mismatch", "Done", "n/a", "Never offers to share the file", "n/a", "360/390/412", ["friendsend/08c_integrity_failure.png"]),
    ("F16", "No Wi-Fi / connection state", "No usable network while Ready", "Open Wi-Fi settings", "n/a", "n/a", "n/a", "360/390/412", ["friendsend/02c_no_wifi.png"]),
    ("F17", "Trusted desktop info", "Header desktop icon or Trusted computer card", "Back", "Forget this computer", "n/a", "n/a", "360/390/412", ["friendsend/09_trusted_desktop.png", "friendsend/10b_trusted_desktop_light.png"]),
    ("F18", "Forget desktop confirmation", "Forget this computer", "Forget (destructive)", "Cancel", "n/a", "n/a", "Alert dialog, 24 dp side margins", ["friendsend/10_forget_confirmation.png"]),
    ("F19", "No compatible share target", "Picker resolves zero apps", "More apps...", "Try again, Discard", "This is the error/empty state", "This is the empty state", "360/390/412", ["friendsend/07d_no_compatible_apps.png"]),
    ("F20", "Light theme", "System or manual", "n/a", "n/a", "n/a", "n/a", "Light variants of F01, F05, F06, F10, F17 and components", ["friendsend/01b_pairing_light.png", "friendsend/02b_ready_light.png", "friendsend/03b_receiving_light.png", "friendsend/06_share_target_picker.png", "friendsend/10b_trusted_desktop_light.png", "friendsend/12_components_light.png"]),
    ("F21", "Dark theme", "System or manual", "n/a", "n/a", "n/a", "n/a", "All base FriendSend mockups are dark unless suffixed light", ["friendsend/07_share_target_picker_dark.png", "friendsend/11_components_dark.png"]),
]

missing = [f for row in DESKTOP + FRIEND for f in row[-1] if not (M / f).exists()]
if missing:
    raise SystemExit(f"missing mockups referenced by the inventory: {missing}")

hdr = "| ID | Purpose | Entry condition | Primary action | Secondary actions | Error state | Empty state | Responsive variants | Mockup file(s) |\n|---|---|---|---|---|---|---|---|---|\n"
fmt = lambda rows: "".join(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | " + "<br>".join(f"`{f}`" for f in r[8]) + " |\n" for r in rows)
out = ("# Screen inventory\n\nGenerated by `design/prototype/gen_inventory.py`; every mockup filename below is verified to exist.\n"
       "All screens are DESIGN ONLY: none of this is implemented in Qt or Flutter. The share-target picker (F10, F11, F12, F19) is\n"
       "**DESIGN APPROVED DIRECTION - NOT IMPLEMENTED** and needs `ShareTargetResolver` and Android package-visibility `<queries>` first.\n\n"
       "Additional mockups not tied to one ID: `mockups/00_family_overview.png` (both apps side by side), `desktop/30_components_dark.png`, "
       "`desktop/31_components_light.png`, `friendsend/11_components_dark.png`, `friendsend/12_components_light.png`.\n\n"
       "## Rýchlik Desktop\n\n" + hdr + fmt(DESKTOP) + "\n## FriendSend Android\n\n" + hdr + fmt(FRIEND))
(ROOT / "SCREEN_INVENTORY.md").write_text(out)
print("wrote SCREEN_INVENTORY.md", len(DESKTOP), "desktop +", len(FRIEND), "FriendSend screens; all mockup files exist")
