import pytest

from rychlik.share.media_probe import probe_media
from conftest_ffmpeg import make_test_video, requires_ffprobe


@requires_ffprobe
def test_probe_real_video_fixture(tmp_path):
    video_path = make_test_video(tmp_path / "fixture.mp4", duration=2.0, size="64x64")

    result = probe_media(video_path)

    assert result is not None
    assert result.duration == pytest.approx(2.0, abs=0.2)
    assert result.width == 64
    assert result.height == 64


def test_probe_nonexistent_file_is_graceful(tmp_path):
    result = probe_media(tmp_path / "does-not-exist.mp4")
    assert result is None


def test_probe_missing_binary_is_graceful(tmp_path):
    (tmp_path / "fake.mp4").write_bytes(b"not a real video")
    result = probe_media(tmp_path / "fake.mp4", ffprobe_path="/no/such/ffprobe-binary")
    assert result is None


def test_probe_non_media_file_is_graceful(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"this is definitely not a video file")
    result = probe_media(junk)
    assert result is None
