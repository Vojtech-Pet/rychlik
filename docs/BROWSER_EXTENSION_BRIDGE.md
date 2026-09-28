# Browser extension bridge

The extension from `rychlik-downloader/browser-extension/` now lives in `browser-extension/` and talks to the new Rýchlik through a
loopback bridge (`src/rychlik/bridge/browser_bridge.py`), on the same port as before (127.0.0.1:17654).

## Why it changed

The old bridge accepted any `POST` from any web page (`Access-Control-Allow-Origin: *`, no secret), so a website you visited could
queue downloads in the app. The new bridge serves a request only when **all** of this holds:

1. `Host` is exactly this loopback endpoint (`127.0.0.1:<port>` / `localhost:<port>`), so DNS-rebinding pages are refused (403).
2. `X-Rychlik-Token` equals the app's secret (constant-time compare). The token is 32 random bytes in a 0600 file
   (`~/.local/share/rychlik/browser_bridge_token`); without it: 401.
3. Body is at most 64 KB of JSON with an `http(s)` URL (`file:`, `javascript:` … refused), and at most 30 requests/min (429).
4. CORS is granted only to `moz-extension://` / `chrome-extension://` origins.

An accepted request **does not download anything**: it opens the Add download dialog pre-filled and the user confirms. A video page
(`media: true`) becomes a `DownloadRequest` with `MediaOptions` (yt-dlp), a plain link stays a plain download; enabled site modules
still get the URL first. `/formats` returns the quality list (Najlepšia kvalita, 1080p, …) from yt-dlp.

## Extension changes

- `bridge-client.js` (new) holds the port and sends the token; only the background script uses it. `content.js` no longer talks to the
  network: it asks the background script (`runtime.sendMessage({type:"bridge"})`), so neither the token nor the port is visible to a page.
- The popup has a token field; the app shows the token in **Settings → Browser extension** (masked, Copy, New token…).
- No token or a wrong token gives a notification telling the user what to do.

## Setup

1. Rýchlik → Settings → Browser extension → Copy token.
2. Install `dist/rychlik-2.0.0.xpi` (see below) or load `browser-extension/manifest.json` via about:debugging → Load Temporary Add-on, open the extension popup, paste the token, Save.

## Evidence

- `tests/test_browser_bridge.py` (15): real HTTP against the real server — missing/wrong token, rebinding Host, hostile bodies, flood, formats,
  CORS, loopback-only, token file mode and regeneration, port-in-use; and bridge -> pre-filled dialog -> queue with/without `MediaOptions`.
- `tests/test_browser_extension.py` (5): Node runs the extension's client and the real background script (mocked browser API) against the
  real Python bridge: token sent, missing/wrong token reported, only `/download` and `/formats` relayable, `content.js` contains no fetch/port/token.
- Full suite 1234 passed.

Not verified: the extension loaded in a real Firefox/Chrome (only Node with a mocked browser API), the floating button and quality
panel on real sites, Chrome (the manifest is Firefox-style `background.scripts`, unchanged from the old extension), and the old app
running at the same time (it also listens on 17654; the second one to start reports the port as taken in Settings).

## Firefox package

`npx web-ext build --source-dir browser-extension --artifacts-dir dist --filename rychlik-2.0.0.zip` (copied to `rychlik-2.0.0.xpi`); `web-ext lint`: 0 errors, 0 warnings.
Version 2.0.0 (the token makes it incompatible with the old app's open bridge). The package is **unsigned**, like the earlier `.xpi` files in `rychlik-downloader/web-ext-artifacts`:
Firefox release refuses unsigned add-ons; install it in Firefox Developer Edition / Nightly / ESR with `xpinstall.signatures.required = false`, or load it temporarily
from `about:debugging`. A permanently installable build for regular Firefox needs signing on addons.mozilla.org (unlisted), which needs an account and API keys.
