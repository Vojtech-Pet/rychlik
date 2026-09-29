# Acquisition Baseline (Prompt 04.5, step 1)

## What exists elsewhere on disk

`/home/vojtech/Stiahnuté/rychlik-downloader/downloader.py` is a legacy prototype
(not part of this repo, not a dependency of it). Relevant patterns observed:

- Uses `requests` with `stream=True`, `allow_redirects=True`, explicit
  `timeout=(connect, read)` tuples.
- Temporary file convention: downloads to `<destination>.part`, and for
  segmented downloads to `<destination>.part.<index>`, then combines/renames
  to the final `destination` only on success (`class DownloadWorker`,
  `class Download`).
- Supports segmented/ranged parallel downloading and browser-cookie-based
  auth (`use_browser_cookies`), plus site-specific extraction helpers
  (`PremiumContentError`, page-scraping functions near the bottom of the file).

## What is reused in this phase

- The `<name>.part` temporary-file convention (kept identical, for
  consistency with any future full port).
- `requests` as the HTTP client (added as a dependency here), matching the
  legacy streaming/timeout/redirect approach, so later porting of more
  legacy behavior (cookies, headers, segmented ranges) is a smaller diff.

## What is intentionally deferred

- Segmented/parallel range downloading (`.part.<index>` combining logic).
- Browser-cookie based authentication.
- Site-specific extraction / scraping helpers.
- yt-dlp backend.
- Safe validator-based resume (this phase restarts interrupted downloads
  from zero — see docs/ACQUISITION_VERTICAL_SLICE_RESULT.md).

## New acquisition boundary (this repo)

```text
DownloadRequest (url, destination_dir, filename_hint)
        ↓
AcquisitionService.acquire(request) -> CompletedDownload
        ↓ (backend: DirectHttpAcquisition)
CompletedDownload (final_path, display_name, source_url, mime_type, size)
        ↓
Artifact.from_completed_download(...)
```

`AcquisitionService` is a thin dispatcher in front of one or more backends
(`DirectHttpAcquisition` for this phase). It owns nothing about sharing;
`Artifact` remains the sole boundary object consumed by `ShareService`, per
docs/SHARE_ARCHITECTURE_CURRENT.md.
