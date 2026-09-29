"""Shared ffmpeg/ffprobe test fixture helpers (Prompt 08).

Real-binary integration tests are skipped, not failed, when ffmpeg/ffprobe
are not installed — documented skip policy per Prompt 08 §32.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None

requires_ffmpeg = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed")
requires_ffprobe = pytest.mark.skipif(not FFPROBE_AVAILABLE, reason="ffprobe not installed")


def make_test_video(path: Path, *, duration: float = 2.0, size: str = "64x64", rate: int = 10) -> Path:
    """Deterministic tiny video fixture generated with ffmpeg's testsrc source."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={size}:rate={rate}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    return path
