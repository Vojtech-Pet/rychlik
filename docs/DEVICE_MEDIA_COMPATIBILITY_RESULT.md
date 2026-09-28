PHASE: Prompt A16 -- Device Compatibility / Automatic Transcoding Pipeline
STATUS: DONE

BASELINE COMMIT: 36b2634a982d9137b3765c4103be614f2c07b235
ACTUAL BASELINE HEAD (at implementation start): 36b2634a982d9137b3765c4103be614f2c07b235

FILES CHANGED:
New:
- src/rychlik/device/media/__init__.py
- src/rychlik/device/media/descriptor.py
- src/rychlik/device/media/capability_profile.py
- src/rychlik/device/media/capability_query.py
- src/rychlik/device/media/planner.py
- src/rychlik/device/media/ffmpeg_runner.py
- src/rychlik/device/media/preparer.py
- src/rychlik/device/media/temp_cache.py
- src/rychlik/device/media/prepared_media.py
- src/rychlik/device/media/preparation_service.py
- tests/media_fixtures.py
- tests/test_device_media_probe.py
- tests/test_device_media_planner.py
- tests/test_device_media_preparer.py
- tests/test_device_media_temp_cache.py
- tests/test_device_media_preparation_service.py
- tests/test_device_media_capability_query.py
- tests/test_device_media_handoff_e2e.py
- docs/DEVICE_MEDIA_COMPATIBILITY.md
- docs/DEVICE_MEDIA_COMPATIBILITY_RESULT.md (this file)

Modified:
- src/rychlik/device/contracts.py (7 new HandoffErrorCode values, PREPARING state, 4 new DeviceHandoffSnapshot fields)
- src/rychlik/device/device_handoff_service.py (PREPARING phase, capability query + preparation integration, HANDOFF_PREPARING event, _prepare_media/_finish_handoff)
- src/rychlik/device/security/secure_transport.py (read-only `trust_store` property)
- friendsend/lib/receiver/receiver_server.dart (ReceiverConfig.mediaProfiles)
- friendsend/lib/security/secure_receiver_server.dart (/hello advertises media_profiles)
- friendsend/test/secure_receiver_server_test.dart (new media_profiles advertisement test)
- docs/OPEN_VALIDATION_DEBT.md (A16 note: no new physical debt entry, subsumed by A14-PHYSICAL-ANDROID-SHARE-SMOKE)

FFMPEG VERSION: n9.0.1
FFPROBE VERSION: n9.0.1

FFMPEG REQUIRED CAPABILITIES: libx264 (video encode), aac (audio encode), libvpx-vp9 + libopus (test fixtures only, to generate real incompatible source media) -- all confirmed present via `ffmpeg -hide_banner -encoders` before implementation; preflight-checked again at runtime before every transcode (`preflight_encoders`).

EXISTING MEDIA INFRASTRUCTURE AUDIT:
- rychlik.share.media_probe / rychlik.share.thumbnail_generator: real, safe ffprobe/ffmpeg wrappers, but duration/width/height only -- insufficient for codec-level decisions. Not reused/extended; their subprocess-safety style was mirrored in new code instead.
- rychlik.core.artifact.Artifact + private _sha256_of streaming hash helper: reused directly for derived media (imported, not duplicated).
- No existing generic safe-subprocess-runner utility: new FFmpegProcessRunner written fresh.
- No existing Python TTL temp-cache pattern: ported 1:1 in design from the proven A14 Dart TempCache.
Full detail in docs/DEVICE_MEDIA_COMPATIBILITY.md.

MEDIA DESCRIPTOR: real ffprobe (argv list, shell=False, bounded timeout), typed MediaProbeFailedError on corrupt/unreadable input -- FFmpeg is never started blindly. Captures media_kind, container, duration, video/audio stream codec/profile/pixel_format/color_transfer (HDR)/frame_rate, plus additional video/audio/subtitle/data stream counts.

CAPABILITY MODEL: DeviceMediaProfile + DeviceMediaCapabilities; two shipped baselines, friendsend-generic-video-v1 (MP4/H.264/AAC/yuv420p) and friendsend-generic-audio-v1 (M4A-family/AAC) -- explicitly documented as compatibility baselines, never a per-downstream-app guarantee.

CAPABILITY TRUST SOURCE: fetch_device_media_capabilities() reuses A15's own connect_and_verify_pin (same TLS-pin-verified connection as the real handoff) to GET /hello. Unknown profile ids are silently ignored. Query failure falls back to direct passthrough (skip preparation) rather than hard-failing the handoff. Verified cross-language against the real Dart secure receiver, including a wrong-pin-fails-closed test.

PLANNER: pure, no subprocess execution. PASSTHROUGH / REMUX / TRANSCODE_AUDIO / TRANSCODE_VIDEO / TRANSCODE_AUDIO_VIDEO / UNSUPPORTED. Compatible streams always copied, never re-encoded blindly -- proven by real mixed-compatibility fixtures. HDR (smpte2084/arib-std-b67) is passthrough-if-already-compatible or explicit HDR_TRANSCODE_UNSUPPORTED -- never a silent SDR downgrade. Dropped extra streams (additional video/audio/subtitles/data) produce explicit PlanWarnings, never silent loss.

FFMPEG EXECUTION: -progress pipe:1 -nostats machine-readable progress (never scraped stderr); bounded stderr tail; graceful terminate->wait->kill->reap cancellation (no zombies); encoder preflight before every transcode. Video: libx264 CRF 22 preset medium yuv420p, odd dimensions padded (never cropped), aspect ratio preserved. Audio: AAC 160kb/s, >2ch downmixed to stereo (mono stays mono), 44.1/48kHz preserved or falls back to 48kHz. -movflags +faststart on all MP4/M4A output.

OUTPUT VALIDATION: every prepared output is re-probed with the same real probe_device_media and checked against the plan before being considered valid (PreparedMediaInvalidError otherwise) -- REMUX validation proves the codec was actually copied, not accidentally re-encoded; transcode validation checks target codec/pixel-format; duration checked within a tolerant 0.5x-1.5x ratio. Exit code 0 alone is never sufficient.

TEMP CACHE: $XDG_CACHE_HOME/rychlik/device-media/ (0700 perms where POSIX applies), safe-segment-id-only naming (never the source filename), injectable clock for TTL tests, 24h default TTL, startup stale-sweep, ownership-safe cleanup that refuses to delete anything outside its own resolved root (tested directly against a forged record).

DERIVED ARTIFACT: PreparedDeviceMedia wraps either the original Artifact (PASSTHROUGH, temporary=false) or a newly built derived Artifact (REMUX/TRANSCODE, temporary=true, distinct SHA-256/MIME from the source) -- reuses Artifact's own hashing logic, no duplicate hash implementation.

DEVICE HANDOFF INTEGRATION: new PREPARING state (bytes_sent stays 0 while active); gated on SecureFriendSendTransport + video/* or audio/* MIME only -- legacy plain-HTTP profile and non-media sends entirely unaffected (all 25 pre-A16 A13/A14 tests using the legacy transport still pass unmodified). DeviceHandoffSnapshot gained preparation_kind/preparation_progress/target_profile_id/preparation_warnings (plain str/tuple fields, deliberately not importing media-package enum types, to avoid a contracts.py -> rychlik.device.media layering dependency). The same cancel_event already used for network cancellation is threaded through media preparation, so the existing cancel() API transparently cancels either phase.

CONCURRENCY: DeviceMediaPreparationService uses its own bounded threading.Semaphore (default 1 concurrent preparation) around only the FFmpeg-running step -- never the A5 download pool, never A13's handoff network pool.

ANDROID CAPABILITY ADVERTISEMENT: ReceiverConfig.mediaProfiles (default: both generic profile ids) is additively included in the secure receiver's real GET /hello JSON response. Protocol version remains 1 (purely additive optional metadata). No mobile UI redesign, no per-social-app capability claims.

REAL (NON-MOCKED) TESTS ADDED:
- tests/test_device_media_probe.py (5): real ffprobe against real generated fixtures, including corrupt-file and missing-file typed failures.
- tests/test_device_media_planner.py (11): pure planner logic against real descriptors -- compatible passthrough, MKV remux-only, audio-only transcode, video-only transcode, full transcode, HDR rejection, HDR-already-compatible passthrough, no-known-profile rejection, extra-stream warnings, WAV/PCM audio transcode, unsupported media kind.
- tests/test_device_media_preparer.py (11): real remux with codec-copy verification, real mixed audio-only/video-only transcodes, real full VP9/Opus->H264/AAC transcode, odd-dimension padding, special-character-filename-safe argv, encoder-unavailable/nonzero-exit/output-validation-failure typed errors, real cancel-mid-transcode.
- tests/test_device_media_temp_cache.py (5): safe naming, discard, TTL cleanup with injected clock, startup sweep, ownership-safe cleanup against a forged record.
- tests/test_device_media_preparation_service.py (6): passthrough creates no temp dir, remux produces a derived artifact with a different hash, discard removes the derived file while the source survives, probe-failure/no-known-profile typed errors, cancel-during-preparation leaves the source untouched.
- tests/test_device_media_capability_query.py (2): real cross-language capability query against the real Dart secure receiver (success + wrong-pin-fails-closed).
- tests/test_device_media_handoff_e2e.py (6): real full-stack handoffs via real DownloadManagerService + real SecurePairingManager + real Dart secure receiver -- passthrough, MKV remux, incompatible-codec transcode, restarted-completion->transcode composition, Share-by-Link/Device-Mode coexistence on one Artifact, cancel-during-transcode.
- friendsend/test/secure_receiver_server_test.dart: +1 test (media_profiles advertisement in /hello), suite 6 -> 7.

Total new Python tests: 46 (40 unit/integration + 6 real E2E), all real, no mocked FFmpeg/ffprobe subprocess calls anywhere.

VALIDATION DEBT: no new physical-device debt entry created (per explicit instruction). docs/OPEN_VALIDATION_DEBT.md updated with an A16 note stating that A16's prepared-media physical validation is subsumed by the existing A14-PHYSICAL-ANDROID-SHARE-SMOKE entry. A9-CRASH-RANGE-E2E and A15-PHYSICAL-MDNS-DISCOVERY remain OPEN and untouched.

DESKTOP TEST SUITE: 906/906 passed (full regression, includes all 46 new A16 tests).
FLUTTER TEST SUITE: 58/58 passed (57 A15 baseline + 1 new A16 test).
ANDROID DEBUG APK BUILD: succeeded.
ANDROID RELEASE APK BUILD: succeeded (app-release.apk, 51.7MB).

GATE: PASS -- all automated acceptance criteria from Prompt A16 §159 satisfied: real probe/plan/prepare/re-validate pipeline; least-destructive-transformation policy proven per-stream; HDR never silently downgraded; original file never modified; Share-by-Link/Device-Mode coexistence proven on one Artifact; secure, authenticated capability discovery reusing A15's exact TLS-pin mechanism; bounded/cancellable FFmpeg execution with real progress and no zombies; dedicated bounded preparation concurrency, isolated from A5/A13 pools; temp-cache lifecycle (TTL, startup sweep, ownership-safe cleanup) proven directly; full backward compatibility with the legacy plain-HTTP profile and non-media sends; Android additive capability advertisement; full desktop + Flutter regression green; both APKs build.

NEXT PHASE READY: YES
NEXT RECOMMENDED PHASE: Prompt A17 -- Device Mode Release Hardening / Physical Android Validation (closing A9-CRASH-RANGE-E2E, A14-PHYSICAL-ANDROID-SHARE-SMOKE, A15-PHYSICAL-MDNS-DISCOVERY on real hardware; not a new feature branch; monetization and final visual redesign deferred further still). Not to be started without explicit user confirmation.

COMMIT: 5ae73df620e45afc9e5b0904a94ee443ed0a7dc2
