# Legacy download modules — audit and migration allowlist (M1)

Source: `/home/vojtech/Stiahnuté/rychlik-downloader/download_modules/` (audited 2026-09-28). Legacy contract:
`can_handle(url) -> bool` and `download(worker)`; the old loader imported every `*.py` there except `_*` and `investigate_*`.

## In scope (11 — the only modules to migrate)

| # | Module | Lines | Kind | Ends with | Notes |
|---|--------|------:|------|-----------|-------|
| 1 | xvideos | 50 | normalize + yt-dlp | `worker._run_media()` | sets url/source_url/media/destination/video_format |
| 2 | xnxx | 50 | normalize + yt-dlp | `_run_media()` | same as xvideos |
| 3 | eporner | 50 | normalize + yt-dlp | `_run_media()` | same as xvideos |
| 4 | tgtube | 82 | normalize + yt-dlp | `_run_media()` | also sets `referrer`; uses `requests` |
| 5 | shemalez | 123 | normalize + yt-dlp | `_run_media()` | `referrer`; `requests` |
| 6 | pornhub | 177 | own source resolution | `_run_media()` | JS challenge solver; `referrer`; `requests` |
| 7 | ashemaletube | 109 | own source resolution | `_run_media()` | page scrape; `referrer`; `requests` |
| 8 | shemaletubevideos | 120 | own source resolution | `_run_media()` | `referrer`; `requests` |
| 9 | ladyboygold | 187 | own source resolution | `_run_media()` | JSON API; `referrer`; `requests` |
| 10 | trannyvideosx | 308 | own source resolution | `_run_media()` | `referrer`; `requests`; decodes a site-specific key scheme (`vitem.part0/part1` are the site's own fields, not `worker.item`) |
| 11 | shez_tube | 82 | external process | own `subprocess.Popen` | runs `she-tube-downloader.sh` (in scope as its asset); uses `worker._set_process/_cancel/_terminate_process` — hardest lifecycle case |

Observed legacy `worker` surface actually used by these 11 modules (nothing else):
`worker.item` (fields: `url, source_url, referrer, video_format, media, destination, display_name, status, error, speed, downloaded, total`),
`worker.update(item)`, `worker._run_media()`, and for shez_tube only `worker._set_process`, `worker._cancel` (an Event), `worker._terminate_process`.
All modules except shez_tube finish by calling `_run_media()`, i.e. they are *URL resolvers in front of yt-dlp*; the adapter can therefore turn
`_run_media()` into a typed media request instead of downloading inside the module.

## Out of scope (explicit)

| File | Reason |
|------|--------|
| `faphouse_paywall.py`, `faphouse_cs_5min_check.py`, `download_cs_faphouse_full_video.py` | Aimed at obtaining paywalled/paid content (paywall API, auth headers, browser cookies). Not migrated. |
| `investigate_ashemeta.py` | Research script, not a module (old loader skipped it). |
| Stream.cz (`tools/streamcz_download.py`) | Later, as a native `resolve()` module — not a legacy module. |

## Migration constraints (agreed)

- `LegacyWorkerAdapter` is a compatibility layer, never a core API; Rýchlik core must not be shaped by the old `worker` contract.
- Modules never own a process: the adapter maps `_set_process/_terminate_process/_cancel` to a runtime-owned process handle; pause/cancel/terminate/progress/cleanup/persistence stay in core.
- `worker.item` is not passed deep into the system; after `download(worker)` (or `_run_media()`) it is translated into a validated typed request.
- Order: M2 yt-dlp `MediaAcquisition` (core primitive) → M3 adapter → M4 loader + enable/disable registry → M5 five simple modules → M6 six resolvers → M7 shez_tube → M8 GUI Tools › Modules → M9 queue/Artifact acceptance → M10 Stream.cz native.
