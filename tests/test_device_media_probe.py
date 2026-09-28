"""Prompt A16 §13/§14/§158: real ffprobe against real, small, generated
fixtures (§110)."""

from __future__ import annotations

import pytest
from rychlik.device.media.descriptor import MediaProbeFailedError, probe_device_media

from media_fixtures import make_compatible_mp4, make_corrupt_file, make_pcm_wav, make_remux_mkv


def test_probe_compatible_mp4(tmp_path):
    path = make_compatible_mp4(tmp_path / "a.mp4")
    descriptor = probe_device_media(path)
    assert descriptor.media_kind == "video"
    assert descriptor.video_stream.codec == "h264"
    assert descriptor.video_stream.pixel_format == "yuv420p"
    assert descriptor.audio_stream.codec == "aac"
    assert descriptor.duration_seconds is not None
    assert descriptor.duration_seconds > 0.5


def test_probe_mkv_container(tmp_path):
    path = make_remux_mkv(tmp_path / "a.mkv")
    descriptor = probe_device_media(path)
    assert "matroska" in descriptor.container


def test_probe_audio_only_wav(tmp_path):
    path = make_pcm_wav(tmp_path / "a.wav")
    descriptor = probe_device_media(path)
    assert descriptor.media_kind == "audio"
    assert descriptor.audio_stream.codec.startswith("pcm")


def test_probe_corrupt_file_fails_typed(tmp_path):
    path = make_corrupt_file(tmp_path / "bad.bin")
    with pytest.raises(MediaProbeFailedError):
        probe_device_media(path)


def test_probe_missing_file_fails_typed(tmp_path):
    with pytest.raises(MediaProbeFailedError):
        probe_device_media(tmp_path / "does_not_exist.mp4")
