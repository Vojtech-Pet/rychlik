# FriendSend Incoming Share Router v1 — result

Scope: `ACTION_SEND` + `text/plain` only. Purely local Android flow, independent of pairing with Rýchlik:

```
Facebook / Chrome / any app -> Android Share -> FriendSend -> Choose app -> Messenger / Messages / ... (targeted ACTION_SEND)
                                                          \-> More apps… -> Android Sharesheet (FriendSend excluded)
```

## Behaviour

- FriendSend declares an `ACTION_SEND text/plain` intent filter; its label is now "FriendSend" (was lowercase `friendsend`).
- Kotlin keeps the shared text until Dart pulls it once (`takeIncomingText`), so a cold start cannot lose it and a live share (`onNewIntent`) cannot be duplicated. The text is passed **untouched** (no trim/rewrite); blank text and text over 100 000 characters are ignored (`IncomingShare.extractText`).
- No pairing screen is shown while a share is pending; without a share the normal screens are unchanged.
- Target apps come from the existing `ShareTargetResolver` (system query for `SEND text/plain`; no hard-coded providers; FriendSend excluded). Manifest `<queries>` gained the `SEND text/plain` intent only.
- Picking an app sends the exact original text with a targeted `ACTION_SEND` (`EXTRA_TEXT`, `setComponent`). "More apps…" opens `createChooser` with `EXTRA_EXCLUDE_COMPONENTS` = FriendSend (no loop).
- Dismissing the picker keeps the text and leaves the screen (no loop); a vanished app shows a message and reopens the picker; a platform error keeps the text. A newer share replaces an unfinished one. Close returns to the normal screen.

## Evidence (kept separate)

| Class | Evidence |
|-------|----------|
| Unit (Kotlin JVM) | `IncomingShareTest` 5 tests (exact text, no trim, MIME with parameters, other actions/MIME ignored, blank/huge ignored); all 17 Kotlin unit tests pass |
| Widget/unit (Flutter) | `test/incoming_share_test.dart` 12 tests: bridge argument/result mapping, cold-start take-once, live announce→pull, no pairing screen, exact text to the picked target, More apps, dismissal keeps text, vanished/failed target, Close, replacement, verbatim non-link text; full Flutter suite 118 passed |
| Emulator (real APK, real Android system UI) | see below |
| Physical phone (Samsung S24, real Facebook) | **not run yet** |

### Emulator run (Medium_Phone AVD, release APK, `artifacts/incoming_share/`)

1. `am start -a SEND -t text/plain --es EXTRA_TEXT <Facebook-style URL>` → the **system Sharesheet lists FriendSend** (`share_chooser.png`).
2. FriendSend opens on "Share link" with the exact URL, no pairing screen (`fs_incoming.png`).
3. Choose app lists the real installed text apps (Bluetooth, Chat, Chrome, Drive, Gmail, Messages, Quick Share, a test sink …), not FriendSend (`fs_picker2.png`).
4. Picking the test sink (`test.textsink`, handles `text/plain`): logcat `SINK_TEXT text=[https://www.facebook.com/share/v/1AbCdEf/?mibextid=abc] length=54 component=test.textsink/…` — the exact original string (`sink_result.png`).
5. A second share (`https://youtu.be/xyz?si=A&t=10 pozri toto`, ampersand and spaces) arriving while FriendSend was in the background is shown correctly; More apps… opens the Android Sharesheet ("Sharing text", that string) **without FriendSend** (`fs_more_apps.png`).
6. Picking the real Messages app opens its share flow ("Select recipients") via targeted ACTION_SEND (`fs_messages.png`). Whether the text is visible there was not inspected.

Not verified: the real Facebook app on a physical phone (the requested acceptance path), Messenger/WhatsApp specifically, `image/*`/`video/*`/`ACTION_SEND_MULTIPLE` (later versions), and the "before" state (an older APK without the filter was not kept, so no before/after pair from this run).
