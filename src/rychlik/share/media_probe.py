"""ffprobe wrapper (Prompt 08). Subprocess-safe: argument arrays only, no
shell=True, bounded timeout. Failure is always graceful (returns None) —
optional metadata probing must never make preview generation impossible.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProbeResult:
    duration: float | None
    width: int | None
    height: int | None


def probe_media(path: Path, *, ffprobe_path: str = "ffprobe", timeout: int = 15) -> ProbeResult | None:
    try:
        completed = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        return None

    try:
        data = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    return ProbeResult(
        duration=_extract_duration(data),
        **dict(zip(("width", "height"), _extract_dimensions(data))),
    )


def _extract_duration(data: dict) -> float | None:
    value = data.get("format", {}).get("duration")
    if value is None:
        for stream in data.get("streams", []):
            if stream.get("duration") is not None:
                value = stream["duration"]
                break
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_dimensions(data: dict) -> tuple[int | None, int | None]:
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width, height = stream.get("width"), stream.get("height")
            if isinstance(width, int) and isinstance(height, int):
                return width, height
    return None, None
