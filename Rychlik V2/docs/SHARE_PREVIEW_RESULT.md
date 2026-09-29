# Share Preview Artifact — Result (Prompt 08)

**No Open Graph page exists yet. No public thumbnail URL exists yet. No
public internet exposure exists yet.** This phase produces only:
`Artifact -> SharePreview`. Prompt 09 will be the first phase to combine
`ShareLink + SharePreview + LocalShareOrigin` into an actual HTML page —
still local only.

## Required contract change (found during inspection, per §1)

`LocalShareOrigin` (Prompt 07) already had an Artifact-change check
(`_resolve_artifact_file` / `ArtifactChanged`), and `SharePreviewService`
needs the exact same policy — "align with Prompt 07's Artifact-change
semantics, do not invent a second incompatible mutation policy" (§16). That
logic was **moved**, not duplicated, into a new shared module:

```text
src/rychlik/core/artifact_integrity.py
├── ArtifactChanged
└── resolve_artifact_file(artifact) -> Path
```

`local_share_origin.py` now imports from there instead of defining its own
copy. Both `LocalShareOrigin` and `SharePreviewService` refuse to serve/
generate anything for an Artifact whose on-disk file no longer matches
(missing, truncated, resized) — one policy, one implementation.

## SharePreview model

```text
src/rychlik/share/share_preview.py
├── SharePreview (frozen dataclass): artifact_id, title, description,
│   media_kind, created_at, preview_version, duration_seconds, width,
│   height, thumbnail_path, thumbnail_mime
├── MediaKind: VIDEO, AUDIO, IMAGE, DOCUMENT, GENERIC_FILE
├── classify_media_kind(mime_type)      — pure
├── derive_title(filename)              — pure
├── derive_description(...)             — pure
└── select_thumbnail_timestamp(duration) — pure
```

`SharePreview.to_public_dict()` exposes only: `title`, `description`,
`media_kind`, `duration_seconds`, `width`, `height`. It excludes
`artifact_id` (opaque internal reference, same rationale as `ShareLink`),
`thumbnail_path` (internal filesystem path — a future HTTP layer maps it to
a public route, never exposed directly), and `thumbnail_mime`.

## Title / description rules

- **Title**: `Path(artifact.filename).stem`. Since `Artifact.filename` is
  always a bare basename (`Path(...).name`, enforced since Prompt 02), a
  title can never contain a directory component — this is structural, not
  merely tested. Empty stem falls back to `"Shared file"`.
- **Description**: deterministic, media-kind-specific, e.g. `Video •
  1920×1080 • 6:57`, `Audio • 3:42`, `Image • 800×600`, `PDF document`,
  `File • 24.8 MB`. Never includes a source URL, local path, browser name,
  cookies, or extractor name — those fields don't exist anywhere in the
  inputs to `derive_description()`.

## Media-kind detection

Purely from `Artifact.mime_type` (never from request input, since there is
none yet): `video/*` → VIDEO, `audio/*` → AUDIO, `image/*` → IMAGE,
`application/pdf` → DOCUMENT, everything else → GENERIC_FILE.

## Thumbnail strategy

- **Video**: representative frame via `ffmpeg`, timestamp chosen by
  `select_thumbnail_timestamp()`: ~15% into the video for durations ≥ 10s
  (capped at `duration - 0.5s`), else a safe early point (`min(1.0,
  duration/2)`) for short clips. Raw frame only — no play-overlay graphic,
  per the "raw frame now" recommendation.
- **Image**: run through the same `generate_thumbnail()` pipeline at
  timestamp 0 (ffmpeg reads a single still frame from an image input too),
  producing a resized cached copy rather than referencing the original file
  directly.
- **Audio / Document / Generic file**: no thumbnail (`thumbnail_path =
  None`). No waveform, no PDF page rendering — explicitly deferred.
- Output format: JPEG, scaled down to fit **1280×720** (aspect-ratio
  preserved, never upscaled) via `scale='min(1280,iw)':'min(720,ih)':
  force_original_aspect_ratio=decrease`.

### Bug found by the real FFmpeg integration test (not a mock)

The first implementation wrote the thumbnail to `<name>.jpg.tmp` before an
atomic rename (matching the acquisition `.part` convention). `ffmpeg`
selects its output **muxer** from the file extension by default, and
`.tmp` is not a recognized image extension — every real-fixture test failed
with `returncode=234` even though the equivalent `.jpg`-suffixed command
succeeded. Fixed by passing `-f mjpeg` explicitly, which makes the muxer
choice independent of the (temporary) file extension. This is exactly the
kind of failure that only a real-binary test (§32/§38) catches — a
mocked/fake ffmpeg call would have passed regardless.

## Subprocess safety

Both `probe_media()` (ffprobe) and `generate_thumbnail()` (ffmpeg) invoke
`subprocess.run()` with argument-array commands, `shell=True` is never used,
and both have a bounded `timeout`. Tested against filenames containing
spaces, semicolons, quotes, Slovak/Czech unicode, a leading dash, and a
`; touch should-not-exist` shell-injection-shaped name — none execute a
shell, and the injection-shaped filename simply fails as "no such file"
(the marker file is never created).

## FFprobe / FFmpeg failure policy

- `probe_media()` returns `None` (not an exception) on: missing binary,
  nonexistent file, non-media file, malformed JSON, non-zero exit, or
  timeout. `SharePreviewService` treats a `None` probe as "no metadata
  available" and continues — `duration`/`width`/`height` stay `None`, the
  preview is still produced.
- `generate_thumbnail()` returns `False` (not an exception) on equivalent
  failures, and always cleans up any partial `.tmp` file. A failed
  thumbnail never prevents `SharePreview` generation — `thumbnail_path`
  simply stays `None`.

## Cache strategy

```text
cache_dir/<artifact.sha256>/preview-v<PREVIEW_VERSION>.json  (metadata)
cache_dir/<artifact.sha256>/preview-v<PREVIEW_VERSION>.jpg   (thumbnail, if any)
```

Cache key = Artifact identity (`sha256`, computed once at Artifact
construction) + `preview_version`. A changed Artifact produces a different
`sha256` → a different cache directory → no possibility of stale reuse, by
construction (not merely by a validity check). `PREVIEW_VERSION = 1`;
bumping it invalidates every previously cached preview without needing a
migration (tested by forging an old version stamp in a cached JSON file).

Cache validation before reuse: metadata file exists and parses, its
`preview_version` matches the current constant, and — if the metadata
claims a thumbnail exists — the thumbnail file exists and is non-empty.
Any failure triggers full regeneration rather than serving a
partially-broken cache entry.

The metadata JSON is written to a `.tmp` sibling and moved into place with
`Path.replace()` (atomic on the same filesystem), same convention as
`.part` files in acquisition and thumbnails in this same phase.

## Privacy check

- `to_public_dict()` never includes `thumbnail_path`, `artifact_id`, or
  anything derived from `Artifact.local_path`/`source_url` — those fields
  simply are not inputs to any public-dict field.
- Verified end-to-end: the non-mock vertical E2E test serializes
  `preview.to_public_dict()` to JSON and asserts the temp working
  directory's path string does not appear anywhere in it.

## Tests

`176/176` passing total (all Prompt 01–07 tests remain green — no test
needed to change this phase). New: 26 (`test_share_preview.py`, pure logic),
4 (`test_media_probe.py`), 9 (`test_thumbnail_generator.py`), 12
(`test_share_preview_service.py`) = 51 new tests.

Real-binary integration tests are marked with `@requires_ffmpeg` /
`@requires_ffprobe` (`tests/conftest_ffmpeg.py`), which `skipif` — not
fail — when the binaries are not installed, so CI without FFmpeg still
passes; this environment has both installed (`ffmpeg n9.0.1`, `ffprobe
n9.0.1`), so all of them actually ran and passed here, including the one
that caught the `.tmp`-extension muxer bug above.

## Real FFmpeg integration

`test_generate_thumbnail_real_fixture`, `test_probe_real_video_fixture`,
`test_first_generation_creates_cache_files`, and others generate a
deterministic tiny video via `ffmpeg`'s `testsrc` source
(`tests/conftest_ffmpeg.py::make_test_video`) and run the real pipeline
against it — not a mock.

## Real preview E2E

`test_non_mock_vertical_e2e` (in-suite): a real local HTTP fixture serves a
real ffmpeg-generated video, `AcquisitionService` downloads it for real,
`Artifact.from_completed_download()` builds a real Artifact, and
`SharePreviewService.get_or_create_preview()` produces a real cached
thumbnail — asserted to exist, be non-empty, and never leak the local temp
path through `to_public_dict()`.

Additionally run as a **standalone script outside pytest**
(`/tmp/.../e2e_check_preview.py`, not committed), confirming the same
pipeline end-to-end with printed output (title, description, thumbnail
path/size, public dict) rather than only assertions inside a test harness.

## Known limitations (explicitly deferred, not hidden)

```text
no scene-detection thumbnail selection (fixed percentage/early-point strategy)
no branded play overlay (raw frame only)
no audio waveform artwork
no PDF page rendering / document preview extraction
no provider-specific preview variants
no Open Graph page, no web player, no public URL, no public tunnel, no cloud
no persistent SharePreview repository beyond the on-disk cache
  (get_or_create_preview() is idempotent and cheap on a cache hit, but
  there is no separate queryable "preview registry")
GUI is NOT wired to SharePreviewService in this phase — ShareDialog already
  shows artifact.filename directly (Prompt 04/06), and wiring in a preview
  card would require plumbing an ArtifactRepository + cache_dir through GUI
  construction; kept out per this phase's explicit narrow scope
```

## ACCEPTANCE GATE

```text
[x] all previous 125 tests remain green (176 total after this phase)
[x] all new SharePreview tests pass
[x] real preview E2E passes (ffmpeg/ffprobe available in this environment)
[x] no private Artifact path leaks through public serialization
[x] no source_url leaks (field doesn't exist on SharePreview at all)
[x] cache correctly invalidates on Artifact change (different sha256 -> different cache dir)
[x] thumbnail generation is bounded/safe (argument-array subprocess, timeout, no shell=True)
[x] worktree clean (after commit)
```

---

PHASE: Prompt 08 — Share Preview Artifact

STATUS: DONE

BASELINE COMMIT: 117eee9

FILES CHANGED:
```text
src/rychlik/core/artifact_integrity.py     (new — required contract change)
src/rychlik/share/local_share_origin.py    (refactored to reuse artifact_integrity)
src/rychlik/share/share_preview.py         (new)
src/rychlik/share/media_probe.py           (new)
src/rychlik/share/thumbnail_generator.py   (new)
src/rychlik/share/share_preview_service.py (new)
tests/conftest_ffmpeg.py                   (new, shared fixture helper)
tests/test_share_preview.py                (new)
tests/test_media_probe.py                  (new)
tests/test_thumbnail_generator.py          (new)
tests/test_share_preview_service.py        (new)
docs/SHARE_PREVIEW_RESULT.md               (new, this file)
```

SHAREPREVIEW MODEL: artifact_id, title, description, media_kind,
created_at, preview_version, duration_seconds, width, height,
thumbnail_path, thumbnail_mime; `to_public_dict()` excludes all
internal/private fields.

MEDIA METADATA: `ffprobe` via argument-array subprocess, graceful `None` on
any failure; duration + video-stream width/height extracted from JSON output.

THUMBNAIL STRATEGY: `ffmpeg` single-frame extraction at a computed
timestamp, scaled to fit 1280×720, JPEG, atomic `.tmp` write + rename,
explicit `-f mjpeg` (bug fix — extension-based muxer detection fails on
`.tmp` paths).

CACHE STRATEGY: `cache_dir/<sha256>/preview-v<N>.{json,jpg}`, validated
before reuse (version match, thumbnail file present & non-empty), bumping
`PREVIEW_VERSION` invalidates all old entries.

PRIVACY CHECK: pass — no local path, source_url, artifact_id, or
thumbnail_path in `to_public_dict()`; verified via JSON string search in
the E2E test.

TESTS ADDED: 51 (26 + 4 + 9 + 12)

TESTS RUN: 176/176 passing

REAL FFMPEG INTEGRATION: pass (ffmpeg/ffprobe present; caught and fixed a
real muxer-selection bug that a mocked test would have missed)

REAL PREVIEW E2E: pass (in-suite + standalone script outside pytest)

KNOWN LIMITATIONS: see above — none hidden.

GATE: PASS

NEXT PHASE READY: YES

NEXT RECOMMENDED PHASE: Prompt 09 — Server-Rendered Open Graph Share Page

COMMIT: (recorded after this phase's commit)
