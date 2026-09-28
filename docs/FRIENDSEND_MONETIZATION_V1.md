# FriendSend Monetization v1 — 5 free sends, then a €1.99 lifetime unlock

No ads, no subscription. Receiving files is always free; only **sending** is gated.

## Model

```
EntitlementService (lib/monetization/entitlement_service.dart)
├── TRIAL:  remainingTrialSends 0..5 (persisted as "sends used", not "remaining")
└── FULL:   unlocked = true (unlimited)
```

- Persistent: a small JSON file in the app's support directory (`entitlement.json`), survives app and phone restarts.
- Central, not scattered: `status()`, `recordSuccessfulTargetedSend()`, `unlock()` are the only ways to read or change it; nothing else touches the counter.
- `AlwaysUnlockedEntitlement` (the default when `HomeScreen` is built without one, e.g. every pre-existing test) never blocks and never counts — v1 changes no existing behaviour unless wired in.

## What counts as a "send" (and what doesn't)

Counted **only** when Android really accepted a *targeted* `ACTION_SEND` (`TargetShareResult.opened`) — i.e. `HandoffController.shareToTarget` (device-received files) and `HomeScreen._pickTextTarget` (Incoming Share Router: text, link or fetched video). Not counted:
failed sends, cancelled/dismissed pickers, a vanished target, transcode/video-fetch failure, or the **system Sharesheet** ("More apps…") — FriendSend cannot confirm the user picked anything there.

## Gating

A single `_requireEntitlement()` check in `HomeScreen` gates every send entry point (targeted tile *and* system Sharesheet, both flows): if blocked, no bridge call is made and `UnlockScreen` is shown instead — the underlying state is untouched (the received-file flow never even calls `controller.chooseApp()`, so no picker opens). One `EntitlementService` instance (constructed once in `main.dart`) is shared by both the device-received flow and the Incoming Share Router, so neither entry point can be used to bypass the other's trial usage.

## Billing

`BillingAdapter` (interface) is separate from `EntitlementService` so entitlement logic is tested with a fake, never real payment code. `GooglePlayBillingAdapter` wraps the `in_app_purchase` plugin (Google Play Billing), product id `friendsend_lifetime_unlock`. **A real purchase cannot succeed yet**: that product must be created in Play Console (account access this session doesn't have) as a one-time managed product. Until then `lifetimeUnlockPrice()` returns null and `purchaseLifetimeUnlock()` truthfully returns `failed` — verified on the emulator (Play Store not signed in): "Couldn't complete the purchase" is shown, entitlement stays locked, never a fake success.

## Evidence

- `test/entitlement_service_test.dart` (9): fresh install = 5 remaining, exact decrement, never negative past 0, unlock is unlimited and stops counting, **persists across a fresh `EntitlementService` instance** (app/phone restart), unreadable file = fresh trial not a crash, change stream, in-memory cache, `AlwaysUnlockedEntitlement`.
- `test/monetization_gate_test.dart` (16, both flows against the real `HomeScreen`/`HandoffController`): full acceptance list — remaining count shown, success decrements exactly once, failure/cancel don't decrement, exhausted trial blocks with no handoff (`controller` state proven unchanged), Not now returns to the underlying screen, successful purchase unlocks and unblocks immediately, cancelled/failed purchase leaves it locked and shows a reason, unlocked = unlimited + no badge, **system Sharesheet never decrements**, and one shared entitlement source blocks both entry points.
- Full Flutter suite: 155 passed. `flutter analyze`: no issues. Kotlin unit tests unaffected (no Kotlin change in this phase; Play Billing's native layer comes from the `in_app_purchase_android` plugin).
- Emulator, real release APK (109.7 MB, up from 109.1 — `in_app_purchase`): 5 real targeted sends to an installed test app succeeded; the 6th share showed **"0 free sends remaining"** before any attempt, and tapping the target opened `UnlockScreen` ("You've used your 5 free sends") instead of sending; tapping **Unlock FriendSend** truthfully failed (no Play Console product yet) and stayed locked; **after a full app restart the trial was still exhausted** (`artifacts/monetization/trial_persists_after_restart.png`, `unlock_screen.png`).

## Not done in v1

- The Play Console product (`friendsend_lifetime_unlock`) does not exist yet — no real purchase can complete until it's created there.
- No server-side purchase/receipt verification (a client-reported `purchased` status is trusted as-is); acceptable for a one-time low-price unlock in v1, worth revisiting if fraud shows up.
- A restore-purchases entry point for a reinstall (Play Billing's `restorePurchases`/purchase history) is not wired in — a user who reinstalls today has to re-earn or re-buy.
- Physical-device run (only the emulator was used here).
