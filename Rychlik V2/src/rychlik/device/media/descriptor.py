"""Real ffprobe-backed media description (Prompt A16 §10-14).

Mirrors the subprocess-safety style already established in
`rychlik.share.media_probe`/`thumbnail_generator` (argv list only, never
`shell=True`, bounded timeout, graceful typed failure) but returns a
materially richer, codec-level `MediaDescriptor` -- compatibility
decisions must never be made from MIME/file-extension alone (§11).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_TIMEOUT = 15


class MediaProbeFailedError(Exception):
    """A corrupt/unsupported/unreadable input -- never a raw ffprobe
    stderr dump, and never a reason to start ffmpeg blindly (§14)."""


@dataclass(frozen=True)
class VideoStreamInfo:
    codec: str
    width: int
    height: int
    profile: str | None = None
    pixel_format: str | None = None
    frame_rate: float | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    bit_depth: int | None = None

    @property
    def is_hdr(self) -> bool:
        """Common HDR transfer functions (§38) -- PQ (smpte2084) and
        HLG (arib-std-b67)."""
        return self.color_transfer in ("smpte2084", "arib-std-b67")


@dataclass(frozen=True)
class AudioStreamInfo:
    codec: str
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None


@dataclass(frozen=True)
class MediaDescriptor:
    media_kind: str  # "video" | "audio" | "other"
    container: str  # ffprobe format_name, e.g. "matroska,webm"
    duration_seconds: float | None = None

    video_stream: VideoStreamInfo | None = None
    audio_stream: AudioStreamInfo | None = None

    additional_video_streams: int = 0
    additional_audio_streams: int = 0
    subtitle_streams: int = 0
    data_streams: int = 0


def probe_device_media(
    path: Path, *, ffprobe_path: str = "ffprobe", timeout: int = _DEFAULT_TIMEOUT
) -> MediaDescriptor:
    """Raises `MediaProbeFailedError` for anything that isn't a readable,
    parseable media file -- never returns a partially-guessed descriptor
    (§13/§14)."""
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaProbeFailedError(f"ffprobe could not run: {exc}") from exc

    if completed.returncode != 0:
        raise MediaProbeFailedError(f"ffprobe exited {completed.returncode}")

    try:
        data = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MediaProbeFailedError(f"ffprobe produced unparsable JSON: {exc}") from exc

    streams = data.get("streams", [])
    if not isinstance(streams, list) or not streams:
        raise MediaProbeFailedError("no streams found")

    fmt = data.get("format", {})
    container = str(fmt.get("format_name", ""))
    duration = _to_float(fmt.get("duration"))

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    data_streams = [s for s in streams if s.get("codec_type") not in ("video", "audio", "subtitle")]

    video_info = _video_info(video_streams[0]) if video_streams else None
    audio_info = _audio_info(audio_streams[0]) if audio_streams else None

    if video_info is not None:
        media_kind = "video"
    elif audio_info is not None:
        media_kind = "audio"
    else:
        media_kind = "other"

    return MediaDescriptor(
        media_kind=media_kind,
        container=container,
        duration_seconds=duration,
        video_stream=video_info,
        audio_stream=audio_info,
        additional_video_streams=max(0, len(video_streams) - 1),
        additional_audio_streams=max(0, len(audio_streams) - 1),
        subtitle_streams=len(subtitle_streams),
        data_streams=len(data_streams),
    )


def _video_info(stream: dict) -> VideoStreamInfo | None:
    width, height = stream.get("width"), stream.get("height")
    codec = stream.get("codec_name")
    if not isinstance(width, int) or not isinstance(height, int) or not codec:
        return None
    frame_rate = _parse_rational(stream.get("r_frame_rate"))
    pix_fmt = stream.get("pix_fmt")
    bit_depth = None
    raw_bits = stream.get("bits_per_raw_sample")
    if isinstance(raw_bits, str) and raw_bits.isdigit():
        bit_depth = int(raw_bits)
    return VideoStreamInfo(
        codec=str(codec),
        width=width,
        height=height,
        profile=stream.get("profile"),
        pixel_format=pix_fmt,
        frame_rate=frame_rate,
        color_transfer=stream.get("color_transfer"),
        color_primaries=stream.get("color_primaries"),
        color_space=stream.get("color_space"),
        bit_depth=bit_depth,
    )


def _audio_info(stream: dict) -> AudioStreamInfo | None:
    codec = stream.get("codec_name")
    if not codec:
        return None
    sample_rate = stream.get("sample_rate")
    try:
        sample_rate = int(sample_rate) if sample_rate is not None else None
    except (TypeError, ValueError):
        sample_rate = None
    return AudioStreamInfo(
        codec=str(codec),
        sample_rate=sample_rate,
        channels=stream.get("channels"),
        channel_layout=stream.get("channel_layout"),
    )


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_rational(value) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return _to_float(value)
    num, _, den = value.partition("/")
    try:
        num_f, den_f = float(num), float(den)
        return num_f / den_f if den_f else None
    except ValueError:
        return None
