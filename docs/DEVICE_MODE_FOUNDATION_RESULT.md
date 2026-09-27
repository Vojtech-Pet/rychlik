PHASE:
Prompt A13 — Device Mode / FriendSend Desktop Handoff Foundation

STATUS:
COMPLETE

BASELINE COMMIT:
5718233

ACTUAL BASELINE HEAD:
5718233b7f56955933071aac9e7c811d1953a166 (worktree was clean at start)

FILES CHANGED:
  new:
    src/rychlik/device/__init__.py
    src/rychlik/device/contracts.py
    src/rychlik/device/pairing.py
    src/rychlik/device/registry.py
    src/rychlik/device/transport.py
    src/rychlik/device/device_handoff_service.py
    tests/friendsend_receiver_fixture.py (test/dev-only support code)
    tests/test_device_contracts.py
    tests/test_device_pairing.py
    tests/test_device_registry.py
    tests/test_device_handoff_service.py
    tests/test_device_cross_branch_e2e.py
    docs/DEVICE_MODE_FOUNDATION.md
    docs/DEVICE_MODE_FOUNDATION_RESULT.md
    docs/FRIENDSEND_PROTOCOL_V1.md
  modified:
    src/rychlik/gui/share_dialog.py (bounded Device Mode wiring, §105/§106)
    tests/test_share_dialog.py (+4 Device Mode tests, updated truthful-
      empty-state expectation)
    docs/OPEN_VALIDATION_DEBT.md (+A13-PRODUCTION-SECURE-CHANNEL entry)

DEVICE MODE TYPES:
  FriendSendEndpoint, FriendSendDevice, DeviceCapability, HandoffState,
  HandoffErrorCode, DeviceHandoffRequest, DeviceHandoffSnapshot (all in
  rychlik.device.contracts); PairingPayload, PairingManager (rychlik.
  device.pairing); PairedDeviceRegistry (rychlik.device.registry);
  FriendSendTransport (ABC), HttpFriendSendTransport, ProbeResult,
  SendOutcome (rychlik.device.transport); DeviceHandoffService,
  DeviceHandoffEvent/Kind (rychlik.device.device_handoff_service).

DEVICE IDENTITY:
  device_id (string) is the sole identity; display_name is presentation-
  only. FriendSendDevice never holds a live socket/session object.
  UnknownDeviceError for any unregistered device_id -- never a fallback
  to "the first" or "most recently used" device.

PROTOCOL VERSION:
  FRIENDSEND_PROTOCOL_VERSION = 1, carried on every offer/stream request
  via X-FriendSend-Protocol-Version. A device with a different reported
  protocol_version -> UnsupportedProtocolError before send() proceeds.

PROTOCOL DOCUMENT:
  docs/FRIENDSEND_PROTOCOL_V1.md -- platform-neutral (JSON field names,
  HTTP endpoints, no Python class described as the wire format), includes
  the required-field table, pairing/auth semantics, the truthful
  acknowledgement table, the bounded error taxonomy, forward-
  compatibility rules, and a non-normative Android A14 mapping section
  (FileProvider/content:// URI/ACTION_SEND/Sharesheet/cleanup).

CAPABILITY MODEL:
  RECEIVE_STREAM / TEMPORARY_FILE_HANDOFF / SHARE_TO_OS -- generic, never
  a per-social-app whitelist. A device lacking RECEIVE_STREAM ->
  UnsupportedCapabilityError before any byte streams (verified live via
  both a locally-lacking-capability device and a receiver-side forced
  UNSUPPORTED_CAPABILITY offer rejection).

PAIRING MODEL:
  PairingManager.create_session(endpoint) -> PairingPayload;
  complete_pairing(session_id, secret, ...) -> FriendSendDevice
  (registers into PairedDeviceRegistry via DeviceHandoffService.
  complete_pairing(), emits DEVICE_PAIRED). Pairing transport itself
  (how a real phone would physically receive the secret -- QR/manual
  entry/NFC) is explicitly out of scope for A13 (§31/§111); complete_
  pairing() is proven directly as a domain operation instead.

PAIRING SECRET / EXPIRY:
  secrets.token_hex(16) = 128 bits, verified distinct across sessions.
  Default TTL 300s (DEFAULT_PAIRING_TTL_SECONDS), expired session ->
  PairingExpiredError, no silent extension. One-time: a session moves to
  CONSUMED on first successful completion; replay of the same session+
  secret afterward also raises PairingExpiredError. Wrong secret (correct
  session id) -> PairingAuthenticationFailedError via secrets.compare_
  digest. No raw secret persisted anywhere; PairingManager is runtime-
  only in-memory state. PairingPayload.redacted() masks the secret for
  logging; the real value is only ever exposed via the separate, explicit
  to_wire_dict().

DEVICE REGISTRY:
  PairedDeviceRegistry -- in-memory, thread-safe (add/get/remove/
  all_devices), runtime-only per §75. Never wedged into the A12 SQLite
  download-state database (§76) -- verified by inspection (device package
  never imports state_store.py).

TRANSPORT ABSTRACTION:
  FriendSendTransport(ABC): probe()/send(). The service layer is never
  HTTP-specific -- HttpFriendSendTransport is one concrete
  implementation, injectable/replaceable.

REAL TEST TRANSPORT:
  HttpFriendSendTransport: real HTTP/1.1 over a real TCP socket, two-
  phase (POST /handoff/offer metadata-only preflight, then POST /handoff/
  stream for bytes only after acceptance) so a rejection is known before
  any payload byte streams. Explicitly labeled EXPERIMENTAL/TEST
  TRANSPORT -- plain HTTP + bearer auth token, no TLS/pinning/Noise/
  session-key derivation (see OPEN VALIDATION DEBT below).

RECEIVER FIXTURE:
  tests/friendsend_receiver_fixture.py::FriendSendReceiverFixture -- real
  ThreadingHTTPServer, 127.0.0.1, OS-assigned port. Validates protocol
  version/auth token/size/MIME at the offer phase (before reading any
  body byte); spools stream bytes to a tempfile.mkstemp() name (declared
  filename NEVER used to build a path); independently verifies size +
  SHA-256; deletes the temp file immediately after every terminal
  outcome (success/failure/cancel). Test-only configuration hooks
  (accept_token, force_capability_rejection, force_receiver_rejection,
  force_corrupt_next_transfer) exist only for deterministic test
  scenarios, mirroring the existing http_fixture_server.py pattern.
  Clearly test/dev-only code, never described as FriendSend Android.

HANDOFF REQUEST MODEL:
  DeviceHandoffRequest(handoff_id, device_id, artifact_id, display_name,
  mime_type, size_bytes, sha256, preferred_filename) -- validated
  (non-empty ids, non-negative size, canonical 64-lowercase-hex sha256).
  Only public-safe Artifact fields ever populate it -- local_path never
  crosses this boundary. handoff_id is a fresh uuid4() every send(), even
  for the same Artifact to the same device twice (verified distinct).

HANDOFF STATE MODEL:
  CREATED -> CONNECTING -> TRANSFERRING -> {RECEIVED, FAILED, CANCELLED}.
  HANDOFF_ACCEPTED is defined in the protocol doc but deliberately not a
  reachable Python enum value/outcome in A13 -- never faked.

ACKNOWLEDGEMENT SEMANTICS:
  RECEIVED means only "receiver verified size+SHA-256 of the complete
  payload" -- never delivery to or viewing by a final recipient. No
  DELIVERED/VIEWED/WHATSAPP_DELIVERED state or text exists anywhere in
  the codebase or GUI. The Share dialog's Device Mode status message was
  specifically tested to never contain "delivered"/"received by"/"viewed"
  (test_send_to_device_never_claims_delivery).

STREAMING MODEL:
  Sequential read from the validated Artifact.local_path in bounded
  chunk_size reads (default 256 KiB), sent as the raw HTTP request body
  with a real Content-Length. A structural spy test
  (test_bounded_memory_streaming_uses_chunk_size) proves every read() call
  is <= chunk_size across a ~700KB payload with multiple reads, never one
  whole-file read.

CHUNK / MEMORY POLICY:
  256 KiB default (_DEFAULT_CHUNK_SIZE), configurable per
  DeviceHandoffService instance. An additional test-only `chunk_delay`
  knob (default 0.0, never used by production code paths) paces chunk
  emission for deterministic progress/cancel test timing over a fast
  loopback connection -- the transfer is still real bytes over a real
  socket either way.

INTEGRITY MODEL:
  Two independent checks must both pass: (1) the SENDER's own running
  SHA-256, computed while streaming, must match the Artifact's declared
  hash (catches local source mutation during transfer, even before any
  receiver response is examined); (2) the RECEIVER's independent size +
  SHA-256 verification of what it actually received. Either mismatch ->
  FAILED/INTEGRITY_MISMATCH (or INCOMPLETE_TRANSFER for a short transfer),
  never RECEIVED.

TEMPORARY STORAGE POLICY:
  Documented in both docs/DEVICE_MODE_FOUNDATION.md and docs/
  FRIENDSEND_PROTOCOL_V1.md: temporary spool only, cleaned up after every
  terminal outcome, never a permanent inbox. Modeled and verified
  directly by the fixture (temp_dir_is_empty() assertions).

CANCELLATION:
  DeviceHandoffService.cancel(handoff_id) sets a per-handoff threading.
  Event; the sender's chunk generator observes it between chunks and
  raises an internal TransportCancelled sentinel, distinct from a real
  network failure, producing CANCELLED (never FAILED). Verified: original
  download's DownloadTask stays COMPLETED, the file on disk is byte-
  identical, an independent ShareLink for the same Artifact is
  unaffected, and the receiver's temporary partial is cleaned up.

HANDOFF PROGRESS MODEL:
  DeviceHandoffSnapshot(bytes_sent, total_bytes, progress_fraction) --
  fully separate from A7's DownloadViewSnapshot; never written into it.
  Progress observed 0 < bytes_sent < total_bytes before completion in a
  real E2E test.

EVENT MODEL:
  subscribe()/unsubscribe() -> DeviceHandoffEvent(kind, handoff_id,
  device_id). DEVICE_PAIRED, HANDOFF_STARTED, HANDOFF_PROGRESS (coalesced
  to >=0.1s between emissions per handoff, never per chunk),
  HANDOFF_RECEIVED/FAILED/CANCELLED. Callbacks always run outside every
  internal lock; one raising subscriber does not stop an active transfer
  (verified: full RECEIVED completion still occurs with a subscriber that
  always raises).

A12 COMPLETED-ARTIFACT INTEGRATION:
  Reuses rychlik.gui.completed_artifact_bridge.build_artifact_for_
  completed() unchanged -- the exact same bridge A12's GUI Share button
  uses. No folder search, no filename guessing, no A4/A5 worker-result
  access anywhere in rychlik.device.

GUI INTEGRATION STATUS:
  Bounded (§105/§106): ShareDialog gained an optional
  device_handoff_service constructor parameter. Production main.py does
  NOT construct one (no real FriendSend app exists) -- the dialog's
  device list stays truthfully empty by default. When supplied, the
  existing "Send to device / app" button lists real paired devices and
  calls service.send() directly, showing only a bounded "Device send
  started" acknowledgement -- never a delivery claim. The dialog was not
  redesigned.

TESTS ADDED: 52
  tests/test_device_contracts.py: 7
  tests/test_device_pairing.py: 10
  tests/test_device_registry.py: 4
  tests/test_device_handoff_service.py: 23
  tests/test_device_cross_branch_e2e.py: 4
  tests/test_share_dialog.py: +4 (net, after replacing one outdated
    always-enabled assertion with a truthful-empty-state test)

TESTS RUN: 840 passed, 0 failed, 0 skipped (788 A12 baseline + 52 new).
  Full suite run three times for stability; test_device_handoff_service.py
  and test_device_cross_branch_e2e.py individually re-run 2-3 times during
  development with no flakiness after the one timing fix described below.

REAL SOCKET HANDOFF E2E:
  PASS -- test_real_streamed_transfer_received and
  test_multiple_chunks_and_correct_final_hash: real HTTP, real bytes,
  multiple 256KiB-bounded chunks, correct final SHA-256, RECEIVED, temp
  storage cleaned.

REAL COMPLETED-DOWNLOAD→DEVICE E2E:
  PASS -- test_real_download_to_device_handoff_end_to_end: a real URL
  download through DownloadManagerService, completed_file(),
  completed_artifact_bridge, a real paired device, a real socket
  transfer, RECEIVED with matching SHA-256, and the original download
  left COMPLETED and untouched.

REAL RESTARTED-COMPLETION→DEVICE E2E:
  PASS -- test_restarted_completion_to_device_handoff: complete a
  download, stop the service, start a fresh service instance against the
  same database, and successfully hand off the durably-recovered
  Artifact to a real receiver.

SHARE-BY-LINK COEXISTENCE E2E:
  PASS -- test_device_mode_and_share_by_link_coexist_independently: one
  completed Artifact is both linked via the real ShareLinkService and
  sent via real Device Mode in the same test; neither operation affects
  the other's outcome.

REAL CANCEL E2E:
  PASS -- test_real_cancel_mid_transfer (device-mode-only) and
  test_cancel_device_handoff_does_not_affect_original_download (cross-
  branch): cancellation mid-stream leaves the original completed
  download's task state, file bytes, and durable completed-file record
  completely unaffected.

THREAD / SOCKET LEAK CHECK:
  PASS -- test_no_thread_leak_after_stop: DeviceHandoffService.stop()
  joins its worker pool; active thread count after stop() is <= before
  start(). The receiver fixture's ThreadingHTTPServer is explicitly shut
  down and its temp directory removed in every test's teardown.

A9 CRASH→RANGE VALIDATION DEBT:
OPEN
  Untouched by this phase (A13 does not touch acquisition/resume
  mechanics). Still tracked in docs/OPEN_VALIDATION_DEBT.md.

A13 PRODUCTION SECURE-CHANNEL DEBT:
OPEN
  New entry added to docs/OPEN_VALIDATION_DEBT.md
  (A13-PRODUCTION-SECURE-CHANNEL): the real HttpFriendSendTransport is
  plain HTTP + bearer token, explicitly not claimed as LAN-secure.
  Blocking a real LAN beta, not blocking this foundation phase.

EXISTING TEST REGRESSIONS:
NONE beyond one intentional, documented test-policy update: the old
  test_share_dialog.py assertion that "Send to device / app" is always
  enabled for a completed Artifact was replaced, since A13 correctly
  makes it truthfully disabled when no DeviceHandoffService (or no paired
  devices) is present, rather than always enabled with no real backend
  behind it. All other pre-A13 tests (787 of the 788 baseline) pass
  unmodified.

BUGS FOUND:
  1. (Self-caught during test development, not a product bug) The first
     versions of the progress and cancel E2E tests used a several-
     hundred-KB payload sent over real loopback HTTP with the default
     256KiB chunking -- fast enough on localhost that the transfer could
     complete within a single test polling tick, so no partial progress
     was ever observed and cancel() sometimes raced a transfer that had
     already finished (returning False because its cancel_event had
     already been cleaned up). Root-caused to test timing, not a product
     defect. Fixed by adding a small, explicit test-only `chunk_delay`
     parameter to the transport/service (default 0.0, never exercised by
     any production code path) so these specific tests can pace a real
     multi-chunk transfer deterministically -- the same pattern already
     used throughout this project's slow HTTP download fixtures.

KNOWN LIMITATIONS:
  no Android application exists; no Android Sharesheet handoff is
    implemented or claimed
  no final production-secure LAN transport (tracked, OPEN)
  no mDNS/LAN discovery -- endpoints are registered manually in tests
  no persistent device trust across desktop application restarts
    (pairing/registry state is runtime-only by design, §75)
  no QR-code pairing UX -- a textual/debug PairingPayload is sufficient
    for A13's protocol-level tests; A14 designs the real UX against a
    real second peer
  no transcoding -- an artifact whose MIME type a receiver does not
    support is rejected (UNSUPPORTED_MEDIA), never transcoded
  no bandwidth arbitration between downloads, device sends, and Share by
    Link
  no internet relay/cloud fallback for Device Mode
  the pairing secret's physical delivery mechanism (QR/manual entry/NFC)
    is not designed or implemented in A13 -- complete_pairing() is proven
    as a domain operation directly, independent of that future UX

GATE:
PASS -- Device Mode remains fully separate from Share by Link and from
  DownloadManagerService, coexisting independently and proven so live;
  handoffs only ever start from an already-validated Artifact, never an
  arbitrary path; the protocol has an explicit version, device identity is
  never display-name-based, capabilities are explicit and enforced before
  any byte streams; pairing uses 128-bit cryptographically secure secrets,
  expires, is replay-resistant, and never persists its raw secret; a real
  transport abstraction exists with one real, non-mock HTTP implementation
  proven against a real (clearly test/dev-only) receiver fixture; transfer
  is bounded-chunk streamed with a structural proof against whole-file
  reads; both sender and receiver independently verify size and SHA-256,
  and integrity failure never reports RECEIVED; the receiver's temporary
  storage is cleaned after every terminal outcome and path traversal
  cannot escape it; cancellation is cooperative and provably does not
  affect the original download, an independent ShareLink, or leave
  receiver-side partial data; acknowledgement semantics are truthful
  throughout, with no delivery claim anywhere; the full cross-branch chain
  (real download -> durable completed-file identity -> Artifact -> real
  Device handoff -> real receiver) passes, including after a full service
  restart; the A13 production-secure-channel gap is explicitly tracked
  rather than hidden, and the pre-existing A9 debt remains tracked and
  untouched; all previous tests remain green; worktree clean (pending
  this commit).

NEXT PHASE READY:
YES

NEXT RECOMMENDED PHASE:
Prompt A14 — FriendSend Android Receiver MVP

COMMIT:
058c994
