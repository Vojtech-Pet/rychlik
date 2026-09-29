# FriendSend Android — Protocol v1 Conformance (Prompt A14)

What `friendsend/` (the real Dart/Flutter receiver) implements against
[docs/FRIENDSEND_PROTOCOL_V1.md](FRIENDSEND_PROTOCOL_V1.md), and any
deviations. Verified both by unit/integration tests against the real
`dart:io` server (`friendsend/test/receiver_server_test.dart`) and by the
real cross-language E2E suite
(`tests/test_friendsend_android_cross_language_e2e.py`) driving the exact
same receiver code as a subprocess against the real desktop
`DeviceHandoffService`.

## Implemented endpoints

| Endpoint | Implemented | Notes |
|---|---|---|
| `GET /hello` | Yes | Reports protocol version, capabilities, platform, device_id, display_name. |
| `POST /handoff/offer` | Yes | Full preflight: protocol version, auth token, capability (test-configurable), size, MIME -- all before any payload byte. |
| `POST /handoff/stream` | Yes | Bounded chunked read, incremental SHA-256, size + hash verification. |
| pairing-completion callback | **Not implemented** | Protocol v1 does not mandate a wire endpoint for this step (see `FRIENDSEND_PROTOCOL_V1.md`, "Pairing semantics"); deferred to A15. |

## Required fields (`/handoff/offer`)

All of `handoff_id`, `display_name`, `mime_type`, `size_bytes`, `sha256`
are required and validated before acceptance
(`lib/protocol/models.dart::HandoffOffer.fromJson`); `preferred_filename`
is optional. A negative `size_bytes` or a `sha256` that is not exactly 64
lowercase hex characters is rejected at parse time with a bounded
`ProtocolFormatException`, never a raw crash.

## Error code mapping

All bounded error codes from the protocol doc are represented 1:1 in
`lib/protocol/protocol.dart::HandoffErrorCode`
(`UNKNOWN_DEVICE`/`ARTIFACT_UNAVAILABLE` are sender-local and never
produced by this receiver, matching the protocol doc):

| Code | Where produced |
|---|---|
| `UNSUPPORTED_PROTOCOL` | Offer preflight, protocol version header mismatch. |
| `AUTHENTICATION_FAILED` | Offer/stream preflight, unknown/wrong auth token. |
| `UNSUPPORTED_CAPABILITY` | Offer preflight (test-configurable toggle for this MVP). |
| `UNSUPPORTED_MEDIA` | Offer preflight, MIME not in the configured allow-list (unset = permissive default). |
| `PAYLOAD_TOO_LARGE` | Offer preflight, declared size exceeds a configured maximum (unset = no product maximum chosen yet). |
| `INCOMPLETE_TRANSFER` | Stream, declared size not fully received (including a broken connection mid-read). |
| `INTEGRITY_MISMATCH` | Stream, computed SHA-256 does not match the offer's declared value. |
| `RECEIVER_REJECTED` | Offer preflight, generic test-configurable rejection; also a malformed offer JSON body. |
| `PAIRING_EXPIRED` | Mobile-side `PairingManager.completePairing()` only (local pairing validation, not a wire response from this receiver in A14). |
| `CANCELLED` | Not sent as a wire error body -- cancellation is a connection-level event per the protocol doc ("connection closed early"), not a JSON error response. |

## Capabilities

`DeviceCapability` mirrors the protocol doc's generic set exactly:
`RECEIVE_STREAM`, `TEMPORARY_FILE_HANDOFF`, `SHARE_TO_OS`. No per-app
capability (`WHATSAPP`, etc.) exists anywhere in the Dart code, matching
the desktop's own A13 contract.

## Pairing

Implemented as a local, mobile-side completion of the desktop-issued
`PairingPayload` (protocol version, session id, endpoint, secret,
expiry) -- see `docs/FRIENDSEND_ANDROID_MVP.md` "Pairing" for the full
explanation of why no wire callback exists yet in A14 and how the real
cross-language E2E tests bridge that gap explicitly for testing. The
secret itself is never persisted or logged
(`PairingPayload.redactedDescription`); a fresh, independent auth token
is minted locally rather than reusing the secret, matching the protocol
doc's stated semantics for what the resulting `auth_token` must be.

## Stream semantics

- Sender must not stream before `{"accepted": true}` -- verified
  directly: every offer-rejection test in `receiver_server_test.dart`
  and the cross-language `test_real_offer_rejection_costs_zero_payload_bytes`
  assert the corresponding desktop `HandoffState` never advances past
  `FAILED` with `bytes_sent == 0`.
- Bounded chunked reads: `dart:io`'s `HttpRequest` stream is consumed
  chunk-by-chunk with no full-body buffering, proven with a 2 MiB
  multi-chunk transfer asserting more than one progress event.
- Size + SHA-256 verification uses bytes the receiver actually wrote,
  never the sender's claim.

## Acknowledgement semantics

`RECEIVED` is reported only after both size and hash checks pass.
`HANDOFF_ACCEPTED` (protocol doc, "truthful acknowledgement table") is
represented locally as `HandoffUiSnapshot.shareOpened` and is set **only**
once the Android platform bridge actually reports the Sharesheet opened
-- never merely because `RECEIVED` was reached. No app state or wire
response ever claims a social-app send, delivery, or viewing.

## Known deviations from the v1 document

- **Pairing wire callback**: not implemented (see above) -- the document
  itself does not mandate one yet.
- **Capability/size/MIME rejection are test-configurable toggles**, not
  a fixed production policy, since A14 explicitly has no chosen product
  maximum size or MIME allow-list yet (§36/§37 of the A14 prompt).
- **`CANCELLED` is never sent as a JSON error body** by this receiver,
  only observed as a dropped connection -- consistent with, not a
  deviation from, the protocol doc's own framing of cancellation as a
  connection-level event.

No other deviations. No Protocol v2 semantics were introduced; this
receiver only ever declares and requires `protocol_version = 1`.
