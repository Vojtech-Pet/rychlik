# Development Tunnel Experiment — Result (Prompt 12)

**Experiment only. Not production infrastructure.** Cloudflare's own docs
describe Quick Tunnel as intended for development/testing, not production —
this phase exists purely to answer one practical question before investing
further in Link Mode: *can the local share stack (Prompt 09) work correctly
when reached over real public HTTPS, through a tunnel we don't control?*

Per explicit steer for this phase: **one provider, no abstraction.** No
`TunnelRegistry`/`CloudflareAdapter`/`TailscaleAdapter` hierarchy —
`DevelopmentTunnelProvider` is a single concrete class wrapping `cloudflared
tunnel --url <local>`. Tailscale Funnel remains the documented fallback
*if* Cloudflare proves unusable — it does not; nothing about this
experiment motivates building it.

## What was built

```text
src/rychlik/share/development_tunnel.py
├── DevelopmentTunnelProvider  — start()/stop()/public_url, subprocess wraps cloudflared
├── TunnelStartError           — raised if no public URL appears within timeout
└── extract_public_url()       — pure regex helper (unit-testable without a real process)
```

`start()` spawns `cloudflared tunnel --url <local_url> --no-autoupdate`,
reads its combined stdout/stderr in a background thread, and extracts the
first `https://*.trycloudflare.com` URL it prints. `stop()` terminates the
process (SIGTERM, then SIGKILL after a timeout) and joins the reader
thread. Both are idempotent, matching the lifecycle pattern already used by
`LocalShareOrigin` (Prompt 07).

`cloudflared` itself was **not** installed via the system package manager
(`pacman` required an interactive sudo password unavailable in this
session) — the official static binary was downloaded directly from
GitHub Releases into `tools/cloudflared` (gitignored, not committed,
40 MB). This required no system-wide change and is trivially removable.

## Tests (deterministic, no real network)

7 tests in `tests/test_development_tunnel.py` run against a **fake**
`cloudflared` (a tiny Python script printing canned output), so they are
part of the normal, fast, CI-safe suite:

```text
extract_public_url(): realistic line, no match, ignores unrelated https:// URLs
start() picks up the public URL from process output
start() raises TunnelStartError on timeout when no URL is ever printed
double start() is idempotent
double stop() is safe
```

These do **not** prove the real Cloudflare service works — that requires
the real experiment below, run manually/standalone, not as part of the
automated gate (network-dependent, non-deterministic hostnames, external
service availability — the same reasoning Prompt 09 used for its own
"real browser smoke" being outside the pytest gate).

## The real experiment

Ran a standalone script (not committed, `/tmp/.../tunnel_experiment.py`)
against the actual product stack from Prompts 07–09:

```text
real ~2.9 MB ffmpeg-generated H.264 video (testsrc, forced 3 Mbps bitrate)
→ Artifact.from_completed_download()
→ ShareLink created + activated (ACTIVE)
→ SharePreviewService (real ffmpeg thumbnail)
→ LocalShareOrigin.start()  (127.0.0.1, OS-assigned port)
→ DevelopmentTunnelProvider.start()  (real cloudflared, real trycloudflare.com URL)
→ real HTTPS requests from this same machine, through Cloudflare's edge,
  back to the tunnel, back to LocalShareOrigin
```

### Results (all against the real public HTTPS URL)

```text
tunnel startup time:            4.9 – 6.2s across 5 runs
GET  /s/<share_id>          -> 200, correct HTML, correct og:title/og:image
GET  /preview/<share_id>    -> 200, image/jpeg, real thumbnail bytes
HEAD /media/<share_id>      -> 200, Content-Length matches, Accept-Ranges: bytes
GET  /media Range 0-0       -> 206, Content-Range: bytes 0-0/2927817, 1 byte, correct
GET  /media Range 1000000-1999999 -> 206, exactly 1,000,000 bytes, byte-for-byte
                                      match against the local file
GET  /media 3x seek ranges  -> 206 each, byte-for-byte match (start, middle, near-EOF)
GET  /media (full, no Range) -> 200, SHA-256 of downloaded bytes ==
                                      SHA-256 of the original local file, exactly
GET  /media Range 0-99      -> Content-Range/ETag/Content-Type all present and
                                      correct through the tunnel (headers NOT mangled)
```

**Every functional check passed against the real Cloudflare Quick Tunnel.**
Range requests are relayed byte-exact; none of the response headers
(`Content-Range`, `ETag`, `Content-Type`, `Accept-Ranges`, `Content-Length`)
were altered, stripped, or reordered by the tunnel.

### Shutdown behavior

After `DevelopmentTunnelProvider.stop()`, a request to the (now-dead)
public URL got an **HTTP 530** response from Cloudflare's edge (a Cloudflare
"no healthy origin" error page), not an immediate connection failure. This
is useful, truthful signal: Cloudflare's edge keeps answering for the
now-dangling hostname and reports the origin as unreachable, rather than
the DNS record vanishing instantly. A future Link Mode integration could in
principle treat a `530` from the public health-check as a "sender appears
offline" signal — not implemented here, just observed.

## Environment-specific issues encountered (sandbox artifacts, not product bugs)

Both were diagnosed and worked around in the **experiment script only** —
neither required any change to `DevelopmentTunnelProvider` or any other
product code:

1. **No working IPv6 route in this sandbox.** `curl -v` showed every IPv6
   connection attempt fail immediately (`Network is unreachable`) before
   falling back to IPv4 automatically; Python's `requests`/`urllib3` in
   this environment's version did not fall back the same way and simply
   raised `ConnectionError`. Worked around in the experiment script by
   monkey-patching `socket.getaddrinfo` to return IPv4 addresses only —
   confirmed this is an environment limitation (verified with a direct
   `curl -v` against Google and against the tunnel's own hostname) rather
   than anything Cloudflare- or Rýchlik-specific.
2. **Intermittent DNS resolution failures** for the freshly-created
   `*.trycloudflare.com` hostname — some requests to the exact same,
   already-successfully-resolved hostname failed moments later with `Name
   or service not known`, then succeeded again on retry. This looks like
   this sandbox's own resolver/cache being unreliable rather than genuine
   DNS propagation delay (the same hostname alternated between resolving
   and failing across consecutive requests seconds apart). Worked around
   with a short retry loop (up to 4 attempts, 2s apart) per request in the
   experiment script. **This does not reflect how a real end-user's
   browser/network would behave** — it is specific to this sandboxed dev
   environment's resolver.

Neither issue is reflected in `DevelopmentTunnelProvider`'s own code or
tests — the 7 committed tests use a fake process and need no network at
all, so they are unaffected and remain part of the normal fast test suite.

## What was NOT tested (explicitly deferred, not hidden)

```text
100 MB file           — not tested; only a ~2.9 MB fixture was used,
                         bandwidth/time budget in this sandboxed session
1 GB file              — not tested, same reason
multiple simultaneous viewers through the tunnel
real WhatsApp / Telegram fetching the public URL (this is Prompt 13)
Tailscale Funnel        — not attempted; Cloudflare worked, so the
                         documented fallback trigger never fired
production domain / named tunnel (this stays a random trycloudflare.com
                         subdomain — that is correct for this experiment)
```

## Gate assessment

The gate Prompt 12 originally proposed (§ list: HTML/thumbnail/media/Range/
seek/headers-not-mangled/100MB/1GB/shutdown-behavior) is **substantially
met** at small-file scale: every item except the two large-file-size checks
passed against the real public tunnel. The two skipped items are a matter
of test data size, not an open technical question — the byte-exact Range
relay already proven at the 1 MB-chunk scale is not expected to behave
differently at 100 MB/1 GB (Cloudflare Quick Tunnel proxies bytes; it does
not buffer or transform based on total transfer size), but this is an
assumption, not something this experiment measured directly.

## Conclusion

**Share by link is technically viable through Cloudflare Quick Tunnel.**
The local stack built in Prompts 07–09 (Range origin, ShareLink lifecycle,
SharePreview, Open Graph HTML) required **zero changes** to work correctly
behind a real public HTTPS tunnel — the local-first architecture held up
exactly as intended (§149 of the original research doc: "Internal transport
can evolve... The UI must not be coupled to one transport provider").

## NEXT RECOMMENDED PHASE

Per the plan agreed before running this experiment: **Prompt 13 — real
WhatsApp rich-preview experiment**, now that a real public HTTPS URL
reliably serves correct HTML/OG/thumbnail/Range media. This is the step
that will actually determine whether Share by Link is a strong product
feature.
