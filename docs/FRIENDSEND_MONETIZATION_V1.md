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


## Physical S24 (user-confirmed, 2026-09-28)

Trial exhaustion → Unlock screen: **confirmed**. After using up the free sends on the physical phone, both Choose app and More apps showed the Unlock screen ("napíše unlock") instead of sending, matching the emulator result. Real purchase was not attempted (no Play Console product yet, as expected). Individual acceptance points (exact-URL delivery to Messenger/WhatsApp, decrement on success only, More apps not decrementing) were not itemised by the user beyond this; treat the gate itself as physically confirmed, the itemised sub-checks as not separately re-verified on hardware.

## Billing hardening (B1–B3, after v1)

v1's client JSON (`{"unlocked": bool}`) was fine for functional trial testing but was **not** a real purchase protection: a billing "purchased" callback alone made it permanent, with no way back for a reinstall. Hardened before any real purchase can happen.

### B1 — purchase state model

`EntitlementState`: `trial → purchasePending → fullVerified | fullUnverified → restoreRequired`. A billing "purchased" callback now only starts verification (`applyPurchase`); it is never itself a permanent unlock. `fullUnverified` grants the same access as `fullVerified` (bounded grace period, default 3 days, configurable/injectable clock for tests) while verification keeps retrying; past the grace period it lapses to `restoreRequired`, never silently staying "unlocked" forever unverified. `EntitlementStatus.canSend`/`.unlocked` are unchanged computed properties, so none of the v1 gating code (`_requireEntitlement`, the picker, the Incoming Share Router) had to change.

### B2 — restore / reinstall recovery

`BillingAdapter.restorePurchases()` (Google Play Billing's `restorePurchases()` + purchase-stream replay) returns whatever the account already owns. `EntitlementSource.restoreFrom(record, verifier: ...)` re-runs the same verify-and-settle path as a fresh purchase (`applyPurchase` under the hood) — reusing all its idempotency/rejection handling. `main.dart` calls it once, best-effort, on every app start (never blocks startup, ignores errors) so a reinstall with an owned purchase self-heals without a tap; `UnlockScreen` also has an explicit **Restore purchases** button for a manual retry.

### B3 — server verification boundary

`PurchaseVerifier.verify(productId, purchaseToken) → verified | rejected | temporaryFailure`, kept separate from `BillingAdapter` so entitlement logic is tested with a fake verifier, never real payment code. `ServerPurchaseVerifier` (the production implementation used today, since no backend exists yet) **truthfully always returns `temporaryFailure`** — a real purchase gets grace-period access, never a silently-fabricated permanent unlock. Replace its body with a real backend call before relying on purchases in production. The product id (`friendsend_lifetime_unlock`) lives in exactly one place (`lib/monetization/product.dart`).

### Evidence

- `test/billing_hardening_test.dart` (16): the full B1–B3 acceptance list — trial exhaustion, verified purchase persists across restart, rejected purchase never unlocks (and leaves the pre-existing trial count untouched, not reset), temporary failure grants grace only, an expired grace period lapses to `restoreRequired` with no fake unlock, wrong product id is a no-op, duplicate/replayed purchase callbacks are idempotent (verifier not re-called), a different token is re-verified, fresh-install-with-owned-purchase restores to FULL, restore-with-nothing-owned stays trial, `retryPendingVerification` promotes a grace-period purchase once verified (or no-ops with nothing pending), `ServerPurchaseVerifier` never fabricates `verified`, one shared `EntitlementService` instance covers both FriendSend entry points.
- `test/monetization_gate_test.dart`: existing 17 device/incoming-flow tests unchanged and still passing (gating logic untouched); added a Restore-purchases UI test (owned → unlocks; not owned → explains, stays blocked).
- `test/entitlement_service_test.dart`: updated for the new API (`applyPurchase` in place of the removed `unlock()`); all 9 still pass.
- Full Flutter suite: 172 passed. `flutter analyze`: no issues.
- Emulator, real release APK (109.9 MB): app starts normally with the new best-effort auto-restore call on launch (no crash, no block); after exhausting the 5 free sends, **Restore purchases** truthfully reports "No previous purchase was found for this account." (no Play Store on this emulator) instead of unlocking (`artifacts/monetization/hardening_unlock_with_restore.png`, `hardening_restore_no_purchase.png`).

### Not done

- `ServerPurchaseVerifier` has no backend yet (by design, documented above) -- a real purchase today would sit in `fullUnverified` grace and then lapse to `restoreRequired`. Build the backend + swap it in before Play Console goes live.
- No physical-device run of the hardened flow (only the emulator was used here); the S24 physical confirmation recorded earlier was against the pre-hardening v1 build.
- The Play Console product still does not exist, so no real end-to-end purchase/restore was exercised against the actual store, only against the local `GooglePlayBillingAdapter` code paths that run when it's unavailable.
