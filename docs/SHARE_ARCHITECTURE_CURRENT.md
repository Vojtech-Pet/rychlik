# Share Architecture — Current State (Prompt 01)

## Context

This is a greenfield project (`/mnt/Data/Rychlik-app`). There is no pre-existing
Rýchlik Desktop codebase to audit in this repository — earlier Rýchlik prototypes
(`rychlik-downloader`, `rychlik-qt-cpp`) exist elsewhere on disk but are not reused
as a dependency here. This document therefore records the *starting* architecture
decisions instead of an audit of legacy code, so the smallest attach point for
`ShareService` is explicit before Prompt 02+ proceeds.

## Stack decision

- Python 3.14, PySide6 (Qt) for GUI.
- `src/rychlik/` package layout:
  - `core/` — download acquisition, Artifact model (not share-specific).
  - `share/` — `ShareService`, `DeviceShareService`, `ShareLinkService`.
  - `gui/` — functional-skeleton GUI only (per TEMPORARY GUI RULE).
- `tests/` — pytest.

## Download acquisition

Not implemented yet. Per global rule 2 ("reuse working downloader"), download
acquisition is out of scope until a decision is made on whether to port logic
from `rychlik-downloader` (yt-dlp/browser-extension based) or reuse it as a
dependency. Until then, `Artifact.from_completed_download()` accepts any
already-completed local file so Share work can proceed independently.

## Smallest ShareService attach point

`Artifact` (Prompt 02) is the single boundary object. `ShareService` (Prompt 03)
consumes `Artifact` instances only — it has no knowledge of how a file was
acquired. This keeps the future downloader integration a one-function seam:
whatever produces a finished file just needs to construct an `Artifact`.

## What must not be rewritten later

- `Artifact` identity fields (`artifact_id`, `sha256`) must stay stable once
  downloader integration lands, since `ShareLink`/`DeviceLink` will reference
  `artifact_id`.

## Gate

Architecture understood: yes, for a greenfield start. Revisit this document
once real download acquisition code is ported in, per global rule 1.
