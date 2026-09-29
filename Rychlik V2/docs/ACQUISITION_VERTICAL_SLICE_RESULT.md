# Acquisition Vertical Slice — Result (Prompt 04.5)

## Implemented architecture

```text
DownloadRequest (url, destination_dir, filename_hint)
        ↓
AcquisitionService.acquire()
        ↓
DirectHttpAcquisition (requests, streaming, .part temp file, atomic rename)
        ↓
CompletedDownload (final_path, display_name, source_url, mime_type, size)
        ↓
Artifact.from_completed_download()   ← unchanged, see "Artifact contract" below
        ↓
ShareService (unchanged)
```

GUI: `DownloadWidget` (functional skeleton) — URL field, Download button,
status label, Share button. Runs `AcquisitionService.acquire()` on a
background `QThread` so the UI thread never blocks; Share becomes enabled
only after a `succeeded` signal carrying a real `CompletedDownload`.

## Files changed

```text
docs/ACQUISITION_BASELINE.md                 (new)
docs/ACQUISITION_VERTICAL_SLICE_RESULT.md    (new, this file)
src/rychlik/acquisition/__init__.py          (new)
src/rychlik/acquisition/contracts.py         (new)
src/rychlik/acquisition/direct_http.py       (new)
src/rychlik/acquisition/acquisition_service.py (new)
src/rychlik/gui/download_widget.py           (new)
tests/http_fixture_server.py                 (new, local deterministic HTTP fixture)
tests/test_acquisition.py                    (new)
tests/test_download_widget.py                (new)
tests/test_prompt05_device_link_isolation.py (new, explicit regression per Prompt 05)
tests/conftest.py                            (added http_fixture_server session fixture)
```

## Tests

`33/33` passing (`pytest`, offscreen Qt platform), covering:

- `DownloadRequest` validation (empty URL, non-http scheme).
- Successful HTTP acquisition, redirect following, no leftover `.part` file.
- Cancel mid-download produces no `CompletedDownload` and no leftover files.
- HTTP 404 / HTTP 500 / connection-refused / empty body all raise
  `AcquisitionError` and leave the destination directory empty.
- `Content-Disposition` filename resolution.
- `CompletedDownload -> Artifact` (existence, size, sha256, MIME, public-dict
  safety).
- `DownloadWidget`: Share stays disabled until a real download completes
  against the local fixture server, and stays disabled on failure.
- Explicit Prompt 05 regression: Device Mode usable with Link Mode disabled,
  and vice versa.

All previously existing tests (Prompts 01–04) remain green.

## Real E2E result

Ran outside pytest, against the same local HTTP fixture server, as a
standalone script (not a mock-only assertion):

```text
URL (local fixture http://127.0.0.1:<port>/normal.mp4)
→ AcquisitionService.acquire()
→ real file on disk (17000 bytes, verified byte-for-byte)
→ Artifact.from_completed_download() (sha256 computed, MIME=video/mp4)
→ ShareService.share_by_link() → ShareStatus.CREATING

E2E: URL -> real file -> CompletedDownload -> Artifact -> Share = OK
```

Worktree is clean after this phase's commit (see below).

## Known limitations

- Only `DirectHttpAcquisition` exists. No yt-dlp backend, no site-specific
  extraction, no backend-selection logic.
- No resume: an interrupted download restarts from zero next time (no
  `Range` request, no validator). This is intentional for this phase.
- No segmented/parallel range downloading.
- No browser-cookie based auth.
- No bandwidth shaping/progress smoothing beyond raw bytes-written callback.
- `DownloadWidget` has no destination-picker UI, no queue, no persistence —
  one URL at a time, in-memory only.
- Filename collisions are not handled (a second download of the same
  filename will overwrite the previous `.part`/final file). Deferred —
  out of scope for a single vertical-slice proof.

## Deferred legacy Rýchlik features

Segmented downloading, `download_modules/*`, browser extension integration,
yt-dlp, cookie-based auth, site-specific scrapers — all explicitly out of
scope per Prompt 04.5 §11, still present only in the legacy
`rychlik-downloader` prototype outside this repo.

## Artifact contract

No changes were required. `Artifact.from_completed_download(local_path,
source_url=...)` already accepted everything `CompletedDownload` produces.
`CompletedDownload.mime_type`/`.size` (derived from HTTP response headers)
are intentionally *not* passed through to `Artifact` — `Artifact` independently
recomputes MIME from the filename and size from the filesystem, which is more
trustworthy than trusting server-supplied headers.

## NEXT RECOMMENDED PHASE

```text
Prompt 06 — ShareLink Domain Model
```

Acquisition now produces a real `Artifact` from a real file on disk, so
`ShareLink` work is no longer built on a purely test-shaped assumption.
