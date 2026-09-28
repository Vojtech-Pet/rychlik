"""Pure, deterministic Device Mode compatibility planner (Prompt A16
§2/§25-32).

No subprocess execution here (§25) -- encoder-availability preflight
(§41/§42) is the service layer's job, not the planner's. Preparation
order always prefers PASSTHROUGH, then REMUX, then the smallest
necessary partial transcode, then a full transcode, then honest
rejection (§2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rychlik.device.media.capability_profile import DeviceMediaCapabilities, DeviceMediaProfile
from rychlik.device.media.descriptor import MediaDescriptor

_MP4_FAMILY_TOKENS = {"mp4", "mov", "m4a", "3gp", "3g2", "mj2", "ipod", "psp"}


def _is_mp4_family(container: str) -> bool:
    tokens = set(container.split(","))
    return bool(tokens & _MP4_FAMILY_TOKENS)


class PlanKind(Enum):
    PASSTHROUGH = "PASSTHROUGH"
    REMUX = "REMUX"
    TRANSCODE_AUDIO = "TRANSCODE_AUDIO"
    TRANSCODE_VIDEO = "TRANSCODE_VIDEO"
    TRANSCODE_AUDIO_VIDEO = "TRANSCODE_AUDIO_VIDEO"
    UNSUPPORTED = "UNSUPPORTED"


class PlanWarning(Enum):
    ADDITIONAL_VIDEO_DROPPED = "ADDITIONAL_VIDEO_DROPPED"
    ADDITIONAL_AUDIO_DROPPED = "ADDITIONAL_AUDIO_DROPPED"
    SUBTITLES_DROPPED = "SUBTITLES_DROPPED"
    DATA_STREAMS_DROPPED = "DATA_STREAMS_DROPPED"
    VIDEO_REENCODED = "VIDEO_REENCODED"
    AUDIO_REENCODED = "AUDIO_REENCODED"


class PlanFailureReason(Enum):
    NO_COMPATIBLE_PROFILE = "NO_COMPATIBLE_PROFILE"
    HDR_TRANSCODE_UNSUPPORTED = "HDR_TRANSCODE_UNSUPPORTED"


@dataclass(frozen=True)
class CompatibilityPlan:
    kind: PlanKind
    target_profile: DeviceMediaProfile | None = None
    warnings: tuple[PlanWarning, ...] = ()
    failure_reason: PlanFailureReason | None = None

    @property
    def requires_video_transcode(self) -> bool:
        return self.kind in (PlanKind.TRANSCODE_VIDEO, PlanKind.TRANSCODE_AUDIO_VIDEO)

    @property
    def requires_audio_transcode(self) -> bool:
        return self.kind in (PlanKind.TRANSCODE_AUDIO, PlanKind.TRANSCODE_AUDIO_VIDEO)

    @property
    def requires_new_output(self) -> bool:
        return self.kind not in (PlanKind.PASSTHROUGH, PlanKind.UNSUPPORTED)


class DeviceCompatibilityPlanner:
    def plan(self, descriptor: MediaDescriptor, capabilities: DeviceMediaCapabilities) -> CompatibilityPlan:
        if descriptor.media_kind == "video":
            return self._plan_video(descriptor, capabilities)
        if descriptor.media_kind == "audio":
            return self._plan_audio(descriptor, capabilities)
        # §19: images/other -- no general transcoding framework in A16.
        return CompatibilityPlan(kind=PlanKind.UNSUPPORTED, failure_reason=PlanFailureReason.NO_COMPATIBLE_PROFILE)

    def _plan_video(self, descriptor: MediaDescriptor, capabilities: DeviceMediaCapabilities) -> CompatibilityPlan:
        profile = capabilities.profile_for("video")
        if profile is None:
            return CompatibilityPlan(kind=PlanKind.UNSUPPORTED, failure_reason=PlanFailureReason.NO_COMPATIBLE_PROFILE)

        video = descriptor.video_stream
        audio = descriptor.audio_stream
        assert video is not None  # media_kind == "video" implies this

        if video.is_hdr:
            # §38-40: passthrough only if genuinely already fully
            # compatible; never a silent HDR->SDR transcode.
            if self._video_stream_compatible(video, profile) and self._container_compatible(descriptor.container, profile) and (
                audio is None or self._audio_stream_compatible(audio, profile)
            ):
                return CompatibilityPlan(kind=PlanKind.PASSTHROUGH, target_profile=profile)
            return CompatibilityPlan(
                kind=PlanKind.UNSUPPORTED, failure_reason=PlanFailureReason.HDR_TRANSCODE_UNSUPPORTED
            )

        container_ok = self._container_compatible(descriptor.container, profile)
        video_ok = self._video_stream_compatible(video, profile)
        audio_ok = audio is None or self._audio_stream_compatible(audio, profile)

        if container_ok and video_ok and audio_ok:
            return CompatibilityPlan(kind=PlanKind.PASSTHROUGH, target_profile=profile)

        warnings = self._extra_stream_warnings(descriptor)

        if video_ok and audio_ok:
            return CompatibilityPlan(kind=PlanKind.REMUX, target_profile=profile, warnings=warnings)
        if video_ok and not audio_ok:
            return CompatibilityPlan(
                kind=PlanKind.TRANSCODE_AUDIO,
                target_profile=profile,
                warnings=(*warnings, PlanWarning.AUDIO_REENCODED),
            )
        if not video_ok and audio_ok:
            return CompatibilityPlan(
                kind=PlanKind.TRANSCODE_VIDEO,
                target_profile=profile,
                warnings=(*warnings, PlanWarning.VIDEO_REENCODED),
            )
        return CompatibilityPlan(
            kind=PlanKind.TRANSCODE_AUDIO_VIDEO,
            target_profile=profile,
            warnings=(*warnings, PlanWarning.VIDEO_REENCODED, PlanWarning.AUDIO_REENCODED),
        )

    def _plan_audio(self, descriptor: MediaDescriptor, capabilities: DeviceMediaCapabilities) -> CompatibilityPlan:
        profile = capabilities.profile_for("audio")
        if profile is None:
            return CompatibilityPlan(kind=PlanKind.UNSUPPORTED, failure_reason=PlanFailureReason.NO_COMPATIBLE_PROFILE)

        audio = descriptor.audio_stream
        assert audio is not None

        container_ok = self._container_compatible(descriptor.container, profile)
        audio_ok = self._audio_stream_compatible(audio, profile)

        if container_ok and audio_ok:
            return CompatibilityPlan(kind=PlanKind.PASSTHROUGH, target_profile=profile)

        warnings = self._extra_stream_warnings(descriptor)
        if audio_ok:
            return CompatibilityPlan(kind=PlanKind.REMUX, target_profile=profile, warnings=warnings)
        return CompatibilityPlan(
            kind=PlanKind.TRANSCODE_AUDIO, target_profile=profile, warnings=(*warnings, PlanWarning.AUDIO_REENCODED)
        )

    @staticmethod
    def _container_compatible(container: str, profile: DeviceMediaProfile) -> bool:
        return _is_mp4_family(container)

    @staticmethod
    def _video_stream_compatible(video, profile: DeviceMediaProfile) -> bool:
        if video.codec not in profile.video_codecs:
            return False
        if profile.pixel_formats and video.pixel_format not in profile.pixel_formats:
            return False
        if profile.max_width and video.width > profile.max_width:
            return False
        if profile.max_height and video.height > profile.max_height:
            return False
        if profile.max_fps and video.frame_rate and video.frame_rate > profile.max_fps:
            return False
        return True

    @staticmethod
    def _audio_stream_compatible(audio, profile: DeviceMediaProfile) -> bool:
        if audio.codec not in profile.audio_codecs:
            return False
        if profile.max_audio_channels and audio.channels and audio.channels > profile.max_audio_channels:
            return False
        return True

    @staticmethod
    def _extra_stream_warnings(descriptor: MediaDescriptor) -> tuple[PlanWarning, ...]:
        warnings: list[PlanWarning] = []
        if descriptor.additional_video_streams > 0:
            warnings.append(PlanWarning.ADDITIONAL_VIDEO_DROPPED)
        if descriptor.additional_audio_streams > 0:
            warnings.append(PlanWarning.ADDITIONAL_AUDIO_DROPPED)
        if descriptor.subtitle_streams > 0:
            warnings.append(PlanWarning.SUBTITLES_DROPPED)
        if descriptor.data_streams > 0:
            warnings.append(PlanWarning.DATA_STREAMS_DROPPED)
        return tuple(warnings)
