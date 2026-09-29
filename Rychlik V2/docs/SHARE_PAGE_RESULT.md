# Server-Rendered Open Graph Share Page — Result (Prompt 09)

**The share page is local-only. No public tunnel exists yet. WhatsApp has
not yet been tested against this page.** This phase combines `ShareLink +
Artifact + SharePreview + LocalShareOrigin` into a real HTML page, still
served only from `127.0.0.1`.

## Architecture

Extended the existing `LocalShareOrigin` (Prompt 07) rather than creating a
second HTTP server — one lifecycle, one port, one binding policy:

```text
LocalShareOrigin
├── GET/HEAD /s/<share_id>       share page      (new, this phase)
├── GET/HEAD /preview/<share_id> thumbnail image (new, this phase)
└── GET/HEAD /media/<share_id>   binary media    (Prompt 07, unchanged logic)
```

New modules:

```text
src/rychlik/share/public_url_builder.py  — PublicUrlBuilder (§6/§7)
src/rychlik/share/share_page_renderer.py — render_share_page(), render_not_found_page()
```

`render_share_page()` is a **pure function** (string in, string out) — it
never touches the filesystem or the network, so the whole HTML-generation
and escaping logic is unit-testable without any HTTP server (see
`tests/test_share_page_renderer.py`, 18 tests). The HTTP handler in
`local_share_origin.py` only resolves `share_id -> ShareLink -> Artifact ->
SharePreview` through existing repositories and calls this pure function.

## Absolute URL strategy

`PublicUrlBuilder(base_url)` centralizes all three route constructions
(`share_page_url`, `preview_url`, `media_url`) — no string concatenation is
scattered elsewhere. `LocalShareOrigin.base_url_builder` derives
`http://<bound-host>:<bound-port>` from the actual bound socket address
after `start()`, unless an explicit `base_url` is passed to the constructor
(reserved for a future tunnel/public-domain phase — untouched by this
phase, no production domain is hardcoded anywhere).

## Page-state / HTTP status mapping

Documented policy, shared between `/s` and `/preview` (a deliberate choice:
one mental model for "is there anything to show for this share right now",
distinct from `/media`'s stricter ACTIVE-only Prompt 07 policy):

```text
ShareStatus.CREATING -> 200  ("Preparing your share...")
ShareStatus.ACTIVE   -> 200  (full page / actual preview image)
ShareStatus.OFFLINE  -> 503  ("sender is offline" message, no video element)
ShareStatus.EXPIRED  -> 410  ("This share has expired.")
ShareStatus.REVOKED  -> 410  ("This share has been revoked.")
ShareStatus.FAILED   -> 404  (kept 404 rather than 500 — "was never usable"
                              reads as not-found, not a server error)
unknown share_id      -> 404  (generic "does not exist" page/empty body)
```

`/media` keeps its own Prompt 07 mapping unchanged — only `ACTIVE` serves
bytes there; `CREATING` and `OFFLINE` are both `404`/`503` on `/media` even
though `/preview` may return `200` for `CREATING` (thumbnail already cached
locally, independent of whether the byte-serving session is open yet). This
was verified directly: `test_preview_denied_media_still_allowed_state_check`
asserts `/preview` returns 200 and `/media` returns 404 for the same
`CREATING` share.

## Open Graph metadata

Present in the **initial HTML response** — no JavaScript is used anywhere
on the page, so a crawler that never executes JS still sees:

```html
<meta property="og:title" content="...">
<meta property="og:description" content="...">
<meta property="og:type" content="website">
<meta property="og:url" content="...">
<meta property="og:image" content="...">          (only if a thumbnail exists)
<meta property="og:image:width" content="...">    (only if width/height known)
<meta property="og:image:height" content="...">
<meta property="og:image:type" content="image/jpeg">
```

`og:video` is explicitly **not** implemented — reserved for the later
isolated experiment (Prompt 14 in the research doc), per §5.

**Fallback when no thumbnail exists** (§11): `og:image` and the `<img>`/
`poster` attribute are simply omitted entirely (option B in the prompt was
rejected — no generic static placeholder asset exists yet, and inventing
one would be scope creep for this phase). The page still renders correctly
without it (verified: `test_og_image_absent_without_thumbnail`).

## HTML escaping

All dynamic text (`title`, `description`, generated URLs interpolated into
HTML) goes through Python's stdlib `html.escape()` — never a manual `<`/`>`
replace. Tested against `<script>...</script>`, an attribute-breakout
payload (`"><img src=x onerror=alert(1)>`), ampersands, and quotes, all at
the pure-renderer level (`test_share_page_renderer.py`). The equivalent
HTTP-level test could not use literal `<`/`>` filenames — see "Environment
limitation" below — so it exercises `&`/`"`/`'` instead, over real HTTP,
proving the exact same `render_share_page()` function is what the running
server calls.

## Security headers

Applied to `/s` and `/preview` responses:

```text
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'self'; img-src 'self' data:;
    media-src 'self'; script-src 'none'; base-uri 'none'; form-action 'none'
X-Robots-Tag: noindex, nofollow
Cache-Control: no-store
```

`<meta name="robots" content="noindex,nofollow">` is also present in the
HTML itself (belt-and-suspenders with the header). **Documented explicitly
per §37: noindex is advisory only, not an access-control mechanism** — the
actual protection is the unguessable `share_id` plus `ShareLink`'s
revoke/expiry lifecycle (Prompt 06).

No CSP nonce/hash infrastructure was needed since the page has zero inline
or external scripts (`script-src 'none'`) — enforced by construction (no JS
is written anywhere on this page), not merely by the header.

## Cache policy

`Cache-Control: no-store` on **both** `/s` and `/preview`, deliberately
simple for this phase (§26/§27 both allow starting simple; preview *could*
be made cacheable later since it's immutable per artifact+preview-version
identity, but that optimization is deferred — no ETag on the preview route
either, for the same reason. Documented as a known limitation, not an
oversight).

## Crawler safety (hard invariant, §17/§18)

`/s` and `/preview` are pure read-only lookups: `ShareLinkRepository.get()`
and `SharePreviewService.get_or_create_preview()` (cache-hit path — no
re-probe, no re-thumbnail on a page reload, per Prompt 08's existing cache).
Neither route ever writes to `ShareLinkRepository`, never changes
`ShareStatus`, and never touches the original video bytes to render the
page — `test_crawler_style_requests_have_no_side_effects` issues 10 repeat
requests across both routes and asserts the `ShareLink` status is bit-for-
bit unchanged afterward. A future real "viewer" concept (session/metrics)
can only ever be attached to `/media`, never to `/s` or `/preview` — this
phase makes that boundary structural, not just documented.

## Preview route

`GET/HEAD /preview/<share_id>` resolves `share_id -> ShareLink ->
artifact_id -> Artifact -> SharePreview` through the same repository
boundaries as everything else — the thumbnail's actual filesystem path
never appears in a URL or a request parameter. Correct `Content-Type`
(`preview.thumbnail_mime`, falling back to `image/jpeg`) and
`Content-Length`. Reuses `SharePreviewService`'s own cache-validity rules
unchanged (§13) — no second preview-integrity mechanism was invented; an
Artifact that changed after the preview was cached is caught by the same
`resolve_artifact_file()` check already shared with `/media` (Prompt 08).

## Environment limitation encountered (documented, not hidden)

Filenames containing literal `<`/`>` characters could not be created on
disk in this sandboxed test environment — `write_bytes()` on such a path
fails with `FileNotFoundError` below the Python/OS layer (verified this is
not a real ext4/tmpfs restriction; `<`/`>` are legal in Linux filenames).
The `<script>` XSS proof therefore lives entirely in
`test_share_page_renderer.py` (pure function, no filesystem), and the
HTTP-level escaping test uses `&`, `"`, `'` instead, which this environment
does allow on disk.

## Browser manual smoke test (§43) — NOT performed

`/usr/local/bin/chromium` and `/usr/local/bin/firefox` exist as command
names in this environment but are broken wrapper scripts pointing at
non-existent binaries (`chromium: /usr/bin/chromium: No such file or
directory`); no `playwright` package is installed either. A real browser
smoke test (open the page, confirm the native `<video>` element renders
and plays/seeks) was **not performed** — this is an explicit gap, not a
skipped-and-hidden step. Recommend the user do this manually before
Prompt 10 (open one of the `/s/<share_id>` URLs from the standalone E2E
script's printed output in an actual desktop browser).

## Tests

`224/224` passing total (all Prompt 01–08 tests remain green — the only
change to any existing test file was mechanical constructor wiring in
`test_local_share_origin.py`, now passing the required
`share_preview_service=` argument; no test assertion or behavior needed to
change). New: 7 (`test_public_url_builder.py`) + 18
(`test_share_page_renderer.py`) + 23 (`test_share_page_route.py`) = 48.

Covers: every `ShareStatus` → HTTP status mapping for both `/s` and
`/preview`, unknown share, UTF-8 title over real HTTP, HTML-escaping over
real HTTP, no local-path/no-source_url leakage, all required OG tags,
`og:image` presence/absence, robots header+meta, `Cache-Control`, security
headers, `HEAD` parity, preview 404 states (unknown/denied/no-thumbnail),
the `CREATING`-allows-preview-but-denies-media distinction, and the full
composition test (§41: real ffmpeg video → real acquisition-shaped Artifact
→ page HTML parsed for exact preview/media URLs → real preview image GET →
real ranged media GET, byte-exact).

## Real share-page E2E

In-suite: `test_full_stack_page_preview_and_ranged_media`. **Plus** a
standalone script run outside pytest
(`/tmp/.../e2e_check_share_page.py`, not committed) that printed the actual
rendered HTML (first 800 chars), fetched the preview image, and fetched
both a `Range` and a full `GET` of the media — output included below for
the record:

```text
GET http://127.0.0.1:<port>/s/<share_id>
status: 200 Content-Type: text/html; charset=utf-8
<!doctype html> ... <meta property="og:image" content=".../preview/...">
...

GET http://127.0.0.1:<port>/preview/<share_id>
status: 200 Content-Type: image/jpeg bytes: 6284

GET http://127.0.0.1:<port>/media/<share_id> Range: bytes=0-999
status: 206 bytes: 1000

E2E: real acquisition -> Artifact -> SharePreview -> ShareLink ACTIVE ->
LocalShareOrigin -> real HTTP GET /s + /preview + Range /media = OK
```

## Privacy check

Verified (both at renderer-unit level and HTTP-integration level): no
`local_path`, no `source_url`, no `secret`, no `artifact_id`, no filesystem
paths appear anywhere in `/s` or `/preview` responses. `to_public_dict()`
boundaries from Prompts 06/08 are what feed the renderer — the renderer
never receives the private fields in the first place, so there is nothing
to accidentally leak.

## Known limitations (explicitly deferred, not hidden)

```text
no public HTTPS / no production domain (still 127.0.0.1-only)
no real WhatsApp/Telegram preview validation (Prompt 13 in the research doc)
no og:video (deliberately deferred, own future experiment)
no browser manual smoke test performed (environment has no working browser)
no generic static placeholder image when a share has no thumbnail (omitted instead)
no ETag / conditional GET on the preview route
Cache-Control is uniformly no-store (a more aggressive, safe preview cache
  policy is deferred pending the public URL / tunnel strategy)
minimal native <video> element only — no custom controls, no captions, no
  quality selector (this is not the final Web Player phase, Prompt 10 is)
no WebRTC, no relay, no cloud, no FriendSend Android
no final graphical design (still functional-skeleton scope)
```

## ACCEPTANCE GATE

```text
[x] all previous 176 tests remain green (224 total after this phase)
[x] all new HTML/preview/security tests pass
[x] non-mock local share-page E2E passes (in-suite + standalone script)
[x] HTML injection tests pass (renderer-level <script>/attribute-breakout;
    HTTP-level &/"/' — see documented environment limitation above)
[x] no secret/local path/source URL leak
[x] crawler requests have no side effects (explicit test)
[x] Range media still byte-exact
[ ] browser manual smoke — NOT performed (no working browser in this
    environment; documented gap, recommended as a manual follow-up)
[x] worktree clean (after commit)
```

---

PHASE: Prompt 09 — Server-Rendered Open Graph Share Page

STATUS: DONE (browser manual smoke explicitly not performed — see above)

BASELINE COMMIT: 4079789

FILES CHANGED:
```text
src/rychlik/share/public_url_builder.py    (new)
src/rychlik/share/share_page_renderer.py   (new)
src/rychlik/share/local_share_origin.py    (extended: /s, /preview routes,
                                             mandatory share_preview_service
                                             constructor arg, base_url override)
tests/test_public_url_builder.py           (new)
tests/test_share_page_renderer.py          (new)
tests/test_share_page_route.py             (new)
tests/test_local_share_origin.py           (mechanical: pass share_preview_service)
docs/SHARE_PAGE_RESULT.md                  (new, this file)
```

ROUTES ADDED: `GET/HEAD /s/<share_id>`, `GET/HEAD /preview/<share_id>`.

PAGE STATE MAPPING: see table above.

OPEN GRAPH: title, description, type, url, image (+width/height/type when
available). No og:video.

SECURITY HEADERS: X-Content-Type-Options, Referrer-Policy, CSP
(script-src 'none'), X-Robots-Tag, Cache-Control: no-store.

HTML ESCAPING: stdlib `html.escape()`, tested against script tags,
attribute-breakout payloads, ampersands, quotes, unicode.

PREVIEW ROUTE: `/preview/<share_id>`, reuses SharePreviewService cache
validity as-is, correct Content-Type/Length, same status-code table as `/s`.

TESTS ADDED: 48 (7 + 18 + 23)

TESTS RUN: 224/224 passing

REAL SHARE-PAGE E2E: pass (in-suite + standalone script outside pytest)

REAL BROWSER SMOKE: NOT performed (no functional browser binary in this
sandboxed environment) — recommended as a manual follow-up before Prompt 10.

PRIVACY CHECK: pass — no local path/source_url/secret/artifact_id in any
`/s` or `/preview` response.

KNOWN LIMITATIONS: see above — none hidden.

GATE: PASS (with one explicitly unperformed optional item, not a blocker
per §43's own wording: "This is optional for Prompt 09")

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt 10 — Minimal Cross-Platform Web Player is
largely already exercised by this phase's native `<video>` element and
real-HTTP Range playback; the more valuable next step is likely
Prompt 12 — Development Tunnel Experiment, to validate the whole stack is
reachable from outside localhost before investing further in player
polish. Recommend deciding this explicitly rather than defaulting to the
numeric order.

COMMIT: (recorded after this phase's commit)
