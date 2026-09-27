# Device Mode / FriendSend Desktop Handoff Foundation (Prompt A13)

FriendSend Android does not exist yet. This phase proves the **desktop**
half of a versioned, authenticated handoff contract against a
deterministic test/dev receiver fixture — never a production phone
application, never a claim of end-recipient delivery. See
`docs/FRIENDSEND_PROTOCOL_V1.md` for the platform-neutral wire contract
A14's Android implementation will consume, and
`docs/DEVICE_MODE_FOUNDATION_RESULT.md` for this phase's result report.

## Device Mode vs. Share by Link

Rýchlik has two independent sharing modes. They are not merged, do not
share code, and one failing never affects the other (proven directly,
`test_device_mode_and_share_by_link_coexist_independently`):

```text
Share
├── Share by Link (rychlik.share)     -- desktop -> public HTTPS URL -> browser
└── Device Mode (rychlik.device)      -- desktop -> paired FriendSend peer -> streamed media
```

## FriendSend's product role

FriendSend is an optional mobile companion — a temporary bridge/proxy,
never a permanent media inbox, cloud drive, or social network. Rýchlik
remains fully usable without it. The receiver fixture built for this
phase's tests demonstrates the "no permanent storage" contract directly:
every received payload is deleted immediately after verification
(success, failure, or cancellation) — never accumulated as a fake inbox.

## Desktop architecture

```text
Completed Rýchlik download (A1-A12)
        |
        v
A12 completed_file() -> completed_artifact_bridge -> Artifact
        |                 (existing, reused unchanged)
        v
rychlik.device.device_handoff_service.DeviceHandoffService
        |  (a SEPARATE service -- NOT part of DownloadManagerService, §6)
        v
rychlik.device.transport.FriendSendTransport (abstraction)
        |
        v
HttpFriendSendTransport (the real implementation, §39)
        |  bounded chunked streaming over a real socket
        v
FriendSend peer (tests/friendsend_receiver_fixture.py in A13;
                  a real Android app starting in A14)
```

`DeviceHandoffService` never imports `DownloadTask`/`QueueEntry`/
`DownloadManagerService` internals, and `DownloadManagerService` never
imports anything from `rychlik.device` — the only connection between the
two branches is the public `Artifact` object, obtained exactly the way
A12's GUI Share button already does (`completed_file()` ->
`completed_artifact_bridge`).

## Protocol version

```python
FRIENDSEND_PROTOCOL_VERSION = 1
```

Every pairing/capability/handoff exchange carries this version explicitly
(an `X-FriendSend-Protocol-Version` header on the wire). A mismatch fails
visibly as `UnsupportedProtocolError`/`HandoffErrorCode.UNSUPPORTED_PROTOCOL`
— never a silent compatibility guess.

## Device identity

`FriendSendDevice` is identified by `device_id` alone — never
`display_name`, which is presentation-only and may collide or change.
`FriendSendEndpoint(host, port)` is the only addressing shape; no future
code parses an ad hoc endpoint string. A device descriptor never holds a
live socket/session object — only an `auth_token` (a credential, not a
connection) and the negotiated `protocol_version`/`capabilities`.

## Capability model

```text
RECEIVE_STREAM
TEMPORARY_FILE_HANDOFF
SHARE_TO_OS
```

Deliberately generic — never a per-social-app whitelist
(`WHATSAPP`/`MESSENGER`/`TELEGRAM`). A future FriendSend uses Android's
normal Sharesheet, so the desktop never needs to know which specific apps
are installed on the peer. A device lacking a capability the desktop
requires fails as `UnsupportedCapabilityError` before any byte streams.

## Pairing session contract

```text
PairingManager.create_session(endpoint) -> PairingPayload(
    protocol_version, pairing_session_id, desktop_instance_id,
    endpoint, secret, expires_at_utc,
)
PairingManager.complete_pairing(session_id, secret, *, device_id, ...) -> FriendSendDevice
```

- **Secret**: `secrets.token_hex(16)` — 128 bits of cryptographically
  secure randomness, never `random.randint`.
- **Expiry**: 300 seconds by default (`DEFAULT_PAIRING_TTL_SECONDS`); an
  expired session raises `PairingExpiredError`, no silent extension.
- **One-time**: a session transitions to `CONSUMED` on first successful
  completion; any further attempt (a replay) also raises
  `PairingExpiredError` — proven directly
  (`test_pairing_is_one_time_replay_rejected`).
- **Wrong secret**: `secrets.compare_digest` comparison; a mismatch raises
  `PairingAuthenticationFailedError`, indistinguishable in message text
  from an unknown session id (never leaks which case applies).
- **Runtime-only**: `PairingManager` keeps sessions purely in memory. No
  raw pairing secret is ever persisted to SQLite (A12's `state_store.py`
  is never touched by this package) — long-term device trust persistence
  is explicitly deferred to A14, once real Android credential semantics
  exist.
- **Redaction**: `PairingPayload.redacted()` masks the secret for safe
  logging; `to_wire_dict()` (the real, unmasked payload) is a distinct,
  deliberate call — never accidentally logged.

### Pairing transport scoping

`PairingManager.complete_pairing()` is a plain Python method — the
domain operation a future network listener calls once it has received the
secret back from the peer. A13 proves this operation's full security
property set (secret entropy, expiry, replay-resistance, wrong-secret
rejection) directly against the method, without also inventing and
testing a second, currently-hypothetical network round-trip for how a
real phone would physically deliver that secret back to the desktop (QR
scan, manual entry, NFC, ...) — that UX is explicitly out of scope here
(§31/§111) and is A14's concern once a real peer exists to design it
against.

## Transport abstraction + real transport

```text
FriendSendTransport (ABC)
    probe(endpoint) -> ProbeResult
    send(device, request, artifact_path, *, chunk_size, progress_callback,
         cancel_event) -> SendOutcome
```

`HttpFriendSendTransport` is the one real implementation: genuine
HTTP/1.1 over a real TCP socket, a two-phase exchange —

```text
POST /handoff/offer    -- JSON metadata only, no payload bytes
POST /handoff/stream    -- the raw bytes, ONLY once the offer was accepted
```

so a receiver rejection (unsupported protocol/capability/media, oversized
payload, bad auth) is known **before** a single byte of the artifact
streams (§50/§93/§102, verified directly: `bytes_sent == 0` on every
offer-phase rejection test).

**Security boundary (§35-37):** this is explicitly an EXPERIMENTAL/TEST
TRANSPORT — plain HTTP plus a bearer auth token, no TLS, no certificate
pinning, no Noise-protocol channel, no pairing-derived session key. See
`docs/OPEN_VALIDATION_DEBT.md` (`A13-PRODUCTION-SECURE-CHANNEL`) for the
explicit, tracked gap this leaves before any real-LAN beta.

## Receiver fixture

`tests/friendsend_receiver_fixture.py::FriendSendReceiverFixture` — a
real `ThreadingHTTPServer`, loopback-only (`127.0.0.1`), OS-assigned port
(never a hardcoded port). **Explicitly test/dev-only code, never
presented as the FriendSend Android application.** It:

- advertises protocol version + capabilities via `GET /hello`;
- validates protocol version, auth token, payload size, and MIME type at
  the **offer** phase, before any bytes are read;
- streams the request body to a private `tempfile.mkstemp()`-named
  temporary file — the declared filename is **never** used to construct a
  filesystem path (path-traversal-proof by construction, not by
  sanitization, §58);
- independently verifies received byte count and SHA-256 against the
  declared values;
- deletes the temporary file immediately after verification, whether the
  outcome was success, failure, or an abrupt disconnect (cancellation) —
  proven directly (`temp_dir_is_empty()` assertions across every terminal
  path).

## Handoff request / state model

```text
DeviceHandoffRequest(handoff_id, device_id, artifact_id, display_name,
                      mime_type, size_bytes, sha256, preferred_filename)
```

Only public-safe `Artifact` information ever crosses this boundary —
**never** `artifact.local_path`. `handoff_id` is fresh cryptographic
randomness (`uuid.uuid4()`) on every single call to `send()`, even for
the same `Artifact` to the same device twice (verified:
`test_same_artifact_sent_twice_gets_distinct_handoff_ids`).

```text
HandoffState: CREATED -> CONNECTING -> TRANSFERRING -> RECEIVED
                                                      -> FAILED
                                                      -> CANCELLED
```

`HANDOFF_ACCEPTED` (OS/app accepted the share) is **not a reachable
state** in A13 — the fixture never implements Android Sharesheet, so
claiming it would be a lie. `RECEIVED` means only "FriendSend verified the
complete payload arrived intact" — never that any further app or person
received or viewed it. See the truthful-acknowledgement table in
`docs/FRIENDSEND_PROTOCOL_V1.md`.

## Streaming / memory / integrity

- Default chunk size: 256 KiB (`_DEFAULT_CHUNK_SIZE`), streamed straight
  from the validated `Artifact.local_path` — never the whole file loaded
  into memory. Proven structurally
  (`test_bounded_memory_streaming_uses_chunk_size`: every `read()` call
  on the artifact file is spied and asserted `<= chunk_size`, with
  multiple reads for a payload several times larger than one chunk).
- The sender computes its **own** running SHA-256 while streaming and
  compares it to the `Artifact`'s declared hash after the transfer
  completes — catching a source file mutated mid-transfer even if the
  receiver's independent check would otherwise have passed on whatever
  bytes it happened to receive (§59/§60,
  `test_source_mutated_during_transfer_caught_locally`).
- The receiver independently re-verifies size and SHA-256; any mismatch
  is `FAILED`/`INTEGRITY_MISMATCH`, **never** `RECEIVED`
  (`test_integrity_mismatch_never_reports_received`).
- A missing source file at `send()` time raises `ArtifactUnavailableError`
  synchronously, before any handoff_id is even created.

## Cancellation

`DeviceHandoffService.cancel(handoff_id)` sets a per-handoff
`threading.Event`; the sender's chunk generator checks it between chunks
and raises internally, producing `HandoffState.CANCELLED` (never
`FAILED`). Cancellation:

- never touches the original completed download (`DownloadTask` stays
  `COMPLETED`, the file on disk is byte-for-byte unchanged);
- never affects an independent `ShareLink` for the same `Artifact`;
- causes the receiver to observe an early-closed connection and clean up
  its temporary partial file exactly like any other incomplete transfer.

All proven directly by `test_real_cancel_mid_transfer` and
`test_cancel_device_handoff_does_not_affect_original_download`.

## Progress / event model

`DeviceHandoffSnapshot(handoff_id, device_id, artifact_id, state,
bytes_sent, total_bytes, progress_fraction, failure_code)` — immutable,
no socket/thread/path reference. This is a **separate** telemetry model
from A7's download progress; handoff bytes are never written into
`DownloadViewSnapshot`. A framework-neutral `subscribe()`/`unsubscribe()`
event stream (`DeviceHandoffEventKind`: `DEVICE_PAIRED`,
`HANDOFF_STARTED`, `HANDOFF_PROGRESS`, `HANDOFF_RECEIVED`,
`HANDOFF_FAILED`, `HANDOFF_CANCELLED`) mirrors A10's philosophy: progress
events are coalesced (at most one every 0.1s per handoff, never one per
chunk), subscriber callbacks run outside every internal lock, and one
raising subscriber cannot corrupt an active transfer (proven directly).

## Worker pool / capacity

`DeviceHandoffService` owns its own small `ThreadPoolExecutor` (default 1
concurrent handoff) — it never steals A5's download-transfer executor.
`max_active_transfers` (A10/A5) and Device Mode's concurrency are
completely independent configuration; a completed `Artifact` may be sent
while other downloads are actively running (proven in the cross-branch
E2E tests, which run a real download and a real device handoff
concurrently against separate services).

## GUI integration (bounded, §105/§106)

The existing `ShareDialog` gained a small, real wiring: an optional
`device_handoff_service` constructor parameter. Production `main.py` does
not construct a `DeviceHandoffService` — there is no real FriendSend app
to pair with — so the dialog's device list stays truthfully empty
("Device Mode not available" / "No FriendSend devices paired") rather
than showing a fake phone. When a service **is** supplied (as every new
test proves), the existing "Send to device / app" button calls
`service.send(device_id, artifact)` and shows only a bounded, honest
"Device send started" acknowledgement — never a delivery claim. The
dialog itself was not redesigned.

## Known limitations

```text
no Android application, no Android Sharesheet handoff -- A14
no final production-secure LAN transport (docs/OPEN_VALIDATION_DEBT.md,
  A13-PRODUCTION-SECURE-CHANNEL)
no mDNS/LAN discovery -- tests manually register a known loopback endpoint
no persistent device trust across desktop restarts (runtime-only, §75)
no QR-code pairing UX -- a textual/debug pairing payload is sufficient
  for A13's protocol tests
no transcoding -- an unsupported-MIME artifact is rejected, never
  transcoded
no bandwidth arbitration between downloads/device sends/Share by Link
no internet relay / cloud fallback for Device Mode
```
