# M6 — custom-source resolver modules: live-network verification result

Scope: the 5 modules audited as "own source resolution" (`docs/LEGACY_MODULES_AUDIT.md`), already loaded/dispatch-tested in M4 without network. This phase ran each one's real resolver against the real live site today (2026-09-28) and, where it produced a URL, made a real ranged HTTP GET against it (no full download) to prove the extracted address is genuininely playable content, not just a plausible-looking string.

Method: `resolve_with_legacy_module(module, real_page_url, tmp_dir)` → `DownloadRequest.url`, then `GET` that URL with `Range: bytes=0-65535` and the same `Referer`/headers the module set, checking status + `Content-Type` + real bytes returned.

## Results

| Module | Real page tried | Result |
|--------|-----------------|--------|
| **pornhub** | a real, currently-listed video page | **PASS** — resolved to an HLS manifest URL; `GET` returned `206`, `content-type: application/vnd.apple.mpegurl`, real manifest bytes |
| **shemaletubevideos** | a real, currently-listed video page | **PASS** — resolved to a direct CDN `.mp4`; `GET` returned `206`, `content-type: video/mp4`, real video bytes |
| **trannyvideosx** | a real, currently-listed video page | **FAIL (site regression)** — resolved to `servpornxxx.serverporno1.xyz/media/videos/h264/...mp4`; that host answers `200 text/html` ("Apache" default-ish page), not video. Reading `trannyvideosx.py`: its embed-page decrypt path (`_decrypt_src`/`_parse_vid_files`) apparently no longer matches the site's current embed page, so it silently falls back to a guessed CDN URL pattern that is stale. Fixing this needs re-reverse-engineering the current embed page, not attempted here. **Disabled in the running registry** (`set_enabled("trannyvideosx", False)`) so it is never dispatched until fixed. |
| **ashemaletube** | site root (can't even reach a video page) | **FAIL (site now blocks the module's transport)** — `ashemaletube.com` now answers every plain request with Cloudflare's "Just a moment…" JS challenge (`403`, `server: cloudflare`). The module uses plain `requests`, which can never pass a JS challenge; it cannot work again without a headless-browser-based fetch, a different (unofficial) bypass, or dropping the site. **Disabled in the running registry.** |
| **ladyboygold** | — | **INCONCLUSIVE** — the site's public frontend is now a client-rendered SPA (`ladyboygold.com/natscms-app/...`) with almost no server-rendered links, so no real `/video/<slug>` URL could be found by crawling (search engines also blocked scripted queries). The module talks to the same backend API the SPA uses (`config.json`, `tour_api.php/content/sets`) rather than scraping HTML, and `config.json` still answers with the expected shape, so the *plumbing* looks intact — but without a real slug the actual video-resolution path was never exercised end-to-end. Left **enabled** (untested, not proven broken) with this caveat recorded.

## Consequence for the module set

Active in `~/.local/share/rychlik/modules/`: `media_sites` (5 sites, M5), `pornhub`, `shemaletubevideos` — all live-verified today. `ashemaletube` and `trannyvideosx` are registered but disabled. `ladyboygold` is enabled but not live-verified.

## Not done here

- No fix attempted for `trannyvideosx`'s embed decryption or for giving `ashemaletube` a Cloudflare-capable transport — both are substantial reverse-engineering efforts, not something to do inside a verification pass.
- A real `ladyboygold` slug was not found; re-attempt if/when a real video URL is available.
- `MediaAcquisition` (the yt-dlp backend that would actually receive `trannyvideosx`'s bad URL in production) does not itself re-validate `Content-Type` before accepting a `.mp4`-named URL — worth hardening later so a similarly stale module can't silently produce a corrupt "download," but that is a separate, general finding, not specific to this module.
