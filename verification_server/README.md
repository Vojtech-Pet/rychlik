# FriendSend purchase verification server

Backend for `ServerPurchaseVerifier` (`friendsend/lib/monetization/purchase_verifier.dart`). Verifies a
`friendsend_lifetime_unlock` purchase token against the real Google Play Developer API (`purchases.products.get`),
so a client-reported "purchased" callback is never, on its own, trusted as a permanent unlock.

## What it does

`POST /verify` `{"productId": "...", "purchaseToken": "..."}` with header `X-Verify-Token: <shared secret>` →
`{"ok": true, "result": "verified" | "rejected" | "temporary_failure"}`.

- `purchaseState == 0` (purchased) → `verified`.
- `purchaseState == 1` (canceled/refunded/revoked) → `rejected`.
- `purchaseState == 2` (pending) → `temporary_failure`.
- A `404` from the Play API (no such purchase for this token) → `rejected`.
- Any other error (auth, quota, network, an unrecognised response shape) → `temporary_failure` — **never** guessed
  as `verified` or `rejected`, since that would be our bug, not evidence about the purchase.

Auth is a shared bearer token (`X-Verify-Token`, constant-time compared), the same loopback-bridge pattern as
`src/rychlik/bridge/browser_bridge.py`; plus a request-size cap and a rate limit. This is a starting point, not a
finished production posture — see "Not done" below.

## Setup (once you have Play Console access)

1. Play Console → **Setup → API access** → link/create a Google Cloud project, create a service account with
   **View app information** + **View financial data / Manage orders and subscriptions** permission for this app,
   and enable the **Android Publisher API** in that Cloud project.
2. Download the service account's JSON key.
3. Run:
   ```
   pip install -r verification_server/requirements.txt
   FRIENDSEND_PACKAGE_NAME=app.friendsend.friendsend \
   FRIENDSEND_SERVICE_ACCOUNT_JSON=/path/to/key.json \
   FRIENDSEND_VERIFY_TOKEN=<a long random secret> \
   python verification_server/server.py
   ```
4. Update `ServerPurchaseVerifier` (currently a truthful `temporaryFailure`-always placeholder) to `POST` to this
   server with the same token, and parse its JSON `result` into `VerificationResult`.

## Tests

`python -m pytest verification_server/tests -q` — all real HTTP against the real server, with a fake Android
Publisher client (no network, no real Google credentials needed). 19 tests: purchase-state mapping, HTTP error
classification (404 vs auth/quota/network), auth/rate-limit/body-size on the HTTP layer, port-in-use handling.

## Not done

- `ServerPurchaseVerifier` (the Dart client) still needs to be pointed at this server once it is deployed
  somewhere reachable from a phone (not `127.0.0.1`) with TLS.
- No persistence/audit log of verification results (useful for support/fraud review later).
- No `google-api-python-client`/`google-auth` real-credential test was run here — only the fake-client unit tests.
  A real end-to-end check needs the Play Console product + service account from the plan's step 1–3.
