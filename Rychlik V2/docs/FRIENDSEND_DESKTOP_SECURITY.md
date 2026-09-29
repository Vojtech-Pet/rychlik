# FriendSend Desktop — Security Implementation (Prompt A15)

Implementation detail companion to
[FRIENDSEND_SECURITY_PROFILE_V1.md](FRIENDSEND_SECURITY_PROFILE_V1.md)
(normative) -- covers exactly how the Python desktop side realizes that
profile. All code lives under `src/rychlik/device/security/`.

## Identity key location and permissions

`identity.py` (`DesktopIdentityStore`) persists the desktop's Ed25519
signing key and `desktop_instance_id` at:

```text
$XDG_DATA_HOME/rychlik/friendsend/desktop_identity.json
```

falling back to `~/.local/share/rychlik/friendsend/` when
`XDG_DATA_HOME` is unset. Directory permissions `0700`, file permissions
`0600` (best-effort on non-POSIX platforms via a bare `try/except
OSError`). The write is atomic (`.tmp` file then `os.replace()`) -- a
crash mid-write can never leave a half-written file that a later run
misreads as valid or silently regenerates over (`DesktopIdentityCorruptError`
is raised instead, visibly, per §89/§90).

Deliberately **separate** from `state.db` (A1-A12 download persistence,
§17) and from the trust store (below) -- three independent files, three
independent concerns.

## Trust store

`trust_store.py` (`FriendSendTrustStore`) persists trusted FriendSend
devices as one JSON file (caller-supplied path; the RESULT document
records the actual location used in tests/production wiring), atomically
written the same way as the identity file. Each `TrustedFriendSendDevice`
record: `device_id`, `display_name`, `tls_spki_sha256`, `protocol_version`,
`security_profile`, `endpoint_host`/`endpoint_port`, `paired_at_utc`,
`last_seen_at_utc`. The one-time pairing secret is never stored here.

`FriendSendTrustStore` uses an `RLock` (not a plain `Lock`) internally --
`upsert()`/`remove()` hold the lock while calling `_ensure_loaded()`,
which itself acquires the lock again via `load()` the first time it runs
on a not-yet-loaded store; a plain `Lock` self-deadlocks on that first
call. This was found and fixed during A15's own real cross-language
pairing E2E test (see the RESULT document, BUGS FOUND).

## TLS pin validation

`secure_transport.py::connect_and_verify_pin()` performs the TCP
connect + TLS handshake + peer-certificate SPKI extraction + pin
comparison described in the security profile document, using a manual
`ssl.SSLContext` (`PROTOCOL_TLS_CLIENT`, `check_hostname=False`,
`verify_mode=CERT_NONE` -- this is the correct, intentional trust model
for a self-signed pinned certificate, not a lazily-disabled check; see
the security profile doc's threat model). The pin check happens and
either succeeds or raises `TlsPinMismatchError` **before** the function
ever returns a usable socket to its caller -- there is no "send now,
verify after" window.

## Request signing

`SecureFriendSendTransport.send()` (implementing the same
`FriendSendTransport` ABC as A13's `HttpFriendSendTransport`, so it
composes directly into `DeviceHandoffService`) fetches a real
`GET /auth/challenge`, builds the canonical signing input
(`canonical.py::auth_signature_input`), signs it with the desktop's
Ed25519 private key, and sends the result as
`X-FriendSend-Desktop-Id`/`X-FriendSend-Challenge-Id`/`X-FriendSend-Signature`
headers on `POST /handoff/offer`.

## Why a hand-rolled minimal HTTP client for the secure path

`minimal_http.py` implements request/response framing manually over the
already-TLS-connected, already-pin-verified socket, rather than using
`requests`. This is a deliberate choice, not an oversight: Prompt A14
found a real interoperability bug where `requests` sent an invalid
simultaneous `Content-Length` + `Transfer-Encoding: chunked` request for
a generator body (see A14's `HttpFriendSendTransport` /
`_SizedChunks` fix). For the security-critical secure transport, full
manual control over the exact bytes sent avoids that whole class of
ambiguity from the start.

## Pairing bootstrap listener

`pairing_bootstrap.py` (`SecurePairingManager`) opens a short-lived,
plain-HTTP `ThreadingHTTPServer` only while a pairing session is pending,
bound to `127.0.0.1` by default (loopback; a real production LAN
deployment would bind a real interface -- tracked as future work, not
attempted in A15, matching the same "best-effort local IPv4" allowance
already used for the endpoint the pairing payload advertises to the
phone). See the security profile document for the full two-round-trip
wire flow and its atomicity guarantee.

## Discovery

`discovery.py` (`FriendSendDiscoveryService`) wraps a single
`zeroconf.Zeroconf`/`ServiceBrowser` pair (never one per UI widget, §69).
`zeroconf`'s own callbacks run on its internal thread; this class
converts them into queued function calls processed on one dedicated
dispatch thread before any subscriber callback runs, so a subscriber
never executes "under" `zeroconf`'s own locks (§115), and a bad
subscriber can never take down discovery (bare
`except Exception: pass` around each dispatched call, mirroring A13's
`DeviceHandoffService` subscriber isolation).

`FriendSendDiscoveryService` has no `trust_store` reference anywhere in
its implementation, by construction -- verified directly by
`test_discovery_alone_never_grants_trust_no_persistence_side_effect`,
which inspects the class's own source to assert this.

## Logging

No file in this package logs a raw pairing secret, private signing key,
or full challenge/signature transcript. Device/desktop identifiers may
appear in short form; see the RESULT document's redaction test coverage.
