"""Prompt A16 §110-114: deterministic, tiny real media fixtures generated
with FFmpeg's synthetic `lavfi` sources at test time -- never committed
as binary files to the repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"fixture generation failed: {result.stderr.decode(errors='replace')}")


def make_compatible_mp4(path: Path, *, duration: float = 1.0, width: int = 320, height: int = 240) -> Path:
    """§111: MP4 / H.264 / AAC / yuv420p -- already Device-Mode-compatible."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:duration={duration}:rate=15",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ]
    )
    return path


def make_remux_mkv(path: Path, *, duration: float = 1.0, width: int = 320, height: int = 240) -> Path:
    """§112: MKV / H.264 / AAC -- codecs compatible, container is not."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:duration={duration}:rate=15",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ]
    )
    return path


def make_pcm_wav(path: Path, *, duration: float = 1.0) -> Path:
    """§113: WAV / PCM -- audio-only, needs transcode to the generic
    audio profile."""
    _run(["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", str(path)])
    return path


def make_mpeg4part2_video(path: Path, *, duration: float = 1.0, width: int = 320, height: int = 240) -> Path:
    """§114: MKV / MPEG-4 Part 2 video / AAC audio -- video codec outside
    the generic profile, audio already compatible (proves the
    copy+transcode split, §115) -- deliberately not HEVC, which may not
    have a reliable encoder in every environment."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:duration={duration}:rate=15",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(path),
        ]
    )
    return path


def make_full_transcode_source(path: Path, *, duration: float = 1.0, width: int = 320, height: int = 240) -> Path:
    """§31: WebM / VP9 / Opus -- both streams outside the generic
    profile."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:duration={duration}:rate=15",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "200k",
            "-c:a",
            "libopus",
            str(path),
        ]
    )
    return path


def make_opus_in_mp4(path: Path, *, duration: float = 1.0, width: int = 320, height: int = 240) -> Path:
    """§29: MP4 / H.264 / Opus -- video already compatible, only audio
    needs transcode."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:duration={duration}:rate=15",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "libopus",
            str(path),
        ]
    )
    return path


def make_corrupt_file(path: Path) -> Path:
    """§119: not a media file at all."""
    path.write_bytes(b"this is not a real media file, just garbage bytes" * 10)
    return path


def make_odd_dimension_mp4(path: Path, *, duration: float = 1.0) -> Path:
    """§46: odd width/height source, forces H.264/yuv420p padding.
    `color=` (unlike `testsrc2=`) actually preserves an odd requested
    size instead of silently rounding it."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:size=321x241:duration={duration}:rate=15",
            "-c:v",
            "mpeg4",  # incompatible codec so the planner requests a real transcode
            str(path),
        ]
    )
    return path
