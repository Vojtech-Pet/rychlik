# Local Share Origin — Result (Prompt 07)

**This server is local-only. No public internet exposure exists yet.**
Binds to `127.0.0.1` only; port is OS-assigned (`port=0`) unless overridden.
No public tunnel, TLS termination, web page, or Open Graph metadata exists
in this phase.

## Architecture

```text
Artifact (Prompt 02)
ShareLink (Prompt 06, via ShareLinkRepository)
        ↓
LocalShareOrigin (this phase)
├── ArtifactRepository   (new — see "Required contract change" below)
├── ShareLinkRepository  (reused from Prompt 06)
├── range_parser.parse_range()  (pure function)
└── _Handler (BaseHTTPRequestHandler, per-request)
```

`LocalShareOrigin` only resolves and serves; it does not create ShareLinks,
does not run acquisition, does not transcode, and owns no GUI state.

## Required contract change

Before this phase, no `Artifact` was ever persisted anywhere — it was
constructed ad hoc and passed directly into a `ShareRequest` for the
duration of one call. `LocalShareOrigin` needs to resolve `artifact_id ->
Artifact` for HTTP requests arriving later, out of band from creation, so a
minimal `ArtifactRepository` (`src/rychlik/core/artifact_repository.py`) was
added, mirroring `ShareLinkRepository`'s shape (`Protocol` +
`InMemoryArtifactRepository`). No existing Artifact/ShareLink contract was
changed.

## HTTP routes

```text
GET  /media/<share_id>
HEAD /media/<share_id>
```

`share_id` is matched against `^/media/([A-Za-z0-9_-]+)$` on the URL path
only (query string is split off and ignored). It is used exclusively as a
repository dictionary key — it never touches the filesystem, so there is no
path-traversal surface by construction, not merely by validation. Anything
else (`/`, `/media/`, extra segments, encoded slashes, `..`, absolute-path
style ids) fails the regex and returns `404`.

## HTTP status mapping (documented policy)

```text
unknown / malformed share_id           -> 404
ShareStatus.CREATING                   -> 404  (not yet active; avoid state oracle)
ShareStatus.OFFLINE                    -> 503
ShareStatus.EXPIRED                    -> 410
ShareStatus.REVOKED                    -> 410
ShareStatus.FAILED                     -> 404
ShareStatus.ACTIVE + artifact missing
  from ArtifactRepository              -> 410
ShareStatus.ACTIVE + file missing/
  changed on disk (ArtifactChanged)    -> 410
unsatisfiable Range                    -> 416, Content-Range: bytes */<size>
unexpected OSError while resolving file -> 500 (no details in body)
```

All error responses have an empty body and `Content-Length: 0` — no local
paths, `source_url`, secrets, or stack traces are ever included.

## Range semantics

Implemented in `src/rychlik/share/range_parser.py` as a pure function,
independent of the HTTP handler:

```text
no Range header                -> full 200
"bytes=" prefix missing/wrong  -> ignored, full 200  (RFC 7233 permits this)
multiple ranges (comma list)   -> ignored, full 200  (multipart explicitly
                                   out of scope for this phase — documented
                                   limitation, not a bug)
malformed syntax                -> ignored, full 200
start > end                     -> 416
start >= size                   -> 416
zero-length suffix ("bytes=-0") -> 416
Range against a 0-byte file     -> 416 (any range)
end beyond EOF but start valid  -> clamped to size-1, served as 206
                                    (RFC-permitted clamp, not a silent fix
                                    of an actually-invalid range)
otherwise                       -> 206 with exact byte-inclusive range
```

Digit strings longer than 19 characters are treated as malformed (ignored)
rather than parsed, to avoid pathological-length integer parsing.

## ETag policy

`ETag: "<artifact.sha256>"` — computed once at Artifact-creation time
(Prompt 02), never recomputed per request. Stable for the lifetime of the
Artifact object; changes only if a new Artifact is constructed for the same
file.

## Content-Type

`artifact.mime_type`, falling back to `application/octet-stream`. Never
derived from request input.

## Artifact-change detection

At request time (not once at share-activation time), `_resolve_artifact_file`:

1. `artifact.local_path.resolve(strict=True)` — raises if the path no
   longer exists.
2. Requires the resolved path to be a regular file.
3. Compares current `stat().st_size` against `artifact.size` recorded at
   Artifact-construction time.

Any mismatch raises `ArtifactChanged` internally, mapped to `410 Gone`. This
is a **per-request** check, not a one-time check at activation — so a file
deleted/truncated/replaced after activation is caught on the next request.

**Guarantee is not TOCTOU-proof**: no filesystem read is transactional. The
check happens immediately before opening the file for streaming, which
narrows but does not eliminate the race window between the check and the
`open()` call. This is the same limitation any local HTTP file server has;
documented here rather than hidden.

## Symlink policy

`local_path.resolve(strict=True)` follows symlinks to their real target and
requires the result to be a regular file. This is the resolution the
Artifact's path already pointed to at Artifact-construction time — no
separate symlink-swap detection exists beyond the size check above. Full
sandboxing (e.g. rejecting symlinks outright, chroot) is out of scope.

## Revoke-during-stream behavior

Authorization (`ShareStatus == ACTIVE`) is checked once, at the start of
each request. An already-in-progress response, once headers are sent, is
allowed to finish. A `revoke()` call only affects **new** requests made
after it takes effect. Immediate mid-stream termination of an already-open
response is explicitly deferred — not implemented, not tested here.

## Binding / port allocation

- Host: `127.0.0.1` by default, injectable for tests (still local-only in
  practice — no test binds to `0.0.0.0`).
- Port: `0` (OS-assigned) by default; actual bound address exposed via
  `LocalShareOrigin.address`.

## Server lifecycle

`start()` / `stop()` are both idempotent (calling either twice is a no-op
the second time) and guarded by a lock. `stop()` calls `shutdown()` +
`server_close()` and joins the serving thread (5s timeout) before clearing
internal state, so tests do not leak server threads. `serve_forever` uses a
`poll_interval=0.05` so `stop()` returns quickly (measured: full new test
file went from ~17s to ~2.2s after this change — no functional effect).

## Concurrency

`ThreadingHTTPServer` with `daemon_threads = True` — each connection is
handled on its own thread, so a client issuing `HEAD` then a later `Range`
GET (as a real browser does) is naturally supported. No custom worker-pool
tuning was added.

## Streaming / memory

`_stream_file()` reads in `64 KiB` chunks via `handle.read(min(chunk,
remaining))` and writes each chunk to the response — the full file is never
read into memory, verified by a dedicated test with a 300 KB file and a
Range request spanning multiple internal chunk boundaries
(`test_range_spanning_multiple_internal_chunks`). `BrokenPipeError` /
`ConnectionResetError` during streaming (client disconnect) are caught and
the request simply ends; the file handle is always closed via a `with`
block regardless of outcome.

## Logging

Structured `logging.getLogger("rychlik.share_origin")` calls include only
`share_id`, `status`/`http_status`, `range_start`, `bytes`, and `result` (a
short machine tag like `served`, `denied_revoked`, `artifact_changed:...`).
Never logs `secret`, cookies, full filesystem paths, or `source_url`. The
default `BaseHTTPRequestHandler` stderr access log is disabled
(`log_message` overridden to a no-op).

## Tests

`125/125` passing total (all Prompt 01–06 tests remain green — **no test
needed to be loosened or reinterpreted this phase**, unlike Prompt 06 where
one regression test's assumption was wrong). New: 25 in
`test_range_parser.py`, 35 in `test_local_share_origin.py`.

Security tests: path-traversal attempts (`..%2F`, doubly-encoded, literal
`..` segments, `//`-prefixed), unknown/malformed share ids, no directory
listing at `/` or `/media/`, localhost-only binding assertion.

Artifact-mutation tests: file deleted, truncated, replaced with different
size, and removed from the repository entirely — all → `410`.

Real, non-mock, TCP/HTTP local E2E: `test_real_end_to_end_head_and_range_get`
in the test suite, **plus** a standalone script run outside pytest
(`/tmp/.../e2e_check_origin.py`, not committed) that ran a real acquisition
against the local HTTP fixture server, built a real `Artifact`, created and
activated a real `ShareLink`, started `LocalShareOrigin`, and issued real
`requests.head()`/`requests.get()` calls with a `Range` header — output
confirmed exact byte match and correct status codes.

## Known limitations (explicitly deferred, not hidden)

```text
no multipart/multi-range responses (rejected as unsupported, served as full 200)
no public tunnel (Cloudflare/Tailscale) — local only
no TLS termination
no web page / Open Graph metadata / thumbnail
no WebRTC / TURN / relay
no cloud storage
no immediate termination of an already-open response after revoke
no full TOCTOU-proof file-identity guarantee (documented, not solved)
InMemoryArtifactRepository / InMemoryShareLinkRepository are process-local,
  non-persistent
```

## ACCEPTANCE GATE

```text
[x] all previous 65 tests remain green (now 90 with range parser, 125 total)
[x] all new Range/server/security tests pass
[x] non-mock local HTTP E2E passes (in-suite + standalone script)
[x] 0 arbitrary filesystem access (share_id never used as a path)
[x] 0 inactive/revoked share byte access (parametrized denial test over
    every non-ACTIVE status)
[x] Range responses are byte-exact (KNOWN_BYTES fixture + 300KB multi-chunk test)
[x] worktree clean (after commit)
```

---

PHASE: Prompt 07 — Local Range-Capable Share Origin

STATUS: DONE

BASELINE COMMIT: 4ee254a

FILES CHANGED:
```text
src/rychlik/core/artifact_repository.py   (new)
src/rychlik/share/range_parser.py         (new)
src/rychlik/share/local_share_origin.py   (new)
tests/test_range_parser.py                (new)
tests/test_local_share_origin.py          (new)
docs/LOCAL_SHARE_ORIGIN_RESULT.md         (new, this file)
```

HTTP ROUTES: `GET/HEAD /media/<share_id>` only.

RANGE SUPPORT: single range (exact/open-ended/suffix), 206/416, clamped
over-long ranges, multi-range explicitly rejected as unsupported (served as
full 200).

SECURITY TESTS: path traversal (encoded and literal), unknown/malformed
share ids, no directory listing, localhost-only binding — all pass.

ARTIFACT MUTATION TESTS: deleted, truncated, size-changed, removed from
repository — all → 410, all pass.

TESTS ADDED: 60 (25 range parser + 35 local share origin)

TESTS RUN: 125/125 passing

REAL HTTP E2E: pass (in-suite `test_real_end_to_end_head_and_range_get` +
standalone script outside pytest)

MEMORY/STREAMING CHECK: pass — chunked (64 KiB) read/write throughout, no
full-file `.read()` anywhere, verified with a 300 KB multi-chunk-boundary
Range test.

KNOWN LIMITATIONS: see above (multipart ranges, TLS, TOCTOU, revoke
mid-stream, no persistence) — none hidden.

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt 08 — Share Preview Artifact

COMMIT: (recorded after this phase's commit)
