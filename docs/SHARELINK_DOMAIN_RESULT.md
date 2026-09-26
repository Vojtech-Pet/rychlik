# ShareLink Domain Model — Result (Prompt 06)

Domain-only phase. No public URL infrastructure exists yet. No media is
externally reachable yet. Real network access begins in Prompt 07.

## Implemented types

```text
src/rychlik/share/share_link.py
├── ShareLink (frozen dataclass)
├── AccessPolicy        (ANYONE_WITH_LINK only, for now)
├── TransportPolicy      (UNRESOLVED, LIVE — production values deferred)
├── InvalidShareLinkTransition (exception)
├── TERMINAL_STATUSES
└── generate_share_id()  (secrets.token_urlsafe(16), 128 bits)

src/rychlik/share/share_link_repository.py
├── ShareLinkRepository (Protocol)
└── InMemoryShareLinkRepository

src/rychlik/share/share_link_service.py (rewritten)
└── ShareLinkService: create_link, get_link, activate, mark_offline,
    expire, mark_failed, revoke_link
```

`ShareStatus` (CREATING/ACTIVE/OFFLINE/EXPIRED/REVOKED/FAILED) was **not**
duplicated — it already existed in `share/contracts.py` from Prompt 03 and is
reused directly, per the "do not introduce parallel duplicate domain models"
instruction.

## State machine

```text
CREATING → ACTIVE
CREATING → FAILED

ACTIVE → OFFLINE
ACTIVE → EXPIRED
ACTIVE → REVOKED
ACTIVE → FAILED

OFFLINE → ACTIVE
OFFLINE → EXPIRED
OFFLINE → REVOKED
OFFLINE → FAILED

EXPIRED, REVOKED, FAILED → (terminal, no outgoing transitions)
```

`ShareLink.transition_to()` is a pure function returning a new `ShareLink`;
invalid transitions raise `InvalidShareLinkTransition` and the caller
(`ShareLinkService`) reports the previous status plus an error string via
`ShareResult`, leaving stored state untouched.

**Idempotency policy:** `revoke_link()` on an already-`REVOKED` link returns
success (`ShareResult(status=REVOKED, error=None)`) without re-entering the
transition machinery. All other terminal-state operations are rejected as
invalid transitions (not silently accepted).

## Access policy / transport policy

- `AccessPolicy.ANYONE_WITH_LINK` is the only value implemented; the enum is
  open to future values (`PASSWORD`, `ONE_TIME`, etc.) without touching
  `ShareLink`'s shape.
- `TransportPolicy` has `UNRESOLVED` (default) and `LIVE`. Production values
  for WebRTC/TURN/relay/temp-cloud are explicitly deferred — `ShareLink` is
  structurally independent of any transport implementation.

## Serialization boundary

`ShareLink.to_public_dict()` exposes exactly:

```text
share_id, status, public_url, expires_at
```

It never exposes `secret` or `artifact_id`. `secret` is also excluded from
`repr()` (`field(repr=False)`) so it can't leak through debug logging.
`ShareLink` structurally never holds an `Artifact`, `local_path`, or
`source_url` — it only stores the opaque `artifact_id` string — so there is
no code path by which Artifact-private fields could leak through a
ShareLink.

## Security considerations

- `generate_share_id()` uses `secrets.token_urlsafe(16)` — 128 bits,
  cryptographically secure, no relation to filename/path (tested).
- Timestamps (`created_at`, `expires_at`) must be timezone-aware; naive
  datetimes are rejected in `__post_init__` (tested).
- Expiry evaluation (`is_expired(now)`) takes `now` as a parameter rather
  than reading the wall clock internally, keeping tests deterministic and
  avoiding hidden global-clock coupling.

## GUI wiring

`ShareDialog.link_button` now calls `ShareLinkService.create_link()` and
shows a functional placeholder: `"Share link created\nStatus: CREATING"` (or
a failure message). No URL, no QR, no styling — exactly per Prompt 06 §16.

## Tests

`65/65` passing (all Prompts 01–04.5 tests remain green; 32 new tests added
across `test_share_link.py`, `test_share_link_service.py`, plus one new
`ShareDialog` test and a fix to the Prompt 05 regression test — see below).

New coverage includes: id entropy/uniqueness/unrelatedness, construction
validation, every listed transition, invalid-transition rejection, terminal
states (parametrized over all three), idempotent revoke, expiry with/without
`expires_at`, timezone-awareness enforcement, public-dict safety, secret
redaction in repr, unknown-share-id handling for every service method, and
empty-artifact_id rejection in `create_link`.

## Known limitations

- Domain-only: no HTTP server, no public URL allocation logic, no QR, no
  Cloudflare Tunnel, no FriendSend Android — all deferred to Prompt 07+.
- `InMemoryShareLinkRepository` is process-local and non-persistent; a real
  backing store is a future concern behind the same `ShareLinkRepository`
  protocol.
- No audit trail of transition history is stored (the `now` passed to
  `transition_to()` is currently unused beyond validation — reserved for a
  future audit log).
- `ShareDialog`'s Link Mode wiring is a placeholder status label only; no
  way yet to see/copy a URL because none is allocated in this phase.

## Regression note

One pre-existing test (`test_prompt05_device_link_isolation.py::
test_link_mode_remains_usable_when_device_mode_disabled`) asserted a direct
`CREATING → REVOKED` transition, which the Prompt 06 state machine correctly
rejects (not in the approved transition table). Fixed by activating the link
before revoking it, matching a realistic lifecycle, rather than loosening the
state machine.

## ACCEPTANCE GATE

```text
[x] all existing tests green
[x] all new ShareLink tests green
[x] no secret/public serialization leak
[x] invalid transitions rejected
[x] Device Mode remains independent
[x] worktree clean (after commit)
```

---

PHASE: Prompt 06 — ShareLink Domain Model

STATUS: DONE

FILES CHANGED:
```text
src/rychlik/share/share_link.py                    (new)
src/rychlik/share/share_link_repository.py          (new)
src/rychlik/share/share_link_service.py             (rewritten)
src/rychlik/gui/share_dialog.py                     (wired Link Mode placeholder)
tests/test_share_link.py                            (new)
tests/test_share_link_service.py                    (new)
tests/test_share_dialog.py                          (added placeholder test)
tests/test_prompt05_device_link_isolation.py         (fixed CREATING->REVOKED assumption)
docs/SHARELINK_DOMAIN_RESULT.md                      (new, this file)
```

DOMAIN TYPES ADDED: ShareLink, AccessPolicy, TransportPolicy,
InvalidShareLinkTransition, ShareLinkRepository, InMemoryShareLinkRepository

STATE TRANSITIONS: see table above

TESTS ADDED: 33 (24 in test_share_link.py, 8 in test_share_link_service.py, 1
in test_share_dialog.py)

TESTS RUN: 65/65 passing

RESULT: ShareLink domain model and lifecycle rules implemented; no network,
no transport, no public URL infrastructure.

SECURITY CHECK: pass (entropy, no filename relation, no secret/artifact_id
leak in public serialization, secret redacted from repr, timezone-aware
timestamps enforced)

KNOWN LIMITATIONS: see above

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt 07 — Local Range-Capable Share Origin

COMMIT: (recorded after this phase's commit)
