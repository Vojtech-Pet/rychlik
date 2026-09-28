# UX flows

DESIGN ONLY. Screen IDs refer to `design/SCREEN_INVENTORY.md`. States named in CAPITALS are real backend states or
error codes that exist today (Prompts A13-A16); flows never invent a backend guarantee.

## 1. Download flow (Rýchlik)

```text
Add (Ctrl+N / paste link)  D05
   |-- media URL? --> D06 (defaults preselected, one click)
   v
Row appears in the table: Waiting -> Downloading -> Completed        D01
   |-- Paused / Retrying / Failed are inline badges (icon + text)
   |-- double-click -> Details (Information / Connections / Log)       D07-D09
   v
Completed: right-click -> Share...                                    D10
```

Send is never a navigation destination. The only Share entry points are the completed-row context menu, the
bulk bar (when every selected item is completed) and the Details dialog.

## 2. Completed -> Share by Link

```text
Share...  D10 -> Share by link  D11 -> D12 (state CREATING -> ONLINE)
D12 actions: Copy link | Open in browser | Show QR code | Share via phone | Stop sharing
Always visible: "Available while this PC is online."
Recipient states rendered with the same chip component: ONLINE / OFFLINE / EXPIRED / REVOKED / ERROR
```

Dependency note: today's dialog offers Share by link only; Open in browser, QR and Share via phone are design
placeholders that need their own backend work.

## 3. Completed -> Send to Device

```text
Share... -> Send to device  D11 -> D13 pick a trusted device
   |-- selected device Offline -> D14 (banner, Send disabled, Retry)
   v
Send
   |-- capability check (authenticated, pinned) -- silent
   |-- PASSTHROUGH: skip straight to Sending                    D17
   |-- REMUX: "Preparing compatible copy..."                   D15   (original never changed)
   |-- TRANSCODE_*: "Converting video for <device>..."         D16
   |-- UNSUPPORTED / HDR_TRANSCODE_UNSUPPORTED / NO_COMPATIBLE_PROFILE -> D19 (specific sentence, no bytes sent)
   v
Sending  D17 -> RECEIVED  D18 ("The file arrived and was verified.")
   |-- CANCELLED (any stage) -> dialog closes, original untouched
   |-- FAILED -> D19 (Try again / Close / Details)
```

Wording rules: RECEIVED never says "delivered" or "sent to <person>"; the phone owns the last step.
The stepper (Prepare / Send / Done) is shown for every send; PASSTHROUGH marks Prepare as done immediately.

## 4. Pair FriendSend

```text
Tools > Devices (D20) -> Pair FriendSend...  D21
   1 Open FriendSend on the phone (same Wi-Fi)  2 Copy pairing code  3 Paste it in FriendSend, tap Pair
   Desktop shows: "Waiting for FriendSend..." + "Expires in 4:32" (5-minute one-time code)
   |-- success -> "Paired with <device>" (D21 success) -> device appears as Trusted - Online in D20
   |-- expiry  -> banner "This code expired" + Create a new code
Phone:  F01 -> F02 (code pasted) -> F03 (connecting) -> F04 (paired) -> F05 (Ready to receive)
```

A discovered but unpaired device (mDNS) appears in D20 as "Not paired" with a Pair... button. Discovery is never trust.

## 5. Secure receive (FriendSend)

```text
F05 Ready  --desktop sends-->  F06 Receiving (progress, Cancel)
   --all bytes-->  F07 Verifying  --size+SHA-256 OK-->  F08 Getting ready to share  -->  F09 Received + F10 picker
   |-- mismatch -> F15 "File verification failed" (file discarded, never shareable)
   |-- connection lost -> F14 (partial deleted)     |-- cancelled -> F13
```

F07 and F08 are short transitional states; implementations must hold each for a minimum readable time (about 400 ms)
or skip them, never flash them. Nothing is offered for sharing before verification succeeds.

## 6. Media preparation

See flow 3. The user sees only: Preparing / Converting, a progress bar, "The original file will not be changed."
and Cancel. No codec, CRF, container or FFmpeg wording ever appears in the default UI.

## 7. Receive -> destination app (Direct Share Target Picker, planned)

```text
F09 Received (auto-opens) F10 "Where do you want to send it?"
   tiles = installed apps that resolve ACTION_SEND for the received MIME type  (dynamic, from Android at runtime)
   tap tile  -> targeted ACTION_SEND with the FileProvider content:// URI -> the target app shows ITS OWN recipient chooser
   More apps... -> normal Android Sharesheet (F11)
   zero tiles -> F19 "No compatible apps found"
   return from target app -> F12 "Handed off to <app>" (HANDOFF_ACCEPTED only; delivery is unknowable)
   Done / Discard -> temporary file removed
```

STATUS: DESIGN APPROVED DIRECTION, NOT IMPLEMENTED. FriendSend never sees a contact, friend list or recipient.

## 8. Cancel

| Where | Control | Result |
|---|---|---|
| Desktop preparing/converting/sending | Cancel | FFmpeg terminated, temp deleted, CANCELLED, original untouched |
| Phone receiving | Cancel | partial deleted; desktop shows CANCELLED; phone shows F13 |
| Phone verifying/preparing | none | short states; no cancel is offered |

## 9. Device offline

```text
Desktop D20/D13: Trusted - Offline chip (never "Not paired"), last-seen time, Send disabled + banner (D14)
Cause on the phone: app closed or no Wi-Fi (F16). Reopening FriendSend restores Trusted - Online without pairing.
Send attempt while offline fails truthfully with a connection message; nothing is uploaded anywhere as a fallback.
```

## 10. Forget / re-pair

```text
Desktop: D20 -> Details (D22) -> Forget device... -> D23 -> device removed; discovery may still list it as Not paired
Phone:   F17 Trusted computer -> Forget this computer -> F18 -> back to F01
Re-pair: run flow 4 again. Old credentials never resurrect old trust.
Identity changed (TLS pin mismatch): D22 (identity-changed variant) offers Forget device only. There is no "trust the new identity" action.
```
