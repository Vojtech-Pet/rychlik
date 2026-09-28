# Device Media Compatibility (Prompt A16)

Automatic media preparation for Device Mode: a normal user should not
have to know whether their downloaded video was MKV/HEVC/VP9/Opus/PCM
before sending it to FriendSend. This document is normative for the
`rychlik.device.media` package.

## Central rule

```text
PASSTHROUGH -> REMUX -> PARTIAL TRANSCODE -> FULL TRANSCODE -> REJECT
```

Always the least destructive transformation that makes the source
compatible -- never "transcode everything because FFmpeg can" (§2).

## Existing media-code audit (§9)

Before writing anything new, the following existing code was inspected:

- `rychlik.share.media_probe` (`ProbeResult`: duration/width/height only)
  and `rychlik.share.thumbnail_generator` -- both real, safe (`argv`
  list, never `shell=True`, bounded timeout, graceful `None`/`False`
  failure) ffprobe/ffmpeg wrappers used by Share Preview generation.
  **Not reused/extended**: `ProbeResult` has no codec/pixel-format/HDR
  information at all, which compatibility decisions require (§11); a new,
  purpose-built `rychlik.device.media.descriptor.probe_device_media`
  mirrors the exact same subprocess-safety style instead.
- `rychlik.core.artifact.Artifact` (frozen dataclass; `sha256`/`size`/
  `mime_type` computed via a private `_sha256_of` streaming-hash helper
  and `mimetypes.guess_type`). Reused directly for derived media (§58) --
  see `rychlik.device.media.prepared_media.build_derived_artifact`, which
  imports the same private hash helper rather than duplicating it.
- No existing generic "safe subprocess runner" utility existed anywhere
  in the codebase; `rychlik.device.media.ffmpeg_runner.FFmpegProcessRunner`
  is new, following the same house style.
- No existing Python TTL-based temp-cache pattern existed; the design was
  ported 1:1 from the already-proven A14 Dart `TempCache`
  (`friendsend/lib/receiver/temp_cache.dart`): id-derived safe naming,
  injectable clock, a startup sweep that unconditionally clears anything
  untracked, and ownership-safe cleanup.

## Architecture

```text
Artifact
   |
DeviceMediaPreparationService  -- probe -> plan -> (ffmpeg) -> re-probe
   |
PreparedDeviceMedia
   |
DeviceHandoffService  (Prompt A13/A15, orchestrates PREPARING -> CONNECTING -> TRANSFERRING)
   |
A15 SecureFriendSendTransport  (TLS pin, Ed25519 auth, offer/stream -- unchanged)
```

`SecureFriendSendTransport` remains responsible only for TLS/pinning/
authentication/offer/stream (§8) -- it never probes or transcodes
anything. The GUI never builds an `ffmpeg` command line (§7) -- all of
this is backend/application service code, testable without PySide6
(§109).

## Media descriptor (`descriptor.py`)

Real `ffprobe -show_format -show_streams -print_format json`, argv list,
`shell=False`, bounded timeout. A corrupt/unreadable file raises a typed
`MediaProbeFailedError` -- FFmpeg is never started blindly on the
strength of a guess (§14).

`MediaDescriptor` carries `media_kind`, `container`, `duration_seconds`,
one primary `video_stream`/`audio_stream` (`VideoStreamInfo`/
`AudioStreamInfo`, including codec, pixel format, HDR transfer function,
profile, frame rate), plus counts of additional video/audio/subtitle/data
streams -- never inferred from MIME/extension alone (§11).

## Capability model (`capability_profile.py`)

```text
DeviceMediaProfile(profile_id, media_kind, mime_type, containers,
                    video_codecs, audio_codecs, pixel_formats,
                    max_width/height/fps/audio_channels)
```

Two shipped baselines (§17/§18):

| Profile | Container | Video | Audio | Pixel format |
|---|---|---|---|---|
| `friendsend-generic-video-v1` | MP4 | H.264 | AAC | yuv420p |
| `friendsend-generic-audio-v1` | M4A/MP4 audio | -- | AAC | -- |

**These are broad compatibility baselines, never a guarantee any
specific downstream app (WhatsApp, Messenger, ...) accepts the result**
(§99-101). No per-social-app rule exists anywhere in this codebase.

## Capability trust source (§20-24)

`capability_query.py::fetch_device_media_capabilities()` reuses A15's
own `connect_and_verify_pin` -- the SAME TLS-pin-verified connection
already used for the real handoff -- to `GET /hello` and read the real
receiver's `media_profiles` field. Unknown/unrecognized profile ids (an
older peer, or a peer advertising something this desktop version doesn't
know) are silently ignored (`DeviceMediaCapabilities.from_profile_ids`),
which naturally falls back to "no known target profile" -- the planner
then correctly refuses to transcode rather than guessing a conversion is
safe. A query that fails outright (network/pin error) causes the desktop
to skip preparation entirely and send the original Artifact directly
(the pre-A16 behavior), rather than hard-failing a send that might have
been fine as-is.

## Planner (`planner.py`)

Pure, deterministic, **no subprocess execution** (§25). Takes a
`MediaDescriptor` + `DeviceMediaCapabilities`, returns a
`CompatibilityPlan` (`PASSTHROUGH` / `REMUX` / `TRANSCODE_AUDIO` /
`TRANSCODE_VIDEO` / `TRANSCODE_AUDIO_VIDEO` / `UNSUPPORTED`). Compatible
streams are always copied, never blindly re-encoded (§29/§30) -- proven
directly by real fixtures where only one of the two streams needs
conversion (`test_real_mixed_copy_and_transcode_video_only`/`_audio_only`).

### HDR (§38-40)

`VideoStreamInfo.is_hdr` detects `smpte2084` (PQ) and `arib-std-b67`
(HLG) transfer functions. If HDR->SDR conversion would be required and
no tone-mapping pipeline is implemented (none is, in A16), the plan is
`UNSUPPORTED` with `HDR_TRANSCODE_UNSUPPORTED` -- never a silent,
washed-out automatic conversion. HDR content that is *already* fully
compatible with the target profile still passes through untouched.

### Extra streams (§33-36)

Additional video/audio streams, subtitles, and data/attachment streams
are dropped in any REMUX/TRANSCODE output (never carried into the wrong
container), each producing an explicit `PlanWarning`
(`ADDITIONAL_VIDEO_DROPPED`/`ADDITIONAL_AUDIO_DROPPED`/
`SUBTITLES_DROPPED`/`DATA_STREAMS_DROPPED`). Warnings are informational,
never a failure state (§37) -- a `PASSTHROUGH` result never carries
these warnings, since nothing was actually dropped (the original bytes
go out untouched).

## FFmpeg preparer (`preparer.py`, `ffmpeg_runner.py`)

- Encoder preflight (`preflight_encoders`): `libx264` for video (§41),
  `aac` for audio (§42) -- verified present via `ffmpeg -encoders`
  before ever starting a transcode; `TRANSCODER_UNAVAILABLE` if missing,
  never a silent fallback to a different codec.
- Video: CRF 22, preset `medium`, `yuv420p`, odd dimensions **padded**
  (never cropped, never distorting aspect ratio) via
  `pad=ceil(iw/2)*2:ceil(ih/2)*2` (§43/§46/§47).
- Audio: AAC 160 kb/s; >2 source channels downmixed to stereo, mono stays
  mono; sample rate preserved at 44.1/48 kHz, otherwise converted to
  48 kHz (§44/§45).
- `-movflags +faststart` on every MP4/M4A output (§50).
- Real, machine-readable progress via `-progress pipe:1 -nostats` --
  never scraped from decorative stderr (§67); stderr itself is captured
  with a bounded tail (4 KB) rather than retained unbounded (§66).
- Cancellation: cooperative check of a shared `threading.Event` between
  progress lines, then graceful `terminate()` -> bounded wait ->
  `kill()` -> reap -- never a leaked zombie process (§74).
- **Output validation is mandatory** (§79): a zero exit code alone is
  never sufficient. The output is re-probed with the exact same real
  `probe_device_media`, and:
  - a REMUX must show the *same* video/audio codec as the source
    (proves the copy didn't accidentally re-encode, §82);
  - a full/partial transcode must show `h264`/`yuv420p`/(`aac` if audio
    exists) (§83/§84);
  - duration must be within a tolerant 0.5x-1.5x ratio of the source
    (§81 -- never sample-exact).
  Any violation raises `PreparedMediaInvalidError` -- the handoff never
  proceeds with a result that doesn't actually satisfy its own plan.

## Temp cache (`temp_cache.py`)

`$XDG_CACHE_HOME/rychlik/device-media/` (fallback `~/.cache/rychlik/
device-media/`), directory `0700` where POSIX applies (§53/§54). Each
preparation gets its own subdirectory named only from an internal,
sanitized preparation id -- never the source/display filename (§57). An
injectable clock drives TTL tests without real sleeping; default TTL 24h
(§89) for genuinely abandoned entries. A startup sweep
(`sweep_untracked_on_startup`) unconditionally clears anything physically
present but not tracked by the current process (crash recovery, §88).
Cleanup (`_remove_if_owned`) refuses to touch anything outside the
cache's own resolved root, even given a corrupt/forged record pointing
elsewhere (§136/§137) -- proven directly by
`test_cleanup_never_deletes_outside_its_own_root`.

## Derived Artifact (`prepared_media.py`)

`build_derived_artifact()` reuses `Artifact`'s own hashing rules --
never a second hash implementation (§58). The derived file's MIME type
is the preparer's own known-correct value (`video/mp4`/`audio/mp4`),
never guessed from the temp cache's internal filename. `PASSTHROUGH`
results reference the *original* Artifact directly (`temporary=False`,
§60); REMUX/TRANSCODE results are always `temporary=True` (§61) and
never share the source's SHA-256 (§62) -- both facts proven directly by
`test_remux_produces_derived_temporary_artifact_with_different_hash`.

## Device handoff integration (`device_handoff_service.py`)

`HandoffState` gained `PREPARING` (before `CONNECTING`) -- while active,
`bytes_sent` stays 0 (§71). Only ever entered when the paired device uses
the secure profile (`SecureFriendSendTransport`) **and** the Artifact's
MIME type is `video/*` or `audio/*` -- the legacy plain-HTTP profile and
all non-media sends are entirely unaffected (proven: all 25 pre-A16
A13/A14 tests that exercise the legacy profile explicitly still pass
unmodified). `DeviceHandoffSnapshot` gained `preparation_kind`,
`preparation_progress`, `target_profile_id`, `preparation_warnings` --
all additive, GUI-safe (no subprocess/handle reference, §72). The SAME
`cancel_event` already used for network cancellation is threaded through
to media preparation, so the existing `cancel()` API transparently
cancels either phase (§73/§107) -- proven by
`test_real_cancel_during_transcode_handoff_e2e`. `DeviceMediaPreparationService`
uses its own bounded `threading.Semaphore` (default 1 concurrent
preparation) -- never the A5 download pool, never A13's handoff network
pool (§92-94).

## FriendSend Android capability advertisement

`ReceiverConfig.mediaProfiles` (default `{friendsend-generic-video-v1,
friendsend-generic-audio-v1}`) is advertised in the secure receiver's
`GET /hello` response as an additive `media_profiles` field. Protocol
version remains 1 (§23) -- this is purely additive optional metadata,
verified byte-identical in meaning by a real cross-language capability
query test (`test_real_capability_query_against_real_dart_receiver`).

## Security boundary

A16 changes nothing about A15's TLS pin / Ed25519 authentication / replay
protection / no-downgrade guarantees (§140). The capability query and the
real handoff both go through the identical pin-verification path; a
spoofed capability reply (wrong certificate) is rejected exactly like a
spoofed handoff would be (`test_wrong_pin_capability_query_fails_closed`).
A failed preparation, an unsupported HDR source, or no known compatible
profile all result in **zero Device Mode payload bytes** reaching the
receiver (§141/§142) -- proven structurally: `_run_handoff` never calls
`self._transport.send()` at all when preparation raises.
