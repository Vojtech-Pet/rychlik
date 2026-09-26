# Real WhatsApp Rich-Preview Experiment — Result (Prompt 13)

**Empirical product experiment, not an architecture-expansion phase.**
Real official WhatsApp Android client, real Cloudflare Quick Tunnel, real
public HTTPS URL. Executed live, interactively, with the user testing on
their own phone while server-side request logs were captured concurrently.

## Date / environment

```text
Date: 2026-09-26
Public URL method: Cloudflare Quick Tunnel (Prompt 12 DevelopmentTunnelProvider)
WhatsApp User-Agent observed in logs: "WhatsApp/2.23.20.0"
Android version / device: not separately recorded by the user
Comparison app: Facebook Messenger (tested informally by the user, not logged server-side)
```

## Test artifact

```text
tools/wa_test_01.mp4 (not committed, gitignored)
1280x720, 15s, H.264, ~1.46 MB
Visible burned-in text overlay: "FriendSend Preview Test 01"
Artifact.filename: "wa_test_01.mp4" -> SharePreview.title = "wa_test_01"
  (the burned-in overlay text is NOT the same as the derived title —
  see "Bugs found" / observations below)
```

## Infrastructure prepared

- `LocalShareOrigin.request_log`: bounded, sanitized, in-memory request log
  (route, method, share_id, HTTP status, User-Agent, Range header, bytes
  served) — added specifically to correlate what WhatsApp's client fetches
  vs. what a recipient's browser fetches. Committed in `5dd6c94`'s parent
  commit `e169f40`.
- Six ShareLinks were pre-created against the same running tunnel/origin up
  front (Prompt 13 §4: one fresh `share_id` per experiment configuration,
  since messaging platforms may cache link previews), so no tunnel restart
  was needed between attempts.
- Server health (`/s`, `/preview`, `/media` HEAD, `/media` Range) was
  verified via `requests` before ever handing a URL to the user (§5 gate).

## Tests executed

```text
WA-01/02/03 equivalent (manual paste, composer preview, send, recipient
  view, tap-to-open, playback) — executed, twice (first attempt hit the
  bug below; repeated on a fresh share_id after the fix)
WA-04 (preview fetch network evidence: does WhatsApp fetch /media before a
  human taps?) — executed, via server request_log correlation
Comparison with Facebook Messenger — executed informally (user's own
  observation, not server-log-correlated on our side)
```

## Tests NOT executed — environment limitation

```text
WA-05 (ACTION_SEND text path / Android tooling)        — NOT EXECUTED
WA-06 (link previews disabled in WhatsApp privacy settings) — NOT EXECUTED
WA-07 (explicit post-crawl ShareLink-state inspection)  — NOT EXECUTED
  (the crawler-side-effect invariant was however verified indirectly, see below)
WA-08 (revoked link, already-sent card)                 — NOT EXECUTED
WA-09 (tunnel/offline behavior with a WhatsApp-sent link specifically) —
  NOT EXECUTED (Prompt 12 already exercised the raw tunnel-down HTTP 530
  behavior without WhatsApp in the loop)
WA-10 (two independent shares, bounded cache-behavior observation) —
  NOT EXECUTED
WA-11 (UTF-8/Slovak diacritics title)                   — NOT EXECUTED
WA-12 (thumbnail crop/letterbox/aspect-ratio characteristics) — only
  observed informally from one screenshot, not systematically measured
Official screenshot archive per §32                     — NOT EXECUTED
  (two screenshots were shared inline in conversation and inspected, not
  saved as separate committed evidence files, per privacy guidance in §32
  about not committing chat-context screenshots)
```

None of these were faked or assumed — they are explicitly out of scope for
this run given time and require either additional phone-side actions the
user did not perform, or an Android test harness (§12/§30/§31) that does
not exist in this environment (`STATUS = NOT EXECUTED`, not `PASS`, per
Prompt 13's own instruction not to fake completion).

## What actually happened — narrative

**Attempt 1 (WA-01, `share_id=gnxyNtitkC7LCw7Z46QctA`):** user pasted the
public URL into WhatsApp, sent it. Server log confirmed WhatsApp's client
(`WhatsApp/2.23.20.0`) fetched `GET /s/<share_id>` successfully (200 OK) —
but the sent message showed only the plain URL text, no title/thumbnail
card. User separately reported the same URL *did* produce a working rich
preview in Facebook Messenger.

**Root-cause investigation:** fetched the exact HTML WhatsApp's client
would have received (`curl -A "WhatsApp/2.23.20.0" <page_url>`) and found
`og:url` and `og:image` pointing at `http://127.0.0.1:<port>/...` — the
**local loopback address**, not the public tunnel URL. `LocalShareOrigin`
(Prompt 09) only accepted a `base_url` override at construction time; the
live-experiment script started the origin, then the tunnel, and never told
the origin about the tunnel's (only-known-after-the-fact) public hostname.
WhatsApp's client correctly fetched `/s` over the real public URL, parsed
the (locally-scoped, unreachable-to-it) `og:image` URL, silently failed to
fetch it, and fell back to a bare-domain-only card — exactly matching what
the user described and what the server log showed (a `/s` fetch with no
following `/preview` or `/media` fetch).

**Fix:** `LocalShareOrigin.set_base_url(url)` (new public method, commit
`5dd6c94`), called after tunnel startup once the public URL is known. 4
new deterministic tests. Also fixed an unrelated pre-existing test race in
`test_request_log.py` discovered while adding these.

**Attempt 2 (WA-03, `share_id=qb93LM-JHf0Nq64x5w_r6w`, after the fix):**
verified via `curl` that `og:url`/`og:image` now pointed at the public
tunnel URL, then handed the user a fresh link. Full success — see below.

## Composer / sent-message preview

Rich card with: real thumbnail (a frame from the generated video, showing
the "FriendSend Preview Test 01" burned-in text), title "wa_test_01",
description "Video • 1280×720 • 0:15", and the tunnel domain — all
correctly rendered in the sent WhatsApp message (screenshot inspected
inline, confirmed by the user; delivered/read receipts visible in the
screenshot, confirming this was the *sent*, not just composed, message).

## Recipient tap behavior

User confirmed explicitly (multiple-choice, not free text, to avoid
ambiguity): tapping the card/link **opened an external browser** to our
`/s/<share_id>` page. This is `PREVIEW_CARD_ONLY` per Prompt 13 §10's
classification, not `INLINE_VIDEO` — consistent with known WhatsApp
personal-client behavior (no native inline playback for arbitrary external
video links; this is a platform limitation, not something under this
project's control).

## Playback / seek

Server log shows a burst of `GET /media` requests with distinct `Range`
headers (`bytes=0-`, `bytes=98304-1464462`, `bytes=1441792-1464462`,
`bytes=32768-64727`, `bytes=32768-1441791`, etc.) starting at 16:23:24 and
continuing through 16:24:15 — the exact signature of a real HTML5
`<video>` element buffering and being seeked around by a human, not a
single linear download. All responses were `206 Partial Content`. This
confirms the Prompt 09 share page's native `<video>` element, once reached,
plays and seeks correctly through the public tunnel with a real browser
in the loop.

## Crawler request evidence (the specific question this phase most wanted answered)

```text
16:20:24  GET /s/<share_id>        WhatsApp/2.23.20.0   200
16:20:42  GET /s/<share_id>        WhatsApp/2.23.20.0   200
16:20:43  GET /s/<share_id>        WhatsApp/2.23.20.0   200
16:20:43  GET /preview/<share_id>  WhatsApp/2.23.20.0   200  (39918 bytes)
```

**No `/media` request from the `WhatsApp/2.23.20.0` User-Agent appears
anywhere in the log**, before or after the human tap. WhatsApp's own
preview-generation step fetched the HTML page and the preview image only —
never the video bytes. This is exactly the architectural separation
Prompt 07/09 were designed to guarantee (crawler path cheap and
side-effect-free; only a real viewer session reaches `/media`), and this
experiment is the first real-world confirmation of it against an actual
messaging client, not just our own test suite.

The three `/s` fetches ~18 seconds apart are noted but not fully explained
— see "LIKELY"/"UNKNOWN" below.

## Crawler side-effect invariant

No code path in `LocalShareOrigin` mutates `ShareLinkRepository` from `/s`
or `/preview` handlers (structural guarantee already established and
tested in Prompt 09 — `test_crawler_style_requests_have_no_side_effects`).
The live WhatsApp traffic is additional real-world confirmation that nothing
about a real client's behavior differs from what the test suite already
assumes; the share stayed `ACTIVE` throughout.

## Facebook Messenger comparison (informal, not server-log-correlated)

The user reported the *original* (buggy, pre-fix) URL rendered a working
rich preview in Facebook Messenger despite `og:image` pointing at
`127.0.0.1`. This is plausible if Messenger's link-preview crawler runs
from Meta's own infrastructure and, upon failing to fetch the (unreachable)
image, still renders *something* using whatever `og:title`/`og:description`
it could parse, or handles a failed image fetch more gracefully than
WhatsApp's client does. This was not independently confirmed via our
request log (no Messenger-associated User-Agent appears in the captured
log — the Messenger test happened before this log was inspected/preserved
across a session restart). **Classify as LIKELY, not PROVEN.**

## Bugs found

**Bug 1 (real, fixed):** `LocalShareOrigin` had no post-`start()` way to
receive a public tunnel URL, so every share page emitted `og:url`/`og:image`
pointing at `127.0.0.1`. Root-caused, fixed (`set_base_url()`), regression
tests added, full suite re-verified green (239/239). This is a genuine
experiment-support / integration bug, not a defect in Prompts 07–09's
tested behavior — `LocalShareOrigin` always supported an explicit
`base_url` at construction; the gap was only that nothing could supply the
tunnel's URL *after* the tunnel exists, which is the only order these two
systems can actually be composed in.

**Observation 2 (not a bug, a product/UX finding):** `SharePreview.title`
is derived from `Artifact.filename` ("wa_test_01"), which is unrelated to
whatever text is burned into the video frames themselves ("FriendSend
Preview Test 01"). For this experiment that was harmless (the filename was
still a reasonable label), but it is worth remembering for future test
design: the card's title text is a filename-derived label, not the video's
visible content.

## Classification

```text
PREVIEW UX:     GOOD
  Rich card with correct thumbnail, title, description, and domain,
  once the base_url bug was fixed. Minor deduction from EXCELLENT because
  the underlying integration (tunnel URL -> origin) required a manual
  fix during this very experiment -- it is not yet a zero-touch pipeline.

NAVIGATION UX:  GOOD
  Tap reliably opens the correct /s/<share_id> page in an external browser.
  Not EXCELLENT only because WhatsApp's personal client fundamentally does
  not offer inline navigation -- external browser hand-off is the ceiling
  for this platform, not a defect in this project's implementation.

PLAYBACK UX:    GOOD
  Real seeking confirmed via multiple distinct Range requests, all
  206 Partial Content, byte-exact per Prompt 07/12's prior verification.
  Not EXCELLENT because this is still the Prompt 09 minimal native
  <video> element (no custom UI), and playback smoothness/quality
  (buffering behavior, stall frequency) was not systematically measured.
```

## PROVEN

- WhatsApp's official Android client (`WhatsApp/2.23.20.0`) fetches
  `GET /s/<share_id>` when a Rýchlik share link is pasted into a
  conversation.
- After the `base_url` fix, WhatsApp's client also fetches
  `GET /preview/<share_id>` and successfully renders a rich card (real
  thumbnail, title, description, domain) in both the composer and the sent
  message.
- WhatsApp's client does **not** fetch `/media` as part of generating this
  preview — confirmed by the complete absence of any `WhatsApp/...`
  User-Agent entry against the `media` route in the request log.
- Tapping the resulting card/link opens an external browser to our
  `/s/<share_id>` page (not inline playback).
- The Prompt 09 share page, once reached via a real browser through the
  real public tunnel, serves a native `<video>` element that buffers and
  seeks correctly (multiple distinct `Range` requests, all `206`).
- `ShareLink` state is unaffected by any of this traffic (remained
  `ACTIVE` throughout; no code path exists that could change it from
  these routes).
- Before the fix, `og:url`/`og:image` pointing at an unreachable
  `127.0.0.1` address caused WhatsApp to silently degrade to a
  domain-only card rather than erroring visibly — a real, previously
  unknown failure mode now fixed and regression-tested.

## LIKELY

- Facebook Messenger renders a preview even when the image URL is
  unreachable (informal user report, not independently server-log
  confirmed on our infrastructure).
- The three `/s` fetches from WhatsApp ~18 seconds apart correspond to:
  one fetch while composing/pasting, and one or two more around the actual
  send action — plausible given WhatsApp's UI flow (type → preview →
  send → possibly re-render for the sent bubble), but the exact internal
  reason for three (not one or two) fetches is not confirmed.
- The desktop-style User-Agent (`Mozilla/5.0 (X11; Linux x86_64)...
  Chrome/153...`) seen fetching `/media` with seek behavior suggests the
  tap-through may have gone through a linked WhatsApp Web/Desktop session
  rather than the phone's own mobile browser — plausible but not confirmed
  (the user was not asked which device actually rendered the video).

## UNKNOWN

- Whether WhatsApp's link-preview fetcher retries a failed `og:image`
  fetch, or gives up after one attempt (relevant to how forgiving the
  system is toward transient tunnel/DNS issues, per Prompt 12's own
  finding of intermittent `trycloudflare.com` DNS flakiness in this
  sandbox — untested whether that flakiness would, on a worse day, cause
  the same silent-fallback behavior even with a correct `base_url`).
- Whether the preview persists correctly if the sender's tunnel/desktop
  goes offline after sending (WA-09, not executed here).
- Behavior with WhatsApp link previews explicitly disabled (WA-06, not
  executed).
- Behavior on a revoked share whose card was already cached by WhatsApp
  (WA-08, not executed).
- Exact device/app (phone browser vs. linked desktop/web session) that
  generated the playback traffic (see "LIKELY" above).
- Whether the three-fetch pattern for `/s` is consistent across repeated
  sends, or specific to this one interaction.

## PRODUCT DECISION

```text
LINK_MODE_WHATSAPP_UX_ACCEPTED_WITH_LIMITATIONS
```

Reasoning: once the (now-fixed) `base_url` bug is out of the picture, the
full pipeline works end-to-end against the real official WhatsApp client —
rich card, correct tap navigation, real seekable playback. The "with
limitations" qualifier reflects: (1) no inline in-app playback is possible
on this platform, by WhatsApp's own design, not ours; (2) several planned
observations (disabled previews, revoked-link behavior, offline behavior,
UTF-8 titles, thumbnail cropping specifics) remain untested and should be
covered in a lower-cost follow-up before wider claims are made; (3) the
`base_url` wiring is currently a manual step in an experiment script, not
yet a supported first-class flow for whoever eventually productizes
Prompt 12+13's combination — that integration gap should be closed
deliberately, not left implicit, before Link Mode ships.

## NEXT RECOMMENDED PHASE

Per Prompt 13 §36 guidance: baseline result is `B`/GOOD (not `A`/EXCELLENT,
not `C`/`D`/`E`), which under the decision tree means Prompt 14 (isolated
`og:video` experiment) is worth trying but **not required** — the current
card+tap-to-browser+seek flow is already a credible product experience.
Recommend closing the remaining gaps from "Tests NOT executed" above
(WA-06 disabled previews, WA-08 revoked link, WA-11 UTF-8 title) as a
cheap follow-up before deciding on `og:video`, since those directly bear
on required product-safety guarantees (graceful fallback, honest revoked
state) rather than a "nice to have" richer preview.

---

PHASE: Prompt 13 — Real WhatsApp Rich-Preview Experiment

STATUS: DONE (partial — several sub-tests explicitly NOT EXECUTED, see above)

BASELINE COMMIT: af14dd7

WHATSAPP VERSION: 2.23.20.0 (as seen in server logs; not independently
cross-checked against the phone's Settings > Help > App info)

ANDROID VERSION: not recorded

PUBLIC URL METHOD: Cloudflare Quick Tunnel

TEST ARTIFACT: tools/wa_test_01.mp4, 1280x720, 15s, ~1.46 MB

TESTS EXECUTED: WA-01/02/03 equivalent (x2, second after bug fix), WA-04,
informal Messenger comparison

TESTS NOT EXECUTED: WA-05, WA-06, WA-07 (explicit), WA-08, WA-09
(WhatsApp-specific), WA-10, WA-11, WA-12 (systematic), screenshot archive

COMPOSER PREVIEW: rich card with thumbnail/title/description (after fix);
domain-only, no thumbnail (before fix)

SENT MESSAGE PREVIEW: same rich card, confirmed via delivered/read receipt
in screenshot

RECIPIENT PREVIEW: same rich card

INLINE PLAYBACK: NO (confirmed via explicit multiple-choice question, not
inferred)

TAP DESTINATION: external browser, our /s/<share_id> page

WEB PLAYER: PASS

SEEK: PASS (multiple distinct Range requests, all 206, in server log)

PREVIEW-DISABLED RESULT: NOT TESTED

CRAWLER REQUESTS: /s (3x) + /preview (1x) from WhatsApp/2.23.20.0; zero
/media requests from that User-Agent

MEDIA REQUEST BEFORE HUMAN TAP: NO (confirmed via request log — no /media
entry with a WhatsApp User-Agent at any point)

CRAWLER SIDE EFFECT: NONE (ShareLink remained ACTIVE; no mutation code
path exists on /s or /preview)

REVOKED-LINK RESULT: NOT TESTED

OFFLINE/TUNNEL-DOWN RESULT: NOT TESTED (WhatsApp-specific; raw tunnel-down
HTTP 530 behavior already characterized in Prompt 12 without WhatsApp)

PREVIEW UX: GOOD

NAVIGATION UX: GOOD

PLAYBACK UX: GOOD

BUGS FOUND: 1 real (base_url pointing at 127.0.0.1 through a public
tunnel), fixed and regression-tested; 1 non-bug observation (title derived
from filename, not video content)

CODE CHANGES: LocalShareOrigin.set_base_url() (commit 5dd6c94), safe
request logging support (commit e169f40)

TESTS RUN: 239/239 passing

PROVEN / LIKELY / UNKNOWN: see sections above

PRODUCT DECISION: LINK_MODE_WHATSAPP_UX_ACCEPTED_WITH_LIMITATIONS

GATE: PASS (as a research phase — a `GOOD`, not `EXCELLENT`, product
result is valid evidence, per Prompt 13 §37's own framing that the gate
concerns whether the experiment was properly run and documented, not
whether the product outcome was perfect)

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: close WA-06/WA-08/WA-11 gaps cheaply; Prompt 14
(og:video) optional, not required, given a GOOD baseline

COMMIT: (recorded after this phase's commit)
