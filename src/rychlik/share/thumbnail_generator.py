"""ffmpeg thumbnail extraction (Prompt 08). Subprocess-safe: argument arrays
only, no shell=True, bounded timeout. Writes to a `.tmp` path first and
atomically renames on success, mirroring the acquisition `.part` convention.
Failure is always graceful (returns False) — thumbnail extraction failing
must not make SharePreview generation impossible.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def generate_thumbnail(
    source_path: Path,
    *,
    output_path: Path,
    timestamp: float,
    max_width: int = 1280,
    max_height: int = 720,
    ffmpeg_path: str = "ffmpeg",
    timeout: int = 30,
) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")

    scale_filter = (
        f"scale='min({max_width},iw)':'min({max_height},ih)':force_original_aspect_ratio=decrease"
    )
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{max(timestamp, 0.0):.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-vf",
        scale_filter,
        "-q:v",
        "3",
        "-f",
        "mjpeg",  # explicit format: the .tmp suffix on tmp_path defeats extension sniffing
        str(tmp_path),
    ]

    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        tmp_path.unlink(missing_ok=True)
        return False

    if completed.returncode != 0 or not tmp_path.is_file() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        return False

    os.replace(tmp_path, output_path)
    return True
