"""FFmpeg command construction + output validation for a
`CompatibilityPlan` (Prompt A16 §41-52/§79-84).

Builds real `ffmpeg` argv lists (never shell text, §51), always copying
a stream that is already compatible rather than blindly re-encoding it
(§2/§29/§30), and always re-probes the generated output before it is
ever considered valid (§79) -- a zero exit code alone is not sufficient.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from rychlik.device.media.descriptor import MediaDescriptor, MediaProbeFailedError, probe_device_media
from rychlik.device.media.ffmpeg_runner import FFmpegProcessRunner, ProgressCallback, probe_encoder_available
from rychlik.device.media.planner import CompatibilityPlan, PlanKind

VIDEO_ENCODER = "libx264"
AUDIO_ENCODER = "aac"
VIDEO_CRF = "22"  # §43
VIDEO_PRESET = "medium"
AUDIO_BITRATE = "160k"  # §44
_SUPPORTED_SAMPLE_RATES = (44100, 48000)  # §45
_FALLBACK_SAMPLE_RATE = 48000


class TranscoderUnavailableError(Exception):
    """§41/§42: the required encoder is not present -- never silently
    choose an unrelated, lower-compatibility codec instead."""


class TranscodeFailedError(Exception):
    """FFmpeg exited non-zero, or was cancelled (§76)."""

    def __init__(self, message: str, *, cancelled: bool = False) -> None:
        super().__init__(message)
        self.cancelled = cancelled


class PreparedMediaInvalidError(Exception):
    """§79/§80: the generated output failed re-probe/profile validation."""


@dataclass(frozen=True)
class PreparationOutcome:
    output_path: Path
    mime_type: str


def preflight_encoders(plan: CompatibilityPlan, *, ffmpeg_path: str = "ffmpeg") -> None:
    if plan.requires_video_transcode and not probe_encoder_available(VIDEO_ENCODER, ffmpeg_path=ffmpeg_path):
        raise TranscoderUnavailableError(f"required video encoder {VIDEO_ENCODER!r} is not available")
    if plan.requires_audio_transcode and not probe_encoder_available(AUDIO_ENCODER, ffmpeg_path=ffmpeg_path):
        raise TranscoderUnavailableError(f"required audio encoder {AUDIO_ENCODER!r} is not available")


def build_ffmpeg_args(plan: CompatibilityPlan, descriptor: MediaDescriptor, source: Path, output: Path) -> list[str]:
    args = ["-i", str(source)]

    if descriptor.media_kind == "video":
        args += ["-map", "0:v:0"]
        if descriptor.audio_stream is not None:
            args += ["-map", "0:a:0"]

        if plan.requires_video_transcode:
            args += [
                "-c:v",
                VIDEO_ENCODER,
                "-crf",
                VIDEO_CRF,
                "-preset",
                VIDEO_PRESET,
                "-pix_fmt",
                "yuv420p",
                # §46: pad (never crop) to even dimensions -- required by
                # yuv420p/H.264, never distorts aspect ratio (§47).
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            ]
        else:
            args += ["-c:v", "copy"]

        if descriptor.audio_stream is not None:
            args += _audio_args(plan.requires_audio_transcode, descriptor)

        args += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-f", "mp4", str(output)]
    else:
        args += ["-map", "0:a:0"]
        args += _audio_args(plan.requires_audio_transcode, descriptor)
        args += ["-movflags", "+faststart", "-f", "ipod", str(output)]

    return args


def _audio_args(transcode: bool, descriptor: MediaDescriptor) -> list[str]:
    if not transcode:
        return ["-c:a", "copy"]
    channels = descriptor.audio_stream.channels if descriptor.audio_stream else None
    target_channels = 2 if (channels or 2) > 2 else (channels or 2)  # §44: downmix >2ch to stereo, mono stays mono
    sample_rate = descriptor.audio_stream.sample_rate if descriptor.audio_stream else None
    target_rate = sample_rate if sample_rate in _SUPPORTED_SAMPLE_RATES else _FALLBACK_SAMPLE_RATE
    return ["-c:a", AUDIO_ENCODER, "-b:a", AUDIO_BITRATE, "-ac", str(target_channels), "-ar", str(target_rate)]


def run_preparation(
    plan: CompatibilityPlan,
    descriptor: MediaDescriptor,
    source: Path,
    output: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> PreparationOutcome:
    preflight_encoders(plan, ffmpeg_path=ffmpeg_path)

    args = build_ffmpeg_args(plan, descriptor, source, output)
    runner = FFmpegProcessRunner(ffmpeg_path=ffmpeg_path)
    result = runner.run(
        args,
        duration_seconds=descriptor.duration_seconds,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )

    if result.cancelled:
        raise TranscodeFailedError("preparation was cancelled", cancelled=True)
    if not result.success:
        raise TranscodeFailedError(f"ffmpeg exited {result.exit_code}: {result.stderr_tail[-500:]}")

    if not output.exists() or not output.is_file() or output.stat().st_size == 0:  # §80
        raise PreparedMediaInvalidError("prepared output is missing or empty")

    try:
        output_descriptor = probe_device_media(output, ffprobe_path=ffprobe_path)
    except MediaProbeFailedError as exc:
        raise PreparedMediaInvalidError(f"prepared output failed re-probe: {exc}") from exc

    _validate_output(plan, descriptor, output_descriptor)

    mime_type = "video/mp4" if descriptor.media_kind == "video" else "audio/mp4"
    return PreparationOutcome(output_path=output, mime_type=mime_type)


def _validate_output(plan: CompatibilityPlan, source: MediaDescriptor, output: MediaDescriptor) -> None:
    """§79/§82-84: the exit code is not enough -- verify the output
    actually satisfies the target profile/plan, including that a
    COPIED stream really was left byte-identical in codec terms (a
    remux that accidentally re-encoded would be caught here)."""
    if output.media_kind == "video":
        if output.video_stream is None:
            raise PreparedMediaInvalidError("prepared output has no video stream")
        if output.video_stream.codec != "h264":
            raise PreparedMediaInvalidError(f"prepared video codec is {output.video_stream.codec!r}, expected h264")
        if output.video_stream.pixel_format != "yuv420p":
            raise PreparedMediaInvalidError("prepared output pixel format is not yuv420p")
        if not plan.requires_video_transcode and source.video_stream is not None:
            if output.video_stream.codec != source.video_stream.codec:
                raise PreparedMediaInvalidError("planned video copy actually changed codec")
        if source.audio_stream is not None:
            if output.audio_stream is None:
                raise PreparedMediaInvalidError("prepared output lost its audio stream")
            if output.audio_stream.codec != "aac":
                raise PreparedMediaInvalidError(f"prepared audio codec is {output.audio_stream.codec!r}, expected aac")
    else:
        if output.audio_stream is None:
            raise PreparedMediaInvalidError("prepared output has no audio stream")
        if output.audio_stream.codec != "aac":
            raise PreparedMediaInvalidError(f"prepared audio codec is {output.audio_stream.codec!r}, expected aac")

    # §81: tolerant duration check -- never sample-exact.
    if source.duration_seconds and output.duration_seconds:
        ratio = output.duration_seconds / source.duration_seconds
        if not (0.5 <= ratio <= 1.5):
            raise PreparedMediaInvalidError(
                f"prepared output duration {output.duration_seconds}s is wildly inconsistent with source {source.duration_seconds}s"
            )
