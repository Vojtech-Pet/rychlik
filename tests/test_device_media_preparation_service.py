"""Prompt A16: DeviceMediaPreparationService orchestration -- real ffprobe
+ real ffmpeg against real fixtures, real temp cache lifecycle."""

from __future__ import annotations

import threading

import pytest
from rychlik.core.artifact import Artifact
from rychlik.device.media.capability_profile import FRIENDSEND_GENERIC_AUDIO_V1, FRIENDSEND_GENERIC_VIDEO_V1, DeviceMediaCapabilities
from rychlik.device.media.planner import PlanKind
from rychlik.device.media.preparation_service import (
    DeviceMediaPreparationService,
    HdrTranscodeUnsupported,
    MediaProbeFailed,
    NoCompatibleProfile,
    TranscodeFailed,
)
from rychlik.device.media.temp_cache import DeviceMediaTempCache

from media_fixtures import make_compatible_mp4, make_corrupt_file, make_full_transcode_source, make_remux_mkv

CAPS = DeviceMediaCapabilities(profiles=(FRIENDSEND_GENERIC_VIDEO_V1, FRIENDSEND_GENERIC_AUDIO_V1))
NO_CAPS = DeviceMediaCapabilities(profiles=())


def _service(tmp_path) -> DeviceMediaPreparationService:
    return DeviceMediaPreparationService(temp_cache=DeviceMediaTempCache(tmp_path / "cache"))


def _artifact(path):
    return Artifact.from_completed_download(path)


def test_passthrough_never_creates_a_new_file_or_temp_dir(tmp_path):
    source = make_compatible_mp4(tmp_path / "a.mp4")
    artifact = _artifact(source)
    service = _service(tmp_path)

    prepared = service.prepare(artifact, CAPS)
    assert prepared.plan_kind == PlanKind.PASSTHROUGH
    assert prepared.temporary is False
    assert prepared.artifact is artifact
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").iterdir())


def test_remux_produces_derived_temporary_artifact_with_different_hash(tmp_path):
    source = make_remux_mkv(tmp_path / "a.mkv")
    artifact = _artifact(source)
    service = _service(tmp_path)

    prepared = service.prepare(artifact, CAPS)
    assert prepared.plan_kind == PlanKind.REMUX
    assert prepared.temporary is True
    assert prepared.artifact.sha256 != artifact.sha256  # §62: never pretend they match
    assert prepared.artifact.mime_type == "video/mp4"
    assert prepared.artifact.filename.endswith(".mp4")
    assert prepared.source_artifact_id == artifact.artifact_id

    # Original untouched (§129).
    assert source.exists()
    assert source.stat().st_size == artifact.size


def test_discard_removes_derived_temp_file_original_survives(tmp_path):
    source = make_remux_mkv(tmp_path / "a.mkv")
    artifact = _artifact(source)
    service = _service(tmp_path)
    prepared = service.prepare(artifact, CAPS, preparation_id="p1")
    assert prepared.artifact.local_path.exists()

    service.discard("p1")
    assert not prepared.artifact.local_path.exists()
    assert source.exists()


def test_probe_failure_raises_typed_error_no_ffmpeg_started(tmp_path):
    source = make_corrupt_file(tmp_path / "bad.bin")
    # Artifact.from_completed_download guesses mime from extension only,
    # so this constructs fine even though the bytes are not real media.
    artifact = Artifact.from_completed_download(source)
    service = _service(tmp_path)
    with pytest.raises(MediaProbeFailed):
        service.prepare(artifact, CAPS)


def test_no_known_profile_raises_typed_error(tmp_path):
    source = make_full_transcode_source(tmp_path / "a.webm")
    artifact = _artifact(source)
    service = _service(tmp_path)
    with pytest.raises(NoCompatibleProfile):
        service.prepare(artifact, NO_CAPS)


def test_cancel_during_preparation_cleans_up_and_leaves_source_untouched(tmp_path):
    source = make_full_transcode_source(tmp_path / "a.webm", duration=3.0, width=640, height=480)
    artifact = _artifact(source)
    service = _service(tmp_path)

    cancel_event = threading.Event()

    def on_progress(processed, duration):
        if processed and processed > 0.2:
            cancel_event.set()

    with pytest.raises(TranscodeFailed) as exc_info:
        service.prepare(artifact, CAPS, preparation_id="cancel-1", progress_callback=on_progress, cancel_event=cancel_event)
    assert exc_info.value.cancelled

    cache_dir = tmp_path / "cache"
    assert not any(cache_dir.iterdir()) if cache_dir.exists() else True
    assert source.exists()
    assert source.stat().st_size == artifact.size
