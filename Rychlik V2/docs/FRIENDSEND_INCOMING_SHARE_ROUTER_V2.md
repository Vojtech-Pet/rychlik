# FriendSend Incoming Share Router v2 — Save to phone

Builds on v1/v1.2 (`docs/FRIENDSEND_INCOMING_SHARE_ROUTER_V1.md`); nothing in v1 changed its behaviour.

## What v2 adds

Once the video behind a shared link has been fetched ("Send video to" state), the screen offers **Save to phone**:

- Copies the video into `Movies/FriendSend/` through MediaStore, so it appears in the Gallery / Files. No storage permission is needed on Android 10+ (API 29); on older Android the button reports "Saving needs Android 10 or newer." (minSdk is 24).
- Only files inside FriendSend's own private video cache can be saved (canonical-path guard in `MainActivity.saveVideoToGallery`); the name is sanitised like the share display name; the copy uses `IS_PENDING` so a half-copied file is never visible, and a failed copy deletes its row.
- One save per download: the button disables after "Saved to Movies/FriendSend"; failures are explained ("Couldn’t save the video.") and can be retried. Sending to an app still works before and after saving.
- The private cache copy is still purged after 1 hour; the saved copy is the user's and is never touched by FriendSend again.

## Evidence

- Flutter: 130 passed (new: Save-to-phone flow with failed / unsupported / saved and no duplicate save, `saveToPhone` channel mapping).
- Emulator (real release APK, Android 16 image): link -> "clip.mp4 · 42 KB" -> Save to phone -> "Saved to Movies/FriendSend"; `/sdcard/Movies/FriendSend/clip.mp4` has SHA-256 `9ee19d13…020cec`, identical to the served file (`artifacts/incoming_share/v2_saved.png`).
- Not verified: the S24 (Samsung Gallery indexing), Android 10–12 devices, very large videos.
