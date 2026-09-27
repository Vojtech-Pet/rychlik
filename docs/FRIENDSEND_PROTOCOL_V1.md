# FriendSend Protocol v1

This document describes the FriendSend wire protocol platform-neutrally.
It is the contract for a future Android (or any other platform) receiver
implementation (A14) — **it never describes a Python class as the wire
protocol.** Where the current desktop reference implementation lives is
noted for cross-reference only (`rychlik.device`,
`tests/friendsend_receiver_fixture.py`).

**Status:** v1, frozen for A14 implementation. **The desktop side is
proven against a deterministic test receiver fixture, not a real Android
device.** No production-secure transport is claimed — see "Security
boundary" below.

## Protocol version

Every exchange declares `protocol_version = 1` via the
`X-FriendSend-Protocol-Version` HTTP header. A receiver or sender that
does not support the declared version must reject the exchange
(`UNSUPPORTED_PROTOCOL`) rather than attempt to interpret an
unrecognized version.

## Roles

- **Sender** = the Rýchlik desktop application.
- **Receiver** = the FriendSend peer (a real Android app in A14; a
  deterministic test fixture in A13). The receiver runs an HTTP server
  bound to an address the sender can reach; the sender is always the HTTP
  client.

## Message / endpoint flow

```text
1. Capability probe (optional, sender-initiated)
   GET /hello
   -> 200 { protocol_version, capabilities[], platform, device_id, display_name }

2. Pairing (out of band for the actual secret exchange -- see "Pairing
   semantics" below; this v1 document does not mandate a specific wire
   endpoint for step 2 itself, since no real second peer has validated one
   yet. A14 SHOULD introduce an explicit pairing endpoint once a concrete
   UX -- QR/manual entry/NFC -- is chosen.)

3. Handoff offer (sender -> receiver, metadata only, no payload bytes)
   POST /handoff/offer
   Headers: X-FriendSend-Protocol-Version, X-FriendSend-Auth-Token,
            X-FriendSend-Handoff-Id
   Body (JSON): { handoff_id, display_name, mime_type, size_bytes, sha256,
                  preferred_filename }
   -> 200 { "accepted": true }              -- proceed to step 4
   -> 4xx { "state": "FAILED", "error_code": "<code>" }  -- do NOT stream

4. Handoff stream (sender -> receiver, ONLY after acceptance)
   POST /handoff/stream
   Headers: X-FriendSend-Protocol-Version, X-FriendSend-Auth-Token,
            X-FriendSend-Handoff-Id, Content-Type: <mime_type>,
            Content-Length: <size_bytes>
   Body: raw binary payload, exactly Content-Length bytes
   -> 200 { "state": "RECEIVED", "bytes_received", "sha256" }
   -> 200 { "state": "FAILED", "error_code": "INTEGRITY_MISMATCH" | "INCOMPLETE_TRANSFER" }
   -> connection closed early by either side = treat as incomplete/cancelled
```

## Required fields

### `/handoff/offer` request body

| Field | Type | Required | Notes |
|---|---|---|---|
| `handoff_id` | string | yes | fresh random identifier, unique per send |
| `display_name` | string | yes | display/preferred filename, never a path |
| `mime_type` | string | yes | |
| `size_bytes` | integer | yes | `>= 0` |
| `sha256` | string | yes | 64 lowercase hex characters |
| `preferred_filename` | string | no | receiver must not rely on the extension alone for MIME/security decisions |

A receiver rejects a request missing any required field, with a negative
`size_bytes`, or a `sha256` that does not match the canonical
64-lowercase-hex format, **before** accepting the offer.

### Unknown fields

An unrecognized **optional** field in any message must be ignored for
forward compatibility. An unrecognized **required** field or an
unsupported protocol version/capability must never be silently accepted.

## Pairing semantics

A pairing exchange establishes a shared `auth_token` used for every
subsequent `/handoff/*` request from that sender to that receiver.

```text
PairingPayload:
    protocol_version
    pairing_session_id   -- cryptographically random, single-use
    desktop_instance_id
    endpoint              -- { host, port } of the endpoint this payload
                             refers to
    secret                -- 128-bit cryptographically random value,
                             hex-encoded
    expires_at_utc
```

- The `secret` must have at least 128 bits of entropy from a
  cryptographically secure source.
- A pairing session expires (300 seconds in the current desktop reference
  implementation) and cannot be extended.
- A pairing session is single-use: once successfully completed, it cannot
  be replayed to establish a second trust relationship.
- The resulting `auth_token` is a separate, freshly generated credential
  — not the pairing secret itself — used for ongoing `/handoff/*` auth.
- No raw pairing secret is persisted long-term by the reference desktop
  implementation.

## Authentication semantics

Every `/handoff/offer` and `/handoff/stream` request carries
`X-FriendSend-Auth-Token`. A receiver rejects any request whose token is
not a token it has accepted for a completed pairing, with
`AUTHENTICATION_FAILED`, before accepting the offer or reading any
payload bytes.

## Capability exchange

`GET /hello` advertises the receiver's `capabilities` as a list of
strings from a small, generic set (`RECEIVE_STREAM`,
`TEMPORARY_FILE_HANDOFF`, `SHARE_TO_OS`, ...). The set is deliberately
generic and never enumerates specific installed apps (no
`WHATSAPP`/`MESSENGER`/`TELEGRAM` capability) — a real FriendSend receiver
is expected to hand a received file to the platform's normal share
mechanism (e.g. Android's Sharesheet), which itself decides which apps
are eligible, without the desktop needing to know in advance. If the
sender requires a capability the receiver does not advertise, it must not
attempt the handoff (`UNSUPPORTED_CAPABILITY`).

## Handoff offer / stream separation

The offer/stream split exists specifically so a receiver's rejection
(unsupported protocol/capability/media, oversized payload, bad auth) is
knowable **before** any payload byte is sent. A sender must not begin
streaming until it has received `{"accepted": true}` from `/handoff/offer`.

## Payload stream

- The payload is streamed as the raw HTTP request body of
  `POST /handoff/stream`, exactly `Content-Length` bytes.
- Senders MUST stream in bounded chunks (the current desktop reference
  implementation default is 256 KiB) — never load the entire payload into
  memory before sending.
- Receivers MUST spool the incoming payload to **temporary** storage only
  — see "Temporary storage policy" below — never treat it as permanent
  storage, and MUST NOT trust the sender-declared `display_name`/
  `preferred_filename` as a filesystem path component (path traversal
  protection): store payload bytes under a receiver-generated, private
  temporary name, and use the declared filename only as display metadata
  once (if) it is later handed to the platform's own share/file mechanism.

## Receiver verification

Before reporting `RECEIVED`, a receiver MUST independently verify, using
bytes it actually received (never trusting the sender's own claim):

1. the total number of bytes received exactly equals the declared
   `size_bytes` (`INCOMPLETE_TRANSFER` otherwise);
2. the SHA-256 of the received bytes exactly equals the declared `sha256`
   (`INTEGRITY_MISMATCH` otherwise).

Only if both checks pass may the receiver report `{"state": "RECEIVED"}`.

## Temporary storage policy

FriendSend is permitted to use temporary storage when required (e.g.
Android may need a file/URI to hand to a share intent). Temporary content
MUST be removed after handoff completion, failure, or cancellation,
according to the receiver implementation's own contract — FriendSend must
never accumulate received media as permanent user-visible storage (it is
not a media library or cloud drive).

## Acknowledgement meanings (truthful acknowledgement table)

| State | Proven meaning |
|---|---|
| `CONNECTING` | the sender is establishing the connection |
| `TRANSFERRING` | the sender is actively sending payload bytes |
| `RECEIVED` | the receiver independently verified the complete payload arrived intact (size + SHA-256) |
| `HANDOFF_ACCEPTED` *(not reachable in A13)* | the receiver/OS accepted the media into its own share/handoff mechanism (e.g. Android Sharesheet was invoked) |
| `FAILED` | the send or the receiver's verification failed; see `error_code` |
| `CANCELLED` | the sender or user cancelled the handoff cooperatively |

**None of these states proves delivery to, or viewing by, the final
social-app recipient.** `RECEIVED` is the strongest state a receiver can
truthfully report without also implementing the platform share mechanism;
`HANDOFF_ACCEPTED` requires that further step and is not implemented by
the A13 desktop/fixture pair.

## Errors

Bounded taxonomy — a receiver/sender never invents new codes outside this
set for a v1 exchange:

```text
UNKNOWN_DEVICE
UNSUPPORTED_PROTOCOL
AUTHENTICATION_FAILED
PAIRING_EXPIRED
UNSUPPORTED_CAPABILITY
UNSUPPORTED_MEDIA
PAYLOAD_TOO_LARGE
ARTIFACT_UNAVAILABLE
CONNECTION_FAILED
INCOMPLETE_TRANSFER
INTEGRITY_MISMATCH
RECEIVER_REJECTED
CANCELLED
```

`UNKNOWN_DEVICE` and `ARTIFACT_UNAVAILABLE` are sender-local conditions
(never sent over the wire); the rest may appear in a `/handoff/offer` or
`/handoff/stream` error response body as `{"error_code": "<code>"}`.

## Forward compatibility

- Unknown **optional** JSON fields in any message: ignore.
- Unknown **required** field, unknown/unsupported protocol version, or a
  required capability the peer does not have: reject visibly, never guess.
- A future protocol version may add new optional fields/capabilities
  without breaking a v1-only peer, as long as v1's required fields remain
  present and correctly typed.

---

## Android A14 mapping (non-normative)

This section is guidance for a future Android implementation, not part of
the v1 wire contract itself.

```text
receiver HTTP server        -- a lightweight embedded HTTP server (e.g.
                                NanoHTTPD or ktor) bound to the device's
                                Wi-Fi/LAN address, advertised via the
                                pairing UX (QR code containing the
                                PairingPayload)

temporary spool              -- payload streamed to the app's private
                                cache directory (never external/shared
                                storage) while verification is pending

verified payload              -- once size+SHA-256 verified, exposed via
                                a FileProvider content:// URI (never a
                                raw file:// path) scoped to the
                                requesting share flow

ACTION_SEND intent            -- once a content:// URI is ready, the app
                                launches a standard Android Sharesheet
                                (Intent.ACTION_SEND / createChooser) so
                                any compatible installed app may receive
                                it -- FriendSend never hardcodes a
                                specific target app

cleanup                       -- the temporary cache file (and any
                                FileProvider grant) is revoked/deleted
                                once the share flow completes, fails, or
                                the app is backgrounded past a bounded
                                window -- FriendSend's cache is never a
                                permanent media library
```

None of this is implemented in A13. It is recorded here so A14 can
implement it without renegotiating the desktop-side protocol.
