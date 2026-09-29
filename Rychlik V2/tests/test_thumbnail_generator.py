import pytest

from rychlik.share.thumbnail_generator import generate_thumbnail
from conftest_ffmpeg import make_test_video, requires_ffmpeg


@requires_ffmpeg
def test_generate_thumbnail_real_fixture(tmp_path):
    video_path = make_test_video(tmp_path / "fixture.mp4", duration=3.0, size="64x64")
    output_path = tmp_path / "preview.jpg"

    ok = generate_thumbnail(video_path, output_path=output_path, timestamp=0.5)

    assert ok is True
    assert output_path.is_file()
    assert output_path.stat().st_size > 0
    assert not output_path.with_name(output_path.name + ".tmp").exists()


@requires_ffmpeg
@pytest.mark.parametrize(
    "filename",
    [
        "video test.mp4",
        'video;"test".mp4',
        "české slovenské video.mp4",
        "-leadingdash.mp4",
        "." + ("x" * 200) + ".mp4",
    ],
)
def test_generate_thumbnail_weird_filenames_are_safe(tmp_path, filename):
    source_path = tmp_path / filename
    make_test_video(source_path, duration=1.0, size="32x32")
    output_path = tmp_path / "out.jpg"

    ok = generate_thumbnail(source_path, output_path=output_path, timestamp=0.1, timeout=15)

    assert ok is True
    assert output_path.stat().st_size > 0


def test_generate_thumbnail_missing_binary_is_graceful(tmp_path):
    source_path = tmp_path / "video.mp4"
    source_path.write_bytes(b"not a real video")
    output_path = tmp_path / "out.jpg"

    ok = generate_thumbnail(
        source_path, output_path=output_path, timestamp=0.0, ffmpeg_path="/no/such/ffmpeg-binary"
    )

    assert ok is False
    assert not output_path.exists()


@requires_ffmpeg
def test_generate_thumbnail_non_media_source_fails_gracefully(tmp_path):
    source_path = tmp_path / "junk.mp4"
    source_path.write_bytes(b"this is not a real video file at all")
    output_path = tmp_path / "out.jpg"

    ok = generate_thumbnail(source_path, output_path=output_path, timestamp=0.0, timeout=5)

    assert ok is False
    assert not output_path.exists()
    assert not output_path.with_name(output_path.name + ".tmp").exists()


def test_no_shell_execution_path_for_injection_attempt(tmp_path):
    # A filename that looks like a shell injection attempt must be treated as
    # a literal path (ffmpeg receives it as a single argv element, no shell
    # ever parses it) — expect a clean failure (no such file), not command
    # execution or a crash.
    marker = tmp_path / "should-not-exist"
    source_path = tmp_path / "video.mp4; touch should-not-exist"
    output_path = tmp_path / "out.jpg"

    ok = generate_thumbnail(source_path, output_path=output_path, timestamp=0.0, timeout=5)

    assert ok is False
    assert not marker.exists()
