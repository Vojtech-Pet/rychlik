"""Prompt A16: the compatibility planner is pure and deterministic --
these tests drive it with synthetic MediaDescriptor objects, no ffprobe
subprocess involved (that's covered separately in
test_device_media_probe.py)."""

from __future__ import annotations

from rychlik.device.media.capability_profile import (
    FRIENDSEND_GENERIC_AUDIO_V1,
    FRIENDSEND_GENERIC_VIDEO_V1,
    DeviceMediaCapabilities,
)
from rychlik.device.media.descriptor import AudioStreamInfo, MediaDescriptor, VideoStreamInfo
from rychlik.device.media.planner import DeviceCompatibilityPlanner, PlanFailureReason, PlanKind, PlanWarning

CAPS = DeviceMediaCapabilities(profiles=(FRIENDSEND_GENERIC_VIDEO_V1, FRIENDSEND_GENERIC_AUDIO_V1))
NO_CAPS = DeviceMediaCapabilities(profiles=())

planner = DeviceCompatibilityPlanner()


def _video(codec="h264", pix_fmt="yuv420p", w=1280, h=720, transfer=None):
    return VideoStreamInfo(codec=codec, width=w, height=h, pixel_format=pix_fmt, color_transfer=transfer)


def _audio(codec="aac"):
    return AudioStreamInfo(codec=codec)


def test_compatible_mp4_h264_aac_is_passthrough():
    descriptor = MediaDescriptor(
        media_kind="video", container="mov,mp4,m4a,3gp,3g2,mj2", video_stream=_video(), audio_stream=_audio()
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.PASSTHROUGH
    assert plan.target_profile.profile_id == "friendsend-generic-video-v1"


def test_mkv_h264_aac_is_remux_only():
    descriptor = MediaDescriptor(
        media_kind="video", container="matroska,webm", video_stream=_video(), audio_stream=_audio()
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.REMUX


def test_mp4_h264_opus_is_audio_only_transcode():
    descriptor = MediaDescriptor(
        media_kind="video",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        video_stream=_video(),
        audio_stream=_audio(codec="opus"),
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.TRANSCODE_AUDIO
    assert PlanWarning.AUDIO_REENCODED in plan.warnings
    assert PlanWarning.VIDEO_REENCODED not in plan.warnings


def test_mp4_mpeg4part2_aac_is_video_only_transcode():
    descriptor = MediaDescriptor(
        media_kind="video", container="mov,mp4,m4a,3gp,3g2,mj2", video_stream=_video(codec="mpeg4"), audio_stream=_audio()
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.TRANSCODE_VIDEO
    assert PlanWarning.VIDEO_REENCODED in plan.warnings
    assert PlanWarning.AUDIO_REENCODED not in plan.warnings


def test_webm_vp9_opus_is_full_transcode():
    descriptor = MediaDescriptor(
        media_kind="video", container="matroska,webm", video_stream=_video(codec="vp9"), audio_stream=_audio(codec="opus")
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.TRANSCODE_AUDIO_VIDEO
    assert PlanWarning.VIDEO_REENCODED in plan.warnings
    assert PlanWarning.AUDIO_REENCODED in plan.warnings


def test_hdr_requiring_conversion_is_unsupported_not_washed_out():
    descriptor = MediaDescriptor(
        media_kind="video",
        container="matroska,webm",
        video_stream=_video(codec="hevc", pix_fmt="yuv420p10le", transfer="smpte2084"),
        audio_stream=_audio(),
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.UNSUPPORTED
    assert plan.failure_reason == PlanFailureReason.HDR_TRANSCODE_UNSUPPORTED


def test_hdr_already_compatible_is_passthrough():
    descriptor = MediaDescriptor(
        media_kind="video",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        video_stream=_video(transfer="arib-std-b67"),
        audio_stream=_audio(),
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.PASSTHROUGH


def test_no_known_profile_never_claims_safe_conversion():
    descriptor = MediaDescriptor(
        media_kind="video", container="matroska,webm", video_stream=_video(codec="vp9"), audio_stream=_audio(codec="opus")
    )
    plan = planner.plan(descriptor, NO_CAPS)
    assert plan.kind == PlanKind.UNSUPPORTED
    assert plan.failure_reason == PlanFailureReason.NO_COMPATIBLE_PROFILE


def test_additional_streams_produce_warnings_only_when_output_is_new():
    descriptor = MediaDescriptor(
        media_kind="video",
        container="matroska,webm",
        video_stream=_video(),
        audio_stream=_audio(),
        additional_audio_streams=2,
        subtitle_streams=1,
        data_streams=1,
    )
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.REMUX
    assert PlanWarning.ADDITIONAL_AUDIO_DROPPED in plan.warnings
    assert PlanWarning.SUBTITLES_DROPPED in plan.warnings
    assert PlanWarning.DATA_STREAMS_DROPPED in plan.warnings


def test_audio_only_wav_pcm_is_transcoded_to_generic_audio_profile():
    descriptor = MediaDescriptor(media_kind="audio", container="wav", audio_stream=_audio(codec="pcm_s16le"))
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.TRANSCODE_AUDIO
    assert plan.target_profile.profile_id == "friendsend-generic-audio-v1"


def test_image_media_kind_is_unsupported_not_a_transcode_framework():
    descriptor = MediaDescriptor(media_kind="other", container="image2")
    plan = planner.plan(descriptor, CAPS)
    assert plan.kind == PlanKind.UNSUPPORTED
