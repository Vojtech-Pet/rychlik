# Browser extension bridge

The extension from `rychlik-downloader/browser-extension/` now lives in `browser-extension/` and talks to the new Rýchlik through a
loopback bridge (`src/rychlik/bridge/browser_bridge.py`), on its own port, 127.0.0.1:17655 (the old downloader keeps 17654, so both can run at the same time and neither extension can reach the other app).

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
2. Install `dist/rychlik-desktop-2.0.3.xpi` (see below) or load `browser-extension/manifest.json` via about:debugging → Load Temporary Add-on, open the extension popup, paste the token, Save.

## Evidence

- `tests/test_browser_bridge.py` (15): real HTTP against the real server — missing/wrong token, rebinding Host, hostile bodies, flood, formats,
  CORS, loopback-only, token file mode and regeneration, port-in-use; and bridge -> pre-filled dialog -> queue with/without `MediaOptions`.
- `tests/test_browser_extension.py` (5): Node runs the extension's client and the real background script (mocked browser API) against the
  real Python bridge: token sent, missing/wrong token reported, only `/download` and `/formats` relayable, `content.js` contains no fetch/port/token.
- Full suite 1234 passed.

Not verified: the extension loaded in a real Firefox/Chrome (only Node with a mocked browser API), the floating button and quality
panel on real sites, Chrome (the manifest is Firefox-style `background.scripts`, unchanged from the old extension), and both apps running together in a real browser (they use different ports, 17654 old / 17655 new, and the new app's Settings reports if 17655 is taken).

## Firefox package

`npx web-ext build --source-dir browser-extension --artifacts-dir dist --filename rychlik-desktop-2.0.3.zip` (copied to `rychlik-desktop-2.0.3.xpi`); `web-ext lint`: 0 errors, 0 warnings.
Version 2.0.1 (2.0.0 shared the old app's port 17654 and connected to it when the old app was running; the token makes it incompatible with the old app's open bridge). The package is **unsigned**, like the earlier `.xpi` files in `rychlik-downloader/web-ext-artifacts`:
Firefox release refuses unsigned add-ons; install it in Firefox Developer Edition / Nightly / ESR with `xpinstall.signatures.required = false`, or load it temporarily
from `about:debugging`. A permanently installable build for regular Firefox needs signing on addons.mozilla.org (unlisted), which needs an account and API keys.

Add-on ID changed to `rychlik-desktop@rychlik.app` (an identifier only, not a real address): addons.mozilla.org already had a different add-on with the old ID `rychlik@localhost`
(upload error "duplicate add-on ID"). It is a separate add-on from the old extension; install it after removing the old one.

## Look (2.0.3)

Overlay button, quality panel and popup use the desktop app's design tokens: dark surface `#18181F`, border `#353744`, purple accent `#6C5DF6`, pill button, 14 px panel radius, the new app icon. The overlay is always dark (readable on any site); the popup follows the browser's light/dark preference (light palette from the app's light theme). Static renders of the real CSS: `artifacts/browser_extension/` (not a screenshot of a live page).
